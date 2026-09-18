# RAGnosis Agent & Live Health Intelligence

## What RAGnosis is

> RAGnosis is an evidence grounded biomedical research agent that combines graph
> knowledge, biomedical literature, multimodal observations, and current public
> health information while explicitly preserving source provenance and
> uncertainty.

RAGnosis is **not** a diagnostic system, a diagnostic device, or a clinically
validated tool. It does not diagnose a user, predict whether a user is or will be
infected, infer personal medical status from symptoms or an image, or authorize
treatment.

## Where the agent fits

The agent (`multimodal/agent.py`) is a thin orchestration layer that **composes**
the existing multimodal pipeline with a new live-health-intelligence capability.
It does not replace or fork the existing flow — vision, PubMed retrieval,
generation, and the deterministic safety layer are reused unchanged; live health
intelligence is simply **one more tool** the agent can invoke.

```text
user request (question + optional image + optional explicit location)
        |
        v
  intent + capability routing        (multimodal/routing.py, deterministic, no LLM)
        |
        v
  explicit location resolution       (multimodal/location.py; ASK if missing + geographic)
        |
        +--> vision observations      (multimodal/vision.py)          [if image]
        +--> biomedical literature    (multimodal/retrieval.py)       [if text question]
        +--> live health intelligence (multimodal/health_intelligence.py) [if live intent]
        |
        v
  evidence fusion (provenance kinds retained)   (multimodal/agent.py)
        |
        v
  grounded generation                (multimodal/service.py generator)
        |
        v
  deterministic safety enforcement   (multimodal/safety.py)
        |
        v
  structured answer + provenance + timestamps + execution trace
```

## Routing (deterministic, no second LLM)

`multimodal/routing.py` classifies each request with transparent keyword/pattern
rules — never another LLM (a second model for routing was explicitly avoided).

- **Static / definitional** questions ("what causes dengue?", "symptoms of
  measles") route to **literature only**. They never trigger a live lookup.
- **Current-activity** questions ("is there a dengue outbreak right now?",
  "what's spreading in my area?", "is measles contagious currently?", "health
  alert", "disease trend", "regional threat") additionally invoke **live health
  intelligence**, and are treated as inherently **geographic**.
- An **image** adds the **vision** capability. Combined requests compose vision +
  literature + live health as needed.

The route, the tools used, and the human-readable reasons are surfaced in the
execution trace (below). The router decides *whether to go and look* — it never
decides *what is spreading*.

## Location handling (explicit only)

`multimodal/location.py` enforces the core policy: **location is only ever what
the user explicitly supplies.** It is never inferred from IP address, browser
locale, system settings, account data, or model knowledge.

- The user's original text is always preserved (`raw`) alongside a conservative
  normalization (`normalized`).
- Granularity is kept distinguishable — `city`, `state_or_region`, `country`, or
  `unknown` — and is never upgraded. A national/regional source is never
  presented as if it were city-level surveillance.
- Multiple locations ("Hyderabad and Mumbai", "Telangana; Kerala") are split and
  **evaluated separately**.
- If the question is geographic but **no** location was supplied, the agent
  **asks first** (`needs_location=True` with a clear prompt) instead of guessing.

## Live health intelligence pipeline

`multimodal/health_intelligence.py` actually fetches current public-health
information at request time. It never asks an LLM "what is spreading", and model
training knowledge is never presented as the current situation.

Pipeline: **discover → fetch → parse → normalize → dedupe → geo-relevance →
freshness → conflict resolution → alert classification.**

- **Providers are injectable.** `DiseaseSurveillanceProvider` / `HealthNewsProvider`
  are protocols. The real adapter, `FeedProvider`, reads RSS/Atom feeds over HTTP
  with a strict timeout and a descriptive User-Agent. Tests inject fakes, so the
  entire capability runs offline.
- **Authoritative source priority (tiers).** `primary_official` (WHO / WHO DON,
  CDC, national agencies) and `regional_official` (ECDC, PAHO, state departments)
  take precedence over `secondary` (media) for status and risk. Secondary
  reporting is allowed but always labeled secondary.
- **Source metadata retained per item:** organization, tier, title, URI,
  published/updated dates, per-item and overall `retrieved_at`, and geo scope.

### Per-disease finding fields

Each `DiseaseFinding` reports (using the source's own wording where possible):

- disease name; whether transmissible and the transmission class
  (`contagious_person_to_person`, `not_person_to_person`, `vector_borne`,
  `food_or_water_borne`, `zoonotic`, `environmental`, `unknown`) — **never
  inferred** from an image, a symptom, or the mere presence of a headline;
- status classification (`outbreak`, `epidemic`, `pandemic`, `endemic`,
  `cluster`, `sporadic`, `no_current_outbreak_status_found`, `conflicting`,
  `unknown`) plus the source's own term, classification source, and date;
- geographic scope (`global` / `national` / `regional` / `local` / `unknown`),
  never collapsed into another scope;
- affected areas and relevance to the requested location (`direct`, `regional`,
  `imported_risk`, `global_context`, `unknown`) — a global headline is never
  presented as local;
- official risk if stated, otherwise the literal `"risk assessment unavailable"`;
  severity only if authoritative. **Risk and severity are never manufactured.**
- alert level (`none` / `watch` / `elevated` / `official_alert`) derived only
  from explicit evidence, with the source and reason retained;
- uncertainty / data-gap state (`low_media_coverage`, `limited_surveillance_data`,
  `reporting_delay`, `incomplete_reporting`, `conflicting_reports`,
  `insufficient_local_data`, `uncertain`) — these describe the **evidence**, and
  **never** accuse a source of concealment or downplaying;
- freshness (`published_at`, `updated_at`, `retrieved_at`, and a
  `freshness_state` of `current` / `recent` / `stale` / `unknown`).

### Conflict resolution

When sources disagree on status, the finding is marked `conflicting`, the primary
/ most-recent official source is used as the lead, and a `conflict_summary`
preserves each source's exact wording, tier, scope, and date.

### Freshness

`freshness_state` is computed from the newest of `updated_at` / `published_at`
against configurable thresholds (`HEALTH_CURRENT_DAYS`, `HEALTH_RECENT_DAYS`). A
missing date yields `unknown` — never a guessed "current". The UI shows
`Last checked: <timestamp>`.

### Caching

A short-lived, configurable in-memory cache (`HEALTH_CACHE_TTL`, default 900s;
`0` disables it) avoids hammering public-health infrastructure. Cached results are
flagged (`from_cache`, `cache_hits`) and the real retrieval timestamp is always
shown. Nothing is cached indefinitely.

### Failure semantics (no silent fallback)

- All sources unreachable → `live_data_status="unavailable"`. Model knowledge is
  **not** used as a substitute; the answer says current data could not be
  retrieved.
- Sources reachable but nothing relevant → `no_relevant_current_data`. Absence of
  a report is explicitly **not** treated as proof that no outbreak exists.
- Some sources fail → `partial`, with the failed sources named.

## Evidence fusion

The agent fuses evidence while retaining provenance kinds (`Evidence.kind`):
`image_observation`, `neo4j_evidence`, `pubmed_evidence`, `health_surveillance`
(primary/regional official), and `health_news` (secondary media). The prompt
groups evidence by kind so the model keeps each source type distinct and preserves
uncertainty.

## Safety (population vs. individual)

The deterministic safety layer (`multimodal/safety.py`) adds a
**personal-medical-determination** check on top of the existing diagnostic-
overreach and fabricated-citation enforcement. If the generated answer tells the
user they are/​will be infected, infers their personal infection status, or
presents treatment as authorized for them, the substantive answer is **withheld**
and replaced with a conservative message. Population- and region-level statements
("cases are rising in the region") are allowed. All checks are pure, deterministic,
and unit-tested — they run *after* generation and change what the user sees, not
just what is logged.

## Execution trace (not chain-of-thought)

Every agent response includes a structured `trace` (§17): `route`, `tools_used`,
`router_reasons`, `retrieval_status`, `live_data_status`, `locations`,
`last_checked`, and `safety_action`. This is declarative provenance only — it
never exposes model chain-of-thought.

## API

The composed agent is served at `POST /agent` (the original `/analyze` endpoint is
unchanged). Unlike `/analyze`, the image is **optional** and an explicit
`location` field scopes any regional lookup.

```bash
# text + live health, explicitly scoped
curl -X POST http://localhost:8001/agent \
  -F "question=Is there a current cholera outbreak I should know about?" \
  -F "location=Hyderabad, Telangana, India"

# combined image + literature + live health
curl -X POST http://localhost:8001/agent \
  -F "question=Describe this rash and any current outbreaks in my area" \
  -F "location=India" \
  -F "image=@sample.png"
```

The response includes `answer`, `modality`, `route`, `trace`, `needs_location`,
`location_prompt`, `observations`, `evidence` (with `kind`), `health` (the full
live-intelligence result), `limitations`, `warnings`, `retrieval_status`,
`live_data_status`, `last_checked`, and `safety_action`.

The browser demo at `GET /` (`multimodal/demo.html`) exposes question + optional
image + optional location, and renders the route, tools, disease-finding table
(status, transmission, relevance, risk, alert, freshness, sources), scope,
`Last checked` timestamp, observations, grouped evidence, safety status, and
limitations.

## Configuration reference (live health)

| Variable | Purpose | Default |
| --- | --- | --- |
| `HEALTH_TIMEOUT` | Per-source HTTP timeout (s) | `15` |
| `HEALTH_USER_AGENT` | User-Agent sent to feeds | `RAGnosis-HealthIntelligence/1.0 (...)` |
| `HEALTH_CACHE_TTL` | Cache lifetime (s); `0` disables | `900` |
| `HEALTH_MAX_ITEMS_PER_SOURCE` | Max items parsed per feed | `40` |
| `HEALTH_CURRENT_DAYS` | Freshness "current" threshold (days) | `14` |
| `HEALTH_RECENT_DAYS` | Freshness "recent" threshold (days) | `60` |
| `HEALTH_SOURCE_FEEDS` | Override feeds: `org|tier|scope|url` per line/`;` | (built-in defaults) |

`tier` ∈ `primary_official` / `regional_official` / `secondary`;
`scope` ∈ `global` / `national` / `regional` / `local` / `unknown`.

When `HEALTH_SOURCE_FEEDS` is empty, built-in `DEFAULT_HEALTH_FEEDS` are used
(WHO Disease Outbreak News, CDC, ECDC). Endpoints are configurable — there are no
hardcoded endpoint assumptions baked into the logic. To add India-specific
official sources (MoHFW, NCDC, IDSP/successor, state health departments such as
Telangana) or PAHO, set their feed URLs via `HEALTH_SOURCE_FEEDS`.

## Optional live smoke test

`scripts/live_health_smoke.py` is the **only** code path that touches the network,
and only when run explicitly. It is **not** part of CI.

```bash
python scripts/live_health_smoke.py --location "Hyderabad, Telangana, India"
python scripts/live_health_smoke.py --location "India" --location "Brazil"
```

It prints the retrieval timestamp, sources attempted/succeeded/failed,
publication/update dates, and the number of findings. If a source is unreachable
it is reported as failed — nothing is fabricated.

## Testing

All tests run offline by default with injected fake providers
(`tests/test_health_intelligence.py`, `tests/test_agent.py`,
`tests/test_routing.py`, `tests/test_location.py`,
`tests/test_safety_personal.py`, `tests/test_agent_api.py`). They cover location
behaviors, freshness, timestamps, disease discovery, transmission parsing, status
classification, geo-scope, source precedence, conflict resolution, dedupe,
underreported/secondary-only handling, data-gap states, all-unavailable,
partial-failure, no-relevant-data, alert classification, provenance retention,
routing, combined flows, population-vs-individual safety, and the API contract.

## Limitations

- Live public-health information reflects only the sources reachable at the shown
  retrieval time. It is **not** real-time surveillance (a mathematical claim we do
  not make) and **not** globally exhaustive coverage.
- Absence of a current report is not proof that no outbreak exists.
- Current status and risk come from official sources when available; RAGnosis
  never invents a severity or risk score, and never asserts intentional
  concealment or downplaying without an authoritative source.
- RAGnosis reports population- and region-level information; it does not assess an
  individual's infection status and is not a diagnostic system.
