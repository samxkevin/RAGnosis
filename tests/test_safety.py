"""Deterministic safety-validation tests (independent of any model)."""

from multimodal.safety import (
    detect_fabricated_citations,
    detect_overconfident_diagnosis,
    validate_response,
)
from multimodal.schemas import Evidence


def _evidence(pmid: str) -> Evidence:
    return Evidence(
        source="PubMed",
        title="t",
        excerpt="e",
        uri=f"https://pubmed.ncbi.nlm.nih.gov/{pmid}/",
        metadata={"pmid": pmid},
    )


def test_detects_definitive_diagnosis_language():
    assert detect_overconfident_diagnosis("This scan confirms malignancy.")
    assert detect_overconfident_diagnosis("The image shows cancer.")
    assert detect_overconfident_diagnosis("You have a tumor.")
    assert detect_overconfident_diagnosis("This is definitely malignant.")


def test_allows_conservative_language():
    conservative = (
        "The image shows an area of increased density that may warrant further "
        "review. This is not a diagnosis and cannot confirm any condition."
    )
    assert detect_overconfident_diagnosis(conservative) == []


def test_detects_fabricated_pmid():
    text = "See PMID: 99999999 for details."
    fabricated = detect_fabricated_citations(text, [_evidence("12345678")])
    assert fabricated == ["99999999"]


def test_accepts_cited_pmid_present_in_evidence():
    text = "As shown in PMID 12345678."
    fabricated = detect_fabricated_citations(text, [_evidence("12345678")])
    assert fabricated == []


def test_detects_fabricated_pubmed_url():
    text = "Reference: https://pubmed.ncbi.nlm.nih.gov/55555555/"
    fabricated = detect_fabricated_citations(text, [_evidence("12345678")])
    assert "55555555" in fabricated


def test_validate_appends_safety_notice_when_missing():
    result = validate_response("Some informational content.", [])
    assert "safety note:" in result.text.lower()


def test_validate_does_not_duplicate_safety_notice():
    text = "Content. Safety note: consult a clinician."
    result = validate_response(text, [])
    assert result.text.lower().count("safety note:") == 1


def test_validate_flags_overconfidence_and_fabrication_together():
    text = "This scan confirms cancer. See PMID 99999999."
    result = validate_response(text, [_evidence("11111111")])
    assert not result.ok
    assert len(result.warnings) == 2


def test_validate_clean_response_has_no_warnings():
    text = (
        "The image shows nonspecific findings that may warrant clinician review. "
        "Evidence in PMID 12345678 discusses similar patterns but is not "
        "patient-specific."
    )
    result = validate_response(text, [_evidence("12345678")])
    assert result.ok
