"""Personal-medical / individual-infection safety tests (§18)."""

from multimodal.safety import (
    SafetyAction,
    detect_personal_medical_claim,
    validate_response,
)


def test_detects_you_are_infected():
    assert detect_personal_medical_claim("Based on this, you have been infected.")


def test_detects_prediction_of_infection():
    assert detect_personal_medical_claim("You will get infected within days.")


def test_detects_treatment_authorization():
    assert detect_personal_medical_claim("You should take antibiotics for this.")


def test_negated_personal_claim_not_flagged():
    assert detect_personal_medical_claim("You do not have an infection.") == []


def test_population_statement_not_flagged():
    text = "Cases are rising in the region and the population-level risk is elevated."
    assert detect_personal_medical_claim(text) == []


def test_validate_withholds_personal_claim():
    result = validate_response("You are infected and you should take amoxicillin.")
    assert result.action is SafetyAction.WITHHELD
    assert "infected" not in result.text.lower() or "withheld" in result.text.lower()
    assert any("personal medical determination" in w.lower() for w in result.warnings)


def test_validate_passes_population_level():
    text = (
        "WHO reports a regional cholera outbreak; the population-level risk is "
        "elevated. Consult a clinician for personal concerns."
    )
    result = validate_response(text)
    assert result.action is SafetyAction.PASS


def test_diagnosis_takes_priority_over_personal():
    # Both present -> diagnostic overreach message (priority 1).
    result = validate_response("This scan confirms cancer and you are infected.")
    assert result.action is SafetyAction.WITHHELD
