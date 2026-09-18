"""Live disease & health intelligence capability (§2-§12, §16, §19, §20, §24).

This module actually *fetches current public-health information at request time*
and turns it into structured, provenance-preserving findings. It never asks an
LLM "what is spreading" and never presents model training knowledge as current
outbreak status (§4, §26).

Design:
- ``DiseaseSurveillanceProvider`` / ``HealthNewsProvider`` are injectable
  protocols. Real adapters (:class:`FeedProvider`) read RSS/Atom/JSON feeds from
  authoritative sources over HTTP with strict timeouts (§24). Tests inject fakes.
- :class:`HealthIntelligence` orchestrates the pipeline:
  discover -> fetch -> parse -> normalize -> dedupe -> geo-relevance ->
  freshness -> conflict resolution -> alert classification (§4).
- A short-lived, configurable in-memory cache honours real retrieval timestamps
  and never caches indefinitely (§20).

Dependency discipline (§24): standard library + ``requests`` only. Feeds are
parsed with ``xml.etree`` and ``email.utils`` date parsing. No scraping
framework, no headless browser, no second LLM.
"""

from __future__ import annotations

import logging
import re
import time
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from typing import Any, Callable, Iterable, Protocol

from .config import MultimodalConfig
from .health_schemas import (
    AlertLevel,
    DataGapState,
    DiseaseFinding,
    FreshnessState,
    GeoScope,
    HealthIntelligenceResult,
    RelevanceToLocation,
    SourceRef,
    SourceTier,
    StatusClassification,
    TransmissionClass,
)
from .location import Location

logger = logging.getLogger("ragnosis.health")


# ---------------------------------------------------------------------------
# Raw feed item + provider protocols
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class FeedItem:
    """A normalized single item as returned by a provider (pre-analysis)."""

    organization: str
    tier: SourceTier
    title: str
    summary: str = ""
    uri: str | None = None
    published_at: str | None = None  # ISO 8601
    updated_at: str | None = None
    retrieved_at: str | None = None
    geo_scope: GeoScope = "unknown"
    # Optional structured hints a provider can supply if the source states them.
    # These are used verbatim and never guessed by the analyzer.
    stated_status: str | None = None
    stated_transmission: str | None = None
    stated_areas: list[str] = field(default_factory=list)
    stated_risk: str | None = None


class HealthDataProvider(Protocol):
    """Common interface for any source that yields :class:`FeedItem`."""

    name: str
    tier: SourceTier

    def fetch(self, locations: list[Location]) -> list[FeedItem]:
        """Return current items. May raise on network failure; the orchestrator
        catches per-provider failures so one dead source does not sink the run.
        """
        ...


# Distinct protocol names for clarity/typing (§2). Structurally identical.
class DiseaseSurveillanceProvider(HealthDataProvider, Protocol):
    ...


class HealthNewsProvider(HealthDataProvider, Protocol):
    ...


# ---------------------------------------------------------------------------
# Feed parsing helpers (pure, offline-testable)
# ---------------------------------------------------------------------------

_ATOM_NS = "{http://www.w3.org/2005/Atom}"


def _parse_date(value: str | None) -> str | None:
    """Parse an RSS/Atom date into a UTC ISO-8601 string, or None."""
    if not value or not value.strip():
        return None
    text = value.strip()
    # RFC 822 (RSS pubDate), e.g. "Tue, 16 Sep 2025 10:00:00 GMT".
    try:
        dt = parsedate_to_datetime(text)
        if dt is not None:
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            return dt.astimezone(timezone.utc).isoformat()
    except (TypeError, ValueError, IndexError):
        pass
    # ISO 8601 (Atom updated/published), tolerate trailing Z.
    iso = text.replace("Z", "+00:00")
    try:
        dt = datetime.fromisoformat(iso)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc).isoformat()
    except ValueError:
        return None


def _text(el: ET.Element | None) -> str:
    if el is None:
        return ""
    return (el.text or "").strip()


def parse_feed(
    xml_text: str,
    organization: str,
    tier: SourceTier,
    geo_scope: GeoScope,
    retrieved_at: str,
    max_items: int = 40,
) -> list[FeedItem]:
    """Parse an RSS 2.0 or Atom feed body into :class:`FeedItem` objects.

    Pure function, no network. Unknown/edge structures are tolerated: a partial
    item still becomes usable. Dates that cannot be parsed become ``None`` rather
    than a guessed value.
    """
    try:
        root = ET.fromstring(xml_text)
    except ET.ParseError:
        logger.warning("Failed to parse feed from %s", organization)
        return []

    items: list[FeedItem] = []

    # RSS 2.0: <rss><channel><item>...
    for item in root.findall(".//item"):
        title = _text(item.find("title"))
        summary = _text(item.find("description"))
        link = _text(item.find("link")) or None
        pub = _parse_date(_text(item.find("pubDate")) or None)
        items.append(
            FeedItem(
                organization=organization,
                tier=tier,
                title=title,
                summary=summary,
                uri=link,
                published_at=pub,
                updated_at=pub,
                retrieved_at=retrieved_at,
                geo_scope=geo_scope,
            )
        )
        if len(items) >= max_items:
            return items

    # Atom: <feed><entry>...
    for entry in root.findall(f"{_ATOM_NS}entry"):
        title = _text(entry.find(f"{_ATOM_NS}title"))
        summary = _text(entry.find(f"{_ATOM_NS}summary")) or _text(
            entry.find(f"{_ATOM_NS}content")
        )
        link_el = entry.find(f"{_ATOM_NS}link")
        link = link_el.get("href") if link_el is not None else None
        published = _parse_date(_text(entry.find(f"{_ATOM_NS}published")) or None)
        updated = _parse_date(_text(entry.find(f"{_ATOM_NS}updated")) or None)
        items.append(
            FeedItem(
                organization=organization,
                tier=tier,
                title=title,
                summary=summary,
                uri=link,
                published_at=published or updated,
                updated_at=updated or published,
                retrieved_at=retrieved_at,
                geo_scope=geo_scope,
            )
        )
        if len(items) >= max_items:
            break

    return items


# ---------------------------------------------------------------------------
# HTTP feed adapter (real source; opt-in via config)
# ---------------------------------------------------------------------------


@dataclass
class FeedSpec:
    """Configuration for one authoritative feed (§3)."""

    organization: str
    tier: SourceTier
    geo_scope: GeoScope
    url: str


def parse_feed_specs(raw: str) -> list[FeedSpec]:
    """Parse ``org|tier|scope|url`` lines (newline/semicolon separated).

    ``tier`` must be a valid :data:`SourceTier`; ``scope`` a valid
    :data:`GeoScope`. Malformed lines are skipped with a warning.
    """
    specs: list[FeedSpec] = []
    if not raw or not raw.strip():
        return specs
    chunks = re.split(r"[\n;]+", raw)
    valid_tiers = {"primary_official", "regional_official", "secondary"}
    valid_scopes = {"global", "national", "regional", "local", "unknown"}
    for chunk in chunks:
        chunk = chunk.strip()
        if not chunk or chunk.startswith("#"):
            continue
        parts = [p.strip() for p in chunk.split("|")]
        if len(parts) != 4:
            logger.warning("Skipping malformed feed spec: %r", chunk)
            continue
        org, tier, scope, url = parts
        if tier not in valid_tiers or scope not in valid_scopes or not url:
            logger.warning("Skipping invalid feed spec: %r", chunk)
            continue
        specs.append(FeedSpec(org, tier, scope, url))  # type: ignore[arg-type]
    return specs


# Default authoritative feeds (§3). These are well-known public RSS endpoints;
# they are only contacted when a FeedProvider is actually constructed AND used
# (never at import, never in offline tests). Endpoints are configurable and can
# be overridden entirely via HEALTH_SOURCE_FEEDS.
DEFAULT_HEALTH_FEEDS: tuple[FeedSpec, ...] = (
    FeedSpec(
        "WHO Disease Outbreak News",
        "primary_official",
        "global",
        "https://www.who.int/feeds/entity/csr/don/en/rss.xml",
    ),
    FeedSpec(
        "CDC Outbreaks",
        "primary_official",
        "national",
        "https://tools.cdc.gov/api/v2/resources/media/403372.rss",
    ),
    FeedSpec(
        "ECDC Threat Reports",
        "regional_official",
        "regional",
        "https://www.ecdc.europa.eu/en/taxonomy/term/2942/feed",
    ),
)


class FeedProvider:
    """Live adapter that fetches and parses an authoritative feed over HTTP.

    Respects a strict timeout and sends a descriptive User-Agent (§24). Network
    errors propagate to the orchestrator, which records the source as failed
    without fabricating data (§19).
    """

    def __init__(
        self,
        spec: FeedSpec,
        config: MultimodalConfig,
        session: Any | None = None,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self.spec = spec
        self.config = config
        self._session = session
        self._clock = clock or (lambda: datetime.now(timezone.utc))

    @property
    def name(self) -> str:
        return self.spec.organization

    @property
    def tier(self) -> SourceTier:
        return self.spec.tier

    def _get_session(self):
        if self._session is None:
            import requests  # local import keeps dependency optional at import

            self._session = requests.Session()
        return self._session

    def fetch(self, locations: list[Location]) -> list[FeedItem]:
        retrieved_at = self._clock().astimezone(timezone.utc).isoformat()
        session = self._get_session()
        resp = session.get(
            self.spec.url,
            timeout=self.config.health_timeout,
            headers={"User-Agent": self.config.health_user_agent},
        )
        resp.raise_for_status()
        return parse_feed(
            resp.text,
            organization=self.spec.organization,
            tier=self.spec.tier,
            geo_scope=self.spec.geo_scope,
            retrieved_at=retrieved_at,
            max_items=self.config.health_max_items_per_source,
        )


def build_default_providers(config: MultimodalConfig) -> list[FeedProvider]:
    """Construct FeedProviders from config feeds, or the built-in defaults.

    No network happens here — providers only reach out when ``fetch`` is called.
    """
    specs = parse_feed_specs(config.health_source_feeds) or list(DEFAULT_HEALTH_FEEDS)
    return [FeedProvider(spec, config) for spec in specs]


# ---------------------------------------------------------------------------
# Analysis vocabulary maps (source-term -> normalized enum)
# ---------------------------------------------------------------------------

# §6 — map a source's own status wording to a normalized bucket. We only map
# terms a source explicitly uses; anything else stays "unknown".
_STATUS_TERMS: tuple[tuple[str, StatusClassification], ...] = (
    ("pandemic", "pandemic"),
    ("epidemic", "epidemic"),
    ("outbreak", "outbreak"),
    ("endemic", "endemic"),
    ("cluster", "cluster"),
    ("sporadic", "sporadic"),
    ("isolated case", "sporadic"),
)

# §7 — transmission keywords a source may state. Never inferred from symptoms.
_TRANSMISSION_TERMS: tuple[tuple[str, TransmissionClass], ...] = (
    ("person-to-person", "contagious_person_to_person"),
    ("person to person", "contagious_person_to_person"),
    ("human-to-human", "contagious_person_to_person"),
    ("respiratory droplet", "contagious_person_to_person"),
    ("airborne", "contagious_person_to_person"),
    ("mosquito", "vector_borne"),
    ("mosquito-borne", "vector_borne"),
    ("vector-borne", "vector_borne"),
    ("vector borne", "vector_borne"),
    ("tick-borne", "vector_borne"),
    ("waterborne", "food_or_water_borne"),
    ("water-borne", "food_or_water_borne"),
    ("foodborne", "food_or_water_borne"),
    ("food-borne", "food_or_water_borne"),
    ("contaminated water", "food_or_water_borne"),
    ("zoonotic", "zoonotic"),
    ("animal-to-human", "zoonotic"),
    ("spillover", "zoonotic"),
    ("environmental exposure", "environmental"),
    ("not spread from person to person", "not_person_to_person"),
    ("does not spread between people", "not_person_to_person"),
)

# §16 — alert keywords -> alert level. Only from explicit source wording.
_ALERT_TERMS: tuple[tuple[str, AlertLevel], ...] = (
    ("public health emergency of international concern", "official_alert"),
    ("pheic", "official_alert"),
    ("official alert", "official_alert"),
    ("emergency declared", "official_alert"),
    ("health emergency", "official_alert"),
    ("elevated risk", "elevated"),
    ("increased risk", "elevated"),
    ("high alert", "elevated"),
    ("watch", "watch"),
    ("advisory", "watch"),
    ("monitoring", "watch"),
)


def normalize_status(term: str | None, text: str) -> tuple[StatusClassification, str | None]:
    """Return (normalized_status, source_term) preferring the source's wording."""
    hay = f"{term or ''} {text}".lower()
    for needle, status in _STATUS_TERMS:
        if needle in hay:
            # Preserve the source's own term where possible.
            return status, (term.strip() if term and term.strip() else needle)
    return "unknown", (term.strip() if term and term.strip() else None)


def normalize_transmission(term: str | None, text: str) -> TransmissionClass:
    hay = f"{term or ''} {text}".lower()
    # Check negative statement first so "not spread person to person" wins.
    for needle, cls in _TRANSMISSION_TERMS:
        if cls == "not_person_to_person" and needle in hay:
            return cls
    for needle, cls in _TRANSMISSION_TERMS:
        if needle in hay:
            return cls
    return "unknown"


def classify_alert(text: str, stated_risk: str | None = None) -> tuple[AlertLevel, str | None]:
    hay = f"{stated_risk or ''} {text}".lower()
    for needle, level in _ALERT_TERMS:
        if needle in hay:
            return level, needle
    return "none", None


# ---------------------------------------------------------------------------
# Freshness (§11)
# ---------------------------------------------------------------------------


def compute_freshness(
    published_at: str | None,
    updated_at: str | None,
    now: datetime,
    current_days: int,
    recent_days: int,
) -> FreshnessState:
    """Classify freshness relative to configured thresholds (§11).

    ``updated_at`` takes precedence over ``published_at``. When no date is known
    the state is ``unknown`` — never guessed as "current".
    """
    stamp = updated_at or published_at
    if not stamp:
        return "unknown"
    try:
        dt = datetime.fromisoformat(stamp.replace("Z", "+00:00"))
    except ValueError:
        return "unknown"
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    age_days = (now - dt.astimezone(timezone.utc)).total_seconds() / 86400.0
    if age_days < 0:
        age_days = 0.0
    if age_days <= current_days:
        return "current"
    if age_days <= recent_days:
        return "recent"
    return "stale"


# ---------------------------------------------------------------------------
# Disease name discovery (§4) — from source text, not from an LLM
# ---------------------------------------------------------------------------

# A transparent lexicon of disease names to recognise in authoritative feed text.
# This is used only to *extract which disease a source is reporting on*; it never
# supplies status, risk, or transmission — those come from the source wording.
DISEASE_LEXICON: tuple[str, ...] = (
    "cholera",
    "dengue",
    "chikungunya",
    "zika",
    "malaria",
    "measles",
    "mpox",
    "monkeypox",
    "ebola",
    "marburg",
    "lassa fever",
    "influenza",
    "avian influenza",
    "h5n1",
    "covid-19",
    "sars-cov-2",
    "nipah",
    "polio",
    "poliovirus",
    "diphtheria",
    "yellow fever",
    "meningitis",
    "leptospirosis",
    "hepatitis",
    "typhoid",
    "tuberculosis",
    "plague",
    "rabies",
    "japanese encephalitis",
    "acute encephalitis syndrome",
    "west nile",
    "rift valley fever",
    "hand foot and mouth disease",
    "scrub typhus",
)


def extract_diseases(text: str) -> list[str]:
    """Return disease names mentioned in ``text`` (deduped, source order)."""
    hay = text.lower()
    found: list[str] = []
    for name in DISEASE_LEXICON:
        if name in hay and name not in found:
            found.append(name)
    return found


# ---------------------------------------------------------------------------
# Geo relevance (§8)
# ---------------------------------------------------------------------------


def assess_relevance(
    item: FeedItem, location: Location
) -> tuple[RelevanceToLocation, str | None, list[str]]:
    """Assess an item's relevance to a location and extract affected areas.

    Returns (relevance, reason, affected_areas). We never present a global
    headline as local (§8): a global-scope item without an explicit location
    match is ``global_context``.
    """
    text = f"{item.title} {item.summary}".lower()
    affected: list[str] = list(item.stated_areas)

    # Collect candidate place tokens from the location hierarchy.
    tokens = [
        t.lower()
        for t in (location.city, location.state_or_region, location.country, location.normalized)
        if t
    ]
    matched = [t for t in tokens if t and t in text]

    # Record affected areas the source explicitly names from our hierarchy.
    for t in (location.city, location.state_or_region, location.country):
        if t and t.lower() in text and t not in affected:
            affected.append(t)

    if matched:
        # Direct if the most specific supplied token matched; otherwise regional.
        finest = None
        for t in (location.city, location.state_or_region, location.country):
            if t:
                finest = t.lower()
                break
        if finest and finest in text:
            return "direct", f"source text mentions '{finest}'", affected
        return "regional", "source mentions a broader area covering this location", affected

    if item.geo_scope == "global":
        return "global_context", "global-scope source with no explicit local mention", affected
    if item.geo_scope in ("national", "regional"):
        # National/regional source that doesn't name the user's place: regional
        # context if the country matches, else global context.
        if location.country and location.country.lower() in text:
            return "regional", "national/regional source covering this country", affected
        return "global_context", "broader-scope source without local specificity", affected
    return "unknown", "insufficient location specificity to assess relevance", affected


# ---------------------------------------------------------------------------
# The capability
# ---------------------------------------------------------------------------


def _now_utc() -> datetime:
    return datetime.now(timezone.utc)


@dataclass
class _CacheEntry:
    stored_at: float
    items: list[FeedItem]


class HealthIntelligence:
    """Orchestrates live health-intelligence lookups (§4).

    Providers are injected (real :class:`FeedProvider` in production, fakes in
    tests). The pipeline is deterministic given a fixed set of provider outputs
    and clock, which is what makes the offline tests meaningful.
    """

    def __init__(
        self,
        config: MultimodalConfig | None = None,
        providers: Iterable[HealthDataProvider] | None = None,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self.config = config or MultimodalConfig.from_env()
        self._providers = list(providers) if providers is not None else None
        self._clock = clock or _now_utc
        self._cache: dict[str, _CacheEntry] = {}

    @property
    def providers(self) -> list[HealthDataProvider]:
        if self._providers is None:
            self._providers = list(build_default_providers(self.config))
        return self._providers

    def configured(self) -> bool:
        """True when at least one provider is available."""
        return bool(self.providers)

    # -- caching (§20) ------------------------------------------------------
    def _cache_key(self, provider: HealthDataProvider, locations: list[Location]) -> str:
        loc = "|".join(sorted(l.normalized.lower() for l in locations)) or "_none_"
        return f"{provider.name}::{loc}"

    def _cached_fetch(
        self, provider: HealthDataProvider, locations: list[Location]
    ) -> tuple[list[FeedItem], bool]:
        ttl = self.config.health_cache_ttl
        key = self._cache_key(provider, locations)
        now = time.monotonic()
        if ttl > 0:
            entry = self._cache.get(key)
            if entry is not None and (now - entry.stored_at) <= ttl:
                return entry.items, True
        items = provider.fetch(locations)
        if ttl > 0:
            self._cache[key] = _CacheEntry(stored_at=now, items=items)
        return items, False

    # -- main entry ---------------------------------------------------------
    def gather(self, locations: list[Location]) -> HealthIntelligenceResult:
        """Fetch, analyze, and structure current findings for the locations."""
        now = self._clock().astimezone(timezone.utc)
        retrieved_at = now.isoformat()
        attempted: list[str] = []
        succeeded: list[str] = []
        failed: list[str] = []
        cache_hits: list[str] = []
        notes: list[str] = []
        all_items: list[FeedItem] = []
        any_from_cache = False

        providers = self.providers
        if not providers:
            return HealthIntelligenceResult(
                live_data_status="unavailable",
                retrieved_at=retrieved_at,
                requested_locations=[l.raw for l in locations],
                notes=["No health-intelligence providers are configured."],
            )

        for provider in providers:
            attempted.append(provider.name)
            try:
                items, from_cache = self._cached_fetch(provider, locations)
                succeeded.append(provider.name)
                if from_cache:
                    cache_hits.append(provider.name)
                    any_from_cache = True
                all_items.extend(items)
            except Exception as exc:  # noqa: BLE001 - one dead source must not sink all
                logger.warning("Health source failed (%s): %s", provider.name, exc)
                failed.append(provider.name)

        # §19: distinguish all-unavailable from reachable-but-nothing.
        if attempted and not succeeded:
            return HealthIntelligenceResult(
                live_data_status="unavailable",
                retrieved_at=retrieved_at,
                requested_locations=[l.raw for l in locations],
                sources_attempted=attempted,
                sources_failed=failed,
                notes=[
                    "All configured health sources were unreachable. Current "
                    "outbreak status is unavailable; model knowledge is NOT used "
                    "as a substitute for live surveillance."
                ],
            )

        deduped = self._dedupe(all_items)

        findings = self._analyze(deduped, locations, now)

        # §19 status resolution.
        if not findings:
            status = "no_relevant_current_data"
            notes.append(
                "Configured sources were reached but reported no current findings "
                "relevant to the requested location(s). Absence of a report is not "
                "proof that no outbreak exists."
            )
        elif failed:
            status = "partial"
            notes.append(
                f"Partial results: {len(failed)} source(s) failed and were omitted."
            )
        else:
            status = "ok"

        return HealthIntelligenceResult(
            live_data_status=status,
            retrieved_at=retrieved_at,
            requested_locations=[l.raw for l in locations],
            findings=findings,
            sources_attempted=attempted,
            sources_succeeded=succeeded,
            sources_failed=failed,
            cache_hits=cache_hits,
            notes=notes,
            from_cache=any_from_cache,
        )

    # -- pipeline stages ----------------------------------------------------
    def _dedupe(self, items: list[FeedItem]) -> list[FeedItem]:
        """Deduplicate near-identical items across sources (§4)."""
        seen: dict[str, FeedItem] = {}
        ordered: list[FeedItem] = []
        for item in items:
            key = (item.uri or "").strip().lower() or _norm_title(item.title)
            if key in seen:
                continue
            seen[key] = item
            ordered.append(item)
        return ordered

    def _analyze(
        self, items: list[FeedItem], locations: list[Location], now: datetime
    ) -> list[DiseaseFinding]:
        """Turn deduped items into per-(disease, location) findings.

        For each location we build findings independently (§1: multiple locations
        evaluated separately). Findings for the same disease from multiple
        sources are merged with conflict detection (§10) and primary-official
        precedence (§3).
        """
        findings: list[DiseaseFinding] = []
        # When no location is supplied we still summarize global context.
        loc_iter: list[Location | None] = list(locations) if locations else [None]

        for location in loc_iter:
            # disease -> list of (item, analysis) contributing to it
            grouped: dict[str, list[tuple[FeedItem, dict[str, Any]]]] = {}
            for item in items:
                text = f"{item.title} {item.summary}"
                diseases = extract_diseases(text)
                if item.stated_status and not diseases:
                    # Source stated a status but no lexicon disease matched; still
                    # record under a generic name from the title.
                    diseases = [_short_disease_from_title(item.title)]
                for disease in diseases:
                    analysis = self._analyze_item_for(item, disease, location, now)
                    grouped.setdefault(disease, []).append((item, analysis))

            for disease, contributions in grouped.items():
                finding = self._merge_contributions(disease, contributions, location, now)
                if finding is not None:
                    findings.append(finding)

        return findings

    def _analyze_item_for(
        self, item: FeedItem, disease: str, location: Location | None, now: datetime
    ) -> dict[str, Any]:
        text = f"{item.title} {item.summary}"
        status, source_term = normalize_status(item.stated_status, text)
        transmission = normalize_transmission(item.stated_transmission, text)
        alert_level, alert_reason = classify_alert(text, item.stated_risk)
        freshness = compute_freshness(
            item.published_at,
            item.updated_at,
            now,
            self.config.health_current_days,
            self.config.health_recent_days,
        )
        if location is not None:
            relevance, reason, areas = assess_relevance(item, location)
        else:
            relevance, reason, areas = "global_context", "no location supplied", list(item.stated_areas)
        return {
            "status": status,
            "status_source_term": source_term,
            "transmission": transmission,
            "alert_level": alert_level,
            "alert_reason": alert_reason,
            "freshness": freshness,
            "relevance": relevance,
            "relevance_reason": reason,
            "affected_areas": areas,
        }

    def _merge_contributions(
        self,
        disease: str,
        contributions: list[tuple[FeedItem, dict[str, Any]]],
        location: Location | None,
        now: datetime,
    ) -> DiseaseFinding | None:
        # Precedence: primary_official > regional_official > secondary; then most
        # recent. This selects the "lead" item whose status/risk we trust (§3,§10).
        tier_rank = {"primary_official": 0, "regional_official": 1, "secondary": 2}

        def sort_key(entry: tuple[FeedItem, dict[str, Any]]):
            item, _ = entry
            stamp = item.updated_at or item.published_at or ""
            return (tier_rank.get(item.tier, 3), _neg_iso(stamp))

        ordered = sorted(contributions, key=sort_key)
        lead_item, lead = ordered[0]

        # Conflict detection (§10): differing *known* statuses across sources.
        statuses = {
            a["status"]
            for _, a in ordered
            if a["status"] not in ("unknown",)
        }
        conflict_summary = None
        status = lead["status"]
        status_source_term = lead["status_source_term"]
        if len(statuses) > 1:
            # Preserve exact wording + scope of each conflicting source.
            parts = []
            for it, a in ordered:
                if a["status"] in ("unknown",):
                    continue
                parts.append(
                    f"{it.organization} ({it.tier}, {it.geo_scope}): "
                    f"{a['status_source_term'] or a['status']}"
                    + (f" [{it.updated_at or it.published_at}]" if (it.updated_at or it.published_at) else "")
                )
            conflict_summary = (
                "Sources report differing status; primary/most-recent used as lead. "
                + " | ".join(parts)
            )
            status = "conflicting"

        # Build source refs from all contributors (provenance, §4/§12).
        sources = [
            SourceRef(
                organization=it.organization,
                tier=it.tier,
                title=it.title,
                uri=it.uri,
                published_at=it.published_at,
                updated_at=it.updated_at,
                retrieved_at=it.retrieved_at,
                geo_scope=it.geo_scope,
                excerpt=(it.summary or "")[:400],
            )
            for it, _ in ordered
        ]

        # Merge affected areas across contributors.
        affected: list[str] = []
        for _, a in ordered:
            for area in a["affected_areas"]:
                if area not in affected:
                    affected.append(area)

        # Relevance: prefer the strongest (direct > regional > imported > global).
        relevance = _best_relevance(a["relevance"] for _, a in ordered)
        relevance_reason = next(
            (a["relevance_reason"] for _, a in ordered if a["relevance"] == relevance),
            None,
        )

        # Alert: highest explicit alert level among contributors (§16).
        alert_level, alert_source, alert_reason = _best_alert(ordered)

        # Transmission: first non-unknown value; never inferred.
        transmission = next(
            (a["transmission"] for _, a in ordered if a["transmission"] != "unknown"),
            "unknown",
        )
        transmissible = _transmissible_flag(transmission)

        # Data-gap state (§9): only secondary sources, or nothing but unknown
        # statuses, signals a gap. Never implies concealment.
        data_gap: DataGapState | None = None
        uncertainty: str | None = None
        tiers = {it.tier for it, _ in ordered}
        if tiers == {"secondary"}:
            data_gap = "low_media_coverage"
            uncertainty = (
                "Only secondary (media) reporting was found; no primary official "
                "surveillance source corroborated this. Treat as unconfirmed."
            )
        elif not statuses:
            data_gap = "limited_surveillance_data"
            uncertainty = (
                "Sources mention this disease but state no explicit outbreak "
                "status; current status could not be determined from the evidence."
            )

        status_display: StatusClassification = status
        if status == "unknown" and not statuses:
            status_display = "no_current_outbreak_status_found"

        freshness = _best_freshness(a["freshness"] for _, a in ordered)

        return DiseaseFinding(
            disease_name=disease,
            requested_location=(location.raw if location else "unspecified"),
            normalized_location=(location.normalized if location else None),
            transmissible=transmissible,
            transmission_class=transmission,
            transmission_note=None,
            status=status_display,
            status_source_term=status_source_term,
            classification_source=lead_item.organization,
            classification_date=(lead_item.updated_at or lead_item.published_at),
            geo_scope=lead_item.geo_scope,
            affected_areas=affected,
            relevance_to_location=relevance,
            relevance_reason=relevance_reason,
            risk_assessment=(lead_item.stated_risk or "risk assessment unavailable"),
            severity=None,  # NEVER manufactured (§5)
            alert_level=alert_level,
            alert_source=alert_source,
            alert_reason=alert_reason,
            data_gap_state=data_gap,
            uncertainty=uncertainty,
            published_at=lead_item.published_at,
            updated_at=lead_item.updated_at,
            retrieved_at=lead_item.retrieved_at,
            freshness_state=freshness,
            conflict_summary=conflict_summary,
            sources=sources,
        )


# ---------------------------------------------------------------------------
# small helpers
# ---------------------------------------------------------------------------

_WS_RE = re.compile(r"\s+")


def _norm_title(title: str) -> str:
    return _WS_RE.sub(" ", (title or "").strip().lower())


def _short_disease_from_title(title: str) -> str:
    words = _norm_title(title).split()
    return " ".join(words[:4]) if words else "unspecified"


def _neg_iso(stamp: str) -> str:
    # Sort most-recent first: invert by returning a key that sorts descending.
    # Empty stamps sort last. We do this by returning a tuple-friendly negation
    # via a large-constant complement on the ISO string comparison.
    if not stamp:
        return "\x00"  # sorts before any real timestamp -> pushed last when reversed
    # We want ascending sort_key with most-recent first, so invert lexically.
    return "".join(chr(0x10FFFF - ord(c)) if ord(c) < 0x10FFFF else c for c in stamp)


_RELEVANCE_RANK = {
    "direct": 0,
    "regional": 1,
    "imported_risk": 2,
    "global_context": 3,
    "unknown": 4,
}


def _best_relevance(values: Iterable[RelevanceToLocation]) -> RelevanceToLocation:
    best: RelevanceToLocation = "unknown"
    best_rank = 99
    for v in values:
        r = _RELEVANCE_RANK.get(v, 99)
        if r < best_rank:
            best_rank = r
            best = v
    return best


_ALERT_RANK = {"none": 0, "watch": 1, "elevated": 2, "official_alert": 3}


def _best_alert(
    ordered: list[tuple[FeedItem, dict[str, Any]]]
) -> tuple[AlertLevel, str | None, str | None]:
    best: AlertLevel = "none"
    best_rank = 0
    src = None
    reason = None
    for it, a in ordered:
        r = _ALERT_RANK.get(a["alert_level"], 0)
        if r > best_rank:
            best_rank = r
            best = a["alert_level"]
            src = it.organization
            reason = a["alert_reason"]
    return best, src, reason


_FRESH_RANK = {"current": 0, "recent": 1, "stale": 2, "unknown": 3}


def _best_freshness(values: Iterable[FreshnessState]) -> FreshnessState:
    best: FreshnessState = "unknown"
    best_rank = 99
    for v in values:
        r = _FRESH_RANK.get(v, 99)
        if r < best_rank:
            best_rank = r
            best = v
    return best


def _transmissible_flag(transmission: TransmissionClass) -> bool | None:
    if transmission == "unknown":
        return None
    if transmission == "not_person_to_person":
        return False
    if transmission == "contagious_person_to_person":
        return True
    # Vector/food/water/zoonotic/environmental are transmissible but not
    # person-to-person; we report transmissible=True with the class carrying the
    # nuance, and never claim person-to-person contagion.
    return True
