from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any, Literal


Modality = Literal["text", "image", "multimodal"]
RetrievalStatus = Literal["ok", "empty", "unavailable", "skipped"]


def _clamp_confidence(value: Any) -> float | None:
    """Normalize a model-supplied confidence into ``[0.0, 1.0]`` or ``None``.

    Vision models are unreliable at self-reporting calibrated probabilities, so
    we never fabricate one. We only accept a numeric value and clamp it into the
    valid range; anything else (including strings like ``"high"``) becomes
    ``None`` so downstream consumers never treat a made-up number as a score.
    """
    if isinstance(value, bool):  # bool is a subclass of int; reject it explicitly
        return None
    if not isinstance(value, (int, float)):
        return None
    try:
        numeric = float(value)
    except (TypeError, ValueError):
        return None
    if numeric != numeric:  # NaN
        return None
    return max(0.0, min(1.0, numeric))


@dataclass(frozen=True)
class Evidence:
    source: str
    title: str
    excerpt: str
    uri: str | None = None
    score: float | None = None
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class ImageObservation:
    label: str
    description: str
    confidence: float | None = None
    location: str | None = None
    caveat: str | None = None

    def __post_init__(self) -> None:
        # Normalize confidence without mutating a frozen dataclass directly.
        object.__setattr__(self, "confidence", _clamp_confidence(self.confidence))

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class MultimodalRequest:
    question: str
    image_path: str | None = None
    conversation: list[dict[str, str]] = field(default_factory=list)

    @property
    def modality(self) -> Modality:
        if self.image_path and self.question:
            return "multimodal"
        if self.image_path:
            return "image"
        return "text"


@dataclass(frozen=True)
class MultimodalResponse:
    answer: str
    modality: Modality
    observations: list[ImageObservation] = field(default_factory=list)
    evidence: list[Evidence] = field(default_factory=list)
    limitations: list[str] = field(default_factory=list)
    model: str | None = None
    generation_model: str | None = None
    retrieval_status: RetrievalStatus = "skipped"
    warnings: list[str] = field(default_factory=list)
    image_metadata: dict[str, Any] = field(default_factory=dict)
    safety_action: str = "pass"

    def to_dict(self) -> dict[str, Any]:
        return {
            "answer": self.answer,
            "modality": self.modality,
            "observations": [o.to_dict() for o in self.observations],
            "evidence": [e.to_dict() for e in self.evidence],
            "limitations": list(self.limitations),
            "vision_model": self.model,
            "generation_model": self.generation_model,
            "retrieval_status": self.retrieval_status,
            "warnings": list(self.warnings),
            "image_metadata": dict(self.image_metadata),
            "safety_action": self.safety_action,
        }
