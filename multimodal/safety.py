from __future__ import annotations

import re
from dataclasses import dataclass, field

from .schemas import Evidence, ImageObservation

MEDICAL_BOUNDARY = (
    "RAGnosis provides informational, evidence-grounded biomedical analysis. "
    "It is not a diagnostic or treatment system and must not replace qualified clinical judgment."
)

IMAGE_BOUNDARY = (
    "Image observations are model-generated observations, not confirmed findings. "
    "The system must not present an image observation as a diagnosis, malignancy determination, "
    "risk score, or treatment recommendation."
)

# Short, explicit rules that both the vision stage and the generation stage share.
# The vision prompt and tests reference these by content, so they live here as the
# single source of truth for the safety contract.
IMAGE_RULES = (
    "Do not diagnose disease.",
    "Do not give a definitive diagnosis from an image.",
    "Do not claim a tumor, cancer, infection, fracture, or other condition is confirmed.",
    "Describe visible patterns conservatively and always include uncertainty.",
    "If the image is not a medical image or is inadequate, report that explicitly.",
    "Confidence must be null unless it is a justified observation confidence.",
    "Do not infer patient identity or demographics that are not visible in the image.",
)


def build_system_instruction() -> str:
    rules = "\n".join(f"- {rule}" for rule in IMAGE_RULES)
    return f"""You are RAGnosis, an evidence-grounded biomedical research assistant.

SAFETY BOUNDARY:
- {MEDICAL_BOUNDARY}
- {IMAGE_BOUNDARY}
- Distinguish what is directly observed, what is retrieved from evidence, and what is uncertain.
- Never invent a source, citation, imaging finding, patient fact, or confidence value.
- Do not infer a person's identity from an image.
{rules}
- If the supplied image is inadequate, say that explicitly.
- Encourage qualified clinical review for personal medical decisions.

OUTPUT DISCIPLINE:
1. Observations, if any.
2. Relevant evidence and why it was retrieved.
3. Uncertainty and limitations.
4. A concise answer to the user's question.
"""


# ---------------------------------------------------------------------------
# Deterministic output validation
# ---------------------------------------------------------------------------
# The prompt above *asks* the model to behave. These checks *verify* the output
# after generation, independent of the model, so an overconfident or fabricated
# claim is caught by code rather than trusted blindly.

SAFETY_NOTICE = (
    "\n\n---\nSafety note: The statements above are informational and derived from "
    "model observations and retrieved literature. They are not a diagnosis. "
    "Please consult a qualified clinician for any personal medical decision."
)

# Patterns that assert a *confirmed* diagnosis from an image. We look for
# definitive framing ("this is cancer", "confirms malignancy", "diagnosed with")
# rather than merely mentioning a condition, which is legitimate.
_DEFINITIVE_PATTERNS = (
    r"\b(this|the)\s+(image|scan|x-?ray|mri|ct|ultrasound)\s+(shows|confirms|proves|demonstrates)\s+"
    r"(a\s+)?(malignan\w+|cancer|tumou?r|carcinoma|metastas\w+|fracture|infection)\b",
    r"\bconfirm(s|ed)?\s+(a\s+)?(diagnosis|malignan\w+|cancer|tumou?r|carcinoma)\b",
    r"\b(you|the patient)\s+(have|has|are|is)\s+(definitely\s+|certainly\s+)?"
    r"(cancer|a\s+tumou?r|a\s+malignan\w+|carcinoma)\b",
    r"\b(is|are)\s+(definitely|certainly|clearly)\s+(malignant|cancerous|benign)\b",
    r"\bdiagnos(ed|is)\s+(with|of|as)\s+\w+",
    r"\b100%\s+(certain|sure|confident)\b",
    r"\bthere\s+is\s+no\s+(doubt|need)\s+(that|to)\b.*\b(cancer|malignan\w+|tumou?r)\b",
)

_PMID_PATTERN = re.compile(r"\bPMID[:\s]*([0-9]{4,9})\b", re.IGNORECASE)
_PUBMED_URL_PATTERN = re.compile(
    r"pubmed\.ncbi\.nlm\.nih\.gov/([0-9]{4,9})", re.IGNORECASE
)


@dataclass
class ValidationResult:
    """Outcome of deterministic post-generation validation."""

    text: str
    warnings: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.warnings


def _known_pmids(evidence: list[Evidence]) -> set[str]:
    pmids: set[str] = set()
    for item in evidence:
        pmid = str(item.metadata.get("pmid", "")).strip()
        if pmid:
            pmids.add(pmid)
        if item.uri:
            for match in _PUBMED_URL_PATTERN.findall(item.uri):
                pmids.add(match)
    return pmids


def detect_overconfident_diagnosis(text: str) -> list[str]:
    """Return the definitive-diagnosis phrases found in ``text`` (may be empty)."""
    found: list[str] = []
    lowered = text.lower()
    for pattern in _DEFINITIVE_PATTERNS:
        match = re.search(pattern, lowered)
        if match:
            found.append(match.group(0).strip())
    return found


def detect_fabricated_citations(text: str, evidence: list[Evidence]) -> list[str]:
    """Return PMIDs cited in ``text`` that are absent from retrieved evidence."""
    known = _known_pmids(evidence)
    cited = set(_PMID_PATTERN.findall(text))
    cited.update(_PUBMED_URL_PATTERN.findall(text))
    return sorted(cited - known)


def validate_response(
    text: str,
    evidence: list[Evidence] | None = None,
    observations: list[ImageObservation] | None = None,
) -> ValidationResult:
    """Deterministically check a generated answer against the safety contract.

    This never edits the model's substantive claims. It (a) records warnings for
    definitive-diagnosis language and citations not present in the retrieved
    evidence, and (b) appends a standing safety notice when the answer makes
    medical statements without one. The model can never suppress these checks.
    """
    evidence = evidence or []
    warnings: list[str] = []

    overconfident = detect_overconfident_diagnosis(text)
    if overconfident:
        warnings.append(
            "Potential definitive-diagnosis language detected: "
            + "; ".join(sorted(set(overconfident)))
        )

    fabricated = detect_fabricated_citations(text, evidence)
    if fabricated:
        warnings.append(
            "Citations not present in retrieved evidence (possible fabrication): PMID "
            + ", PMID ".join(fabricated)
        )

    result_text = text
    if "safety note:" not in text.lower():
        result_text = text.rstrip() + SAFETY_NOTICE

    return ValidationResult(text=result_text, warnings=warnings)
