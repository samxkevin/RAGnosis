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


# --- Red-team regressions (Phase-6 §11): direct disease attribution ---------
import pytest  # noqa: E402


@pytest.mark.parametrize(
    "text",
    [
        "You have dengue.",
        "You have the flu.",
        "You have an infection.",
        "You probably have malaria.",
        "You have probably got dengue.",
        "You have a case of measles.",
        "You've got dengue.",
        "You're diagnosed with measles.",
    ],
)
def test_direct_disease_attribution_flagged(text):
    assert detect_personal_medical_claim(text), text


@pytest.mark.parametrize(
    "text",
    [
        "You should take this treatment.",
        "You should take this medication.",
        "You need to take these antibiotics.",
        "You should start the prescription today.",
    ],
)
def test_generic_treatment_authorization_flagged(text):
    assert detect_personal_medical_claim(text), text


@pytest.mark.parametrize(
    "text",
    [
        # population / general-advice statements must remain possible
        "People who have dengue may experience fever.",
        "You do not have dengue based on this.",
        "If you have symptoms, seek medical care.",
        "You should consult a doctor.",
        "You should get vaccinated.",
        "You should rest and stay hydrated.",
        "You should take precautions to avoid mosquito bites.",
        "Those who have malaria need treatment.",
        "You have questions about dengue.",
        "The evidence does not establish an outbreak.",
        "This image cannot confirm a diagnosis.",
        "Dengue cases are increasing in the region.",
    ],
)
def test_legitimate_statements_not_over_blocked(text):
    assert detect_personal_medical_claim(text) == [], text


@pytest.mark.parametrize(
    "text",
    [
        "You have dengue.",
        "You should take this treatment.",
    ],
)
def test_validate_withholds_redteam_gaps(text):
    result = validate_response(text)
    assert result.action is SafetyAction.WITHHELD, text
