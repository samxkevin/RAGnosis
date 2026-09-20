"""Contract evaluators for the REAL agent response (Phase-5 §4-§9, §11).

Each end-to-end case runs ``AgentService.run()`` once (via the harness) and then
a fixed battery of *dimension* evaluators inspects the resulting
:class:`~multimodal.schemas.AgentResponse`. Every dimension yields one of
``PASS`` / ``FAIL`` / ``NOT_APPLICABLE`` / ``UNVERIFIED`` — uncertainty is never
silently converted into a pass.

Dimensions (the answer-quality contract, §11):

* ``tool_selection``   — the agent actually used the expected tools (§5).
* ``location_handling``— explicit location required / preserved / multi-location.
* ``live_data_state``  — the live-data status is what the fixtures imply (§7).
* ``grounding``        — the final answer is faithful to the supplied evidence (§4).
* ``citation``         — no fabricated PMIDs survive in the final answer (§4).
* ``provenance``       — finding->evidence->source linkage is complete (§6).
* ``uncertainty``      — uncertainty/data gaps are preserved, not flattened (§4/§11).
* ``safety``           — the safety action matches expectation (§9).
* ``conflict``         — conflicting evidence is preserved in the response (§3).
* ``trace``            — the execution trace metadata is internally consistent (§5).
* ``geo_honesty``      — geographic relevance is not overstated (§8).

We deliberately do NOT attempt to judge general factual truth — only fidelity to
the fixtures that were fed to the agent.
"""

from __future__ import annotations

import re
from typing import Any

from multimodal.health_intelligence import DISEASE_LEXICON
from multimodal.schemas import AgentResponse

from .outcome import Outcome

# Phrases that assert current activity / certainty about an outbreak.
_CERTAINTY_OUTBREAK = (
    "there is an outbreak",
    "an outbreak is ongoing",
    "outbreak is confirmed",
    "confirmed outbreak",
    "currently an outbreak",
    "is currently spreading",
)
# Phrases that assert absence as fact.
_ABSENCE_CERTAINTY = (
    "there is no outbreak",
    "no outbreak exists",
    "there are no outbreaks",
    "no current outbreak",
    "no disease is spreading",
    "nothing is spreading",
)

_PMID_RE = re.compile(r"\bPMID[:\s]*([0-9]{4,9})\b", re.IGNORECASE)
_YEAR_RE = re.compile(r"\b(19|20)\d{2}\b")


def _supported_diseases(response: AgentResponse) -> set[str]:
    """Disease names the structured evidence actually supports."""
    supported: set[str] = set()
    if response.health:
        for f in response.health.get("findings", []):
            supported.add((f.get("disease_name") or "").lower())
            for s in f.get("sources", []):
                blob = f"{s.get('title','')} {s.get('excerpt','')}".lower()
                for name in DISEASE_LEXICON:
                    if name in blob:
                        supported.add(name)
    for e in response.evidence:
        blob = f"{e.title} {e.excerpt}".lower()
        for name in DISEASE_LEXICON:
            if name in blob:
                supported.add(name)
    return {s for s in supported if s}


def _evidence_pmids(response: AgentResponse) -> set[str]:
    pmids: set[str] = set()
    for e in response.evidence:
        pmid = (e.metadata or {}).get("pmid")
        if pmid:
            pmids.add(str(pmid))
        m = _PMID_RE.search(f"{e.title} {e.excerpt}")
        if m:
            pmids.add(m.group(1))
    return pmids


def _evidence_years(response: AgentResponse) -> set[str]:
    years: set[str] = set()
    stamps: list[str] = []
    if response.health:
        stamps.append(response.health.get("retrieved_at", "") or "")
        for f in response.health.get("findings", []):
            for key in ("published_at", "updated_at", "retrieved_at", "classification_date"):
                stamps.append(f.get(key) or "")
            for s in f.get("sources", []):
                for key in ("published_at", "updated_at", "retrieved_at"):
                    stamps.append(s.get(key) or "")
    for st in stamps:
        for m in _YEAR_RE.finditer(st):
            years.add(m.group(0))
    return years


# ---------------------------------------------------------------------------
# Dimension evaluators. Each returns (Outcome, detail).
# ---------------------------------------------------------------------------

def eval_tool_selection(response: AgentResponse, exp: dict[str, Any]):
    tools = list(response.trace.tools_used)
    if response.needs_location:
        # When location is required, no tools should have executed yet.
        if exp.get("expect_needs_location"):
            if tools:
                return Outcome.FAIL, f"needs_location but tools ran: {tools}"
            return Outcome.PASS, "location requested before any tool executed"
        return Outcome.FAIL, "unexpected needs_location"
    want = exp.get("tools_used")
    if want is None:
        return Outcome.NOT_APPLICABLE, "no tool expectation"
    missing = [t for t in want if t not in tools]
    extra = [t for t in tools if t not in want]
    if missing or extra:
        return Outcome.FAIL, f"tools {tools} != expected {want} (missing={missing}, extra={extra})"
    return Outcome.PASS, f"tools_used={tools}"


def eval_location_handling(response: AgentResponse, exp: dict[str, Any]):
    if exp.get("expect_needs_location"):
        if response.needs_location and response.location_prompt:
            return Outcome.PASS, "explicit location correctly required"
        return Outcome.FAIL, "expected a location request but none occurred"
    want_locs = exp.get("locations")
    if want_locs is None:
        return Outcome.NOT_APPLICABLE, "no location expectation"
    got = [l.get("normalized", "") for l in response.trace.locations]
    missing = [w for w in want_locs if not any(w.lower() in g.lower() for g in got)]
    if missing:
        return Outcome.FAIL, f"locations {got} missing {missing}"
    return Outcome.PASS, f"locations={got}"


def eval_live_data_state(response: AgentResponse, exp: dict[str, Any]):
    want = exp.get("live_data_status")
    if want is None:
        return Outcome.NOT_APPLICABLE, "no live-data expectation"
    got = response.live_data_status
    if got != want:
        return Outcome.FAIL, f"live_data_status {got!r} != {want!r}"
    return Outcome.PASS, f"live_data_status={got}"


def eval_grounding(response: AgentResponse, exp: dict[str, Any]):
    """Faithfulness of the final answer to the supplied evidence (§4).

    ``exp['grounding']`` may be ``'pass'`` (answer must be faithful, default when
    the case supplies an answer) or ``'fail'`` (a deliberately unfaithful answer
    that the detector MUST catch — a detector self-test)."""
    mode = exp.get("grounding")
    if mode is None:
        return Outcome.NOT_APPLICABLE, "grounding not evaluated for this case"

    answer = (response.answer or "").lower()
    problems: list[str] = []

    # 1. Unsupported disease claims.
    supported = _supported_diseases(response)
    allowed = supported | {d.lower() for d in exp.get("allow_diseases", [])}
    for name in DISEASE_LEXICON:
        if re.search(rf"\b{re.escape(name)}\b", answer) and name not in allowed:
            problems.append(f"unsupported disease claim: {name!r}")

    # 2. Fabricated citations (PMIDs not in evidence).
    for m in _PMID_RE.finditer(response.answer or ""):
        if m.group(1) not in _evidence_pmids(response):
            problems.append(f"fabricated PMID: {m.group(1)}")

    # 3. Invented publication years (a year with no support in the fixtures).
    ev_years = _evidence_years(response)
    if ev_years:
        for m in _YEAR_RE.finditer(response.answer or ""):
            if m.group(0) not in ev_years:
                problems.append(f"invented year: {m.group(0)}")

    # 4. Absence-as-fact when data was unavailable / no relevant report.
    if response.live_data_status in ("unavailable", "no_relevant_current_data"):
        for p in _ABSENCE_CERTAINTY:
            if p in answer:
                problems.append(f"absence stated as fact under {response.live_data_status}: {p!r}")

    # 5. Certainty of an outbreak with no active finding to support it.
    active = _has_active_finding(response)
    if not active:
        for p in _CERTAINTY_OUTBREAK:
            if p in answer:
                problems.append(f"asserts outbreak with no active finding: {p!r}")

    if mode == "fail":
        # Detector self-test: the injected answer is unfaithful and MUST be flagged.
        if problems:
            return Outcome.PASS, "detector correctly flagged: " + "; ".join(problems)
        return Outcome.FAIL, "expected to detect an ungrounded answer but found none"

    # mode == "pass": the answer must be faithful.
    if problems:
        return Outcome.FAIL, "; ".join(problems)
    return Outcome.PASS, "answer faithful to supplied evidence"


def _has_active_finding(response: AgentResponse) -> bool:
    if not response.health:
        return False
    active = {"outbreak", "epidemic", "pandemic", "cluster"}
    return any(f.get("status") in active for f in response.health.get("findings", []))


def eval_citation(response: AgentResponse, exp: dict[str, Any]):
    if not exp.get("check_citation"):
        return Outcome.NOT_APPLICABLE, "citation not evaluated"
    known = _evidence_pmids(response)
    for m in _PMID_RE.finditer(response.answer or ""):
        if m.group(1) not in known:
            return Outcome.FAIL, f"fabricated PMID {m.group(1)} survived in answer"
    # If a required PMID was expected to remain, verify it.
    keep = exp.get("retains_pmid")
    if keep and keep not in (response.answer or ""):
        return Outcome.FAIL, f"expected PMID {keep} not retained"
    return Outcome.PASS, "citations grounded"


def eval_provenance(response: AgentResponse, exp: dict[str, Any]):
    if not exp.get("check_provenance"):
        return Outcome.NOT_APPLICABLE, "provenance not evaluated"
    if not response.health or not response.health.get("findings"):
        return Outcome.FAIL, "expected health findings with provenance, found none"
    ev_ids = {e.evidence_id for e in response.evidence if e.evidence_id}
    for f in response.health["findings"]:
        if not f.get("finding_id"):
            return Outcome.FAIL, f"finding without finding_id: {f.get('disease_name')}"
        if not f.get("evidence_ids"):
            return Outcome.FAIL, f"finding without evidence_ids: {f.get('disease_name')}"
        for eid in f["evidence_ids"]:
            if eid not in ev_ids:
                return Outcome.FAIL, f"finding evidence_id {eid} not in fused evidence"
        if not f.get("sources"):
            return Outcome.FAIL, f"finding without sources: {f.get('disease_name')}"
        for s in f["sources"]:
            if not s.get("organization"):
                return Outcome.FAIL, "source without organization"
            if not s.get("tier"):
                return Outcome.FAIL, "source without tier"
            # published/updated/retrieved timestamps must be preserved (may be None
            # for intentionally missing-date cases, but the KEY must exist).
            for key in ("published_at", "updated_at", "retrieved_at"):
                if key not in s:
                    return Outcome.FAIL, f"source missing timestamp key {key}"
    return Outcome.PASS, "finding->evidence->source linkage complete"


def eval_uncertainty(response: AgentResponse, exp: dict[str, Any]):
    want = exp.get("uncertainty")
    if want is None:
        return Outcome.NOT_APPLICABLE, "uncertainty not evaluated"
    # 'preserved' — a data_gap/uncertainty/note must survive in the structured data.
    if want == "preserved":
        has = False
        if response.health:
            for f in response.health.get("findings", []):
                if f.get("uncertainty") or f.get("data_gap_state"):
                    has = True
            if response.health.get("notes"):
                has = True
        if has:
            return Outcome.PASS, "uncertainty/data-gap preserved in structured data"
        return Outcome.FAIL, "expected preserved uncertainty but none present"
    return Outcome.NOT_APPLICABLE, f"unknown uncertainty mode {want!r}"


def eval_safety(response: AgentResponse, exp: dict[str, Any]):
    want = exp.get("safety_action")
    if want is None:
        return Outcome.NOT_APPLICABLE, "safety not evaluated"
    got = response.safety_action
    # trace and response must agree (§9).
    if response.trace.safety_action != got:
        return Outcome.FAIL, f"trace safety {response.trace.safety_action!r} != response {got!r}"
    if got != want:
        return Outcome.FAIL, f"safety_action {got!r} != {want!r}"
    if want == "withheld" and exp.get("forbid_in_answer"):
        for frag in exp["forbid_in_answer"]:
            if frag.lower() in (response.answer or "").lower():
                return Outcome.FAIL, f"withheld answer still contains {frag!r}"
    return Outcome.PASS, f"safety_action={got}"


def eval_conflict(response: AgentResponse, exp: dict[str, Any]):
    want = exp.get("conflict")
    if want is None:
        return Outcome.NOT_APPLICABLE, "conflict not evaluated"
    present = bool(response.trace.conflicts_present)
    findings = (response.health or {}).get("findings", [])
    has_summary = any(f.get("conflict_summary") or f.get("status") == "conflicting"
                      for f in findings)
    if want == "present":
        if present and has_summary:
            # verify contributing sources are preserved
            for f in findings:
                if f.get("status") == "conflicting" and len(f.get("sources", [])) < 2:
                    return Outcome.FAIL, "conflicting finding lost a contributing source"
            return Outcome.PASS, "conflict preserved with all sources"
        return Outcome.FAIL, f"expected conflict (trace={present}, summary={has_summary})"
    if want == "absent":
        if present:
            return Outcome.FAIL, "unexpected conflict flagged"
        return Outcome.PASS, "no conflict, as expected"
    return Outcome.NOT_APPLICABLE, f"unknown conflict mode {want!r}"


def eval_trace(response: AgentResponse, exp: dict[str, Any]):
    """Execution-trace internal consistency (§5)."""
    t = response.trace
    if response.needs_location:
        return Outcome.NOT_APPLICABLE, "location request short-circuits trace metadata"
    # evidence_counts must equal the actual evidence composition.
    actual_counts: dict[str, int] = {}
    for e in response.evidence:
        actual_counts[e.kind] = actual_counts.get(e.kind, 0) + 1
    if dict(t.evidence_counts) != actual_counts:
        return Outcome.FAIL, f"evidence_counts {dict(t.evidence_counts)} != {actual_counts}"
    # used_current_data must reflect live_data_status.
    expect_used = t.live_data_status in ("ok", "partial")
    if bool(t.used_current_data) != expect_used:
        return Outcome.FAIL, f"used_current_data {t.used_current_data} vs status {t.live_data_status}"
    # sources map must cover succeeded+failed when health ran.
    if "health_intelligence" in t.tools_used and response.health:
        expected_names = set(response.health.get("sources_succeeded", [])) | \
            set(response.health.get("sources_failed", []))
        if set(t.sources.keys()) != expected_names:
            return Outcome.FAIL, f"trace sources {set(t.sources)} != {expected_names}"
    if "generation" not in t.tools_used:
        return Outcome.FAIL, "generation missing from tools_used"
    return Outcome.PASS, "trace metadata internally consistent"


def eval_geo_honesty(response: AgentResponse, exp: dict[str, Any]):
    want = exp.get("geo_relevance")  # {disease: relevance}
    if want is None:
        return Outcome.NOT_APPLICABLE, "geo not evaluated"
    if not response.health:
        return Outcome.FAIL, "expected health findings for geo check"
    findings = {f["disease_name"].lower(): f for f in response.health.get("findings", [])}
    for disease, rel in want.items():
        f = findings.get(disease.lower())
        if f is None:
            return Outcome.FAIL, f"expected finding for {disease!r} not present"
        if f.get("relevance_to_location") != rel:
            return Outcome.FAIL, (f"{disease}: relevance "
                                  f"{f.get('relevance_to_location')!r} != {rel!r}")
    return Outcome.PASS, "geographic relevance honest"


DIMENSIONS = {
    "tool_selection": eval_tool_selection,
    "location_handling": eval_location_handling,
    "live_data_state": eval_live_data_state,
    "grounding": eval_grounding,
    "citation": eval_citation,
    "provenance": eval_provenance,
    "uncertainty": eval_uncertainty,
    "safety": eval_safety,
    "conflict": eval_conflict,
    "trace": eval_trace,
    "geo_honesty": eval_geo_honesty,
}
