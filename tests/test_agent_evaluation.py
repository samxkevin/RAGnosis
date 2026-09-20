"""Wire the end-to-end agent benchmark into the offline suite (Phase-5 §15).

These tests run the REAL ``AgentService.run()`` through deterministic injected
fakes (no network, no API keys) and assert the end-to-end contract holds. They
also carry focused regressions for the two agent bugs the benchmark surfaced:

  * a pure vision/literature question must NOT be blocked on a location just
    because the text contained an incidental geographic cue; and
  * a generated prediction that the user "will probably become infected" must be
    treated as a personal-medical claim and withheld.
"""

from __future__ import annotations

import pytest

from evaluation.agent_cases import CASES
from evaluation.agent_evaluators import DIMENSIONS
from evaluation.agent_runner import DIMENSION_ORDER, run
from evaluation.outcome import Outcome
from multimodal.safety import detect_personal_medical_claim


def test_benchmark_has_full_dimension_coverage():
    # Every declared dimension must have an evaluator and run slot.
    assert set(DIMENSION_ORDER) == set(DIMENSIONS)


def test_end_to_end_benchmark_has_no_failures():
    report = run()
    assert report.total == len(CASES)
    assert report.total >= 20  # realistic coverage (§3/§12)
    failures = report.failures()
    assert not failures, f"E2E contract failures: {failures}"
    # Every case is decisively pass/fail — nothing silently unverified.
    assert report.unverified == 0, report.unverifieds()


@pytest.mark.parametrize("case", CASES, ids=[c.id for c in CASES])
def test_each_case_passes_all_applicable_dimensions(case):
    report = run([case])
    (result,) = report.results
    assert result.error is None, result.error
    bad = [(d.dimension, d.detail) for d in result.dimensions
           if d.outcome in (Outcome.FAIL, Outcome.UNVERIFIED)]
    assert not bad, f"{case.id}: {bad}"


def test_grounding_detector_selftests_are_present():
    # At least one case must exercise the grounding detector in "fail" mode so
    # the detector cannot silently pass everything.
    fail_mode = [c for c in CASES if c.expected.get("grounding") == "fail"]
    assert fail_mode, "no grounding detector self-test case present"


# --- Regression: incidental geo cue must not force a location prompt ---------
def test_image_question_with_incidental_geo_cue_does_not_ask_location():
    case = next(c for c in CASES if c.id == "e2e-image-only")
    (result,) = run([case]).results
    tool = next(d for d in result.dimensions if d.dimension == "tool_selection")
    assert tool.outcome is Outcome.PASS, tool.detail


# --- Regression: "will probably become infected" is a personal claim --------
@pytest.mark.parametrize(
    "text",
    [
        "You will probably become infected with the flu.",
        "You will likely be infected with dengue.",
        "You are probably going to get infected.",
    ],
)
def test_infection_prediction_is_flagged(text):
    assert detect_personal_medical_claim(text)


@pytest.mark.parametrize(
    "text",
    [
        "Cases are rising in the region.",
        "You will receive your results next week.",
        "People in the area should take standard precautions.",
    ],
)
def test_benign_statements_not_flagged(text):
    assert not detect_personal_medical_claim(text)
