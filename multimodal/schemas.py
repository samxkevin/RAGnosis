from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal


Modality = Literal["text", "image", "multimodal"]


@dataclass(frozen=True)
class Evidence:
    source: str
    title: str
    excerpt: str
    uri: str | None = None
    score: float | None = None
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class ImageObservation:
    label: str
    description: str
    confidence: float | None = None
    location: str | None = None
    caveat: str | None = None


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
