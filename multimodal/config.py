"""Centralized configuration for the RAGnosis multimodal track.

All environment-driven settings live here so the rest of the package reads
configuration from one documented place instead of scattering ``os.getenv``
calls across modules. Nothing in this module performs network I/O or reads
secrets at import time; values are resolved lazily from the environment when a
``MultimodalConfig`` is constructed. This keeps the package importable (and
unit-testable) without any credentials present.
"""

from __future__ import annotations

import os
from dataclasses import dataclass


def _get_float(name: str, default: float) -> float:
    raw = os.getenv(name)
    if raw is None or not raw.strip():
        return default
    try:
        return float(raw)
    except (TypeError, ValueError):
        return default


def _get_int(name: str, default: int) -> int:
    raw = os.getenv(name)
    if raw is None or not raw.strip():
        return default
    try:
        return int(raw)
    except (TypeError, ValueError):
        return default


@dataclass(frozen=True)
class MultimodalConfig:
    """Immutable snapshot of the multimodal configuration.

    Construct with :meth:`from_env` to read the current process environment.
    Tests can build an instance directly with explicit values.
    """

    # Vision provider (OpenAI-compatible chat/completions endpoint).
    openai_api_key: str = ""
    vision_base_url: str = "https://api.openai.com/v1"
    vision_model: str = "gpt-4.1-mini"
    vision_timeout: float = 90.0

    # Grounded generation (Cohere).
    cohere_api_key: str = ""
    cohere_model: str = "command-a-03-2025"
    cohere_fallback_model: str = "command-r7b-12-2024"

    # Biomedical retrieval (NCBI PubMed E-utilities).
    pubmed_timeout: float = 15.0
    pubmed_tool: str = "RAGnosis"
    pubmed_email: str = ""
    pubmed_api_key: str = ""

    # Image handling limits.
    max_upload_bytes: int = 12 * 1024 * 1024
    max_image_pixels: int = 40_000_000  # decompression-bomb guard (~40 MP)
    max_vision_dimension: int = 2048  # longest edge sent to the vision model

    @classmethod
    def from_env(cls) -> "MultimodalConfig":
        return cls(
            openai_api_key=os.getenv("OPENAI_API_KEY", ""),
            vision_base_url=os.getenv(
                "MULTIMODAL_BASE_URL", "https://api.openai.com/v1"
            ).rstrip("/"),
            vision_model=os.getenv("MULTIMODAL_VISION_MODEL", "gpt-4.1-mini"),
            vision_timeout=_get_float("MULTIMODAL_TIMEOUT", 90.0),
            cohere_api_key=os.getenv("COHERE_API_KEY", ""),
            cohere_model=os.getenv("COHERE_MULTIMODAL_RAG_MODEL", "command-a-03-2025"),
            cohere_fallback_model=os.getenv(
                "COHERE_MULTIMODAL_FALLBACK_MODEL", "command-r7b-12-2024"
            ),
            pubmed_timeout=_get_float("PUBMED_TIMEOUT", 15.0),
            pubmed_tool=os.getenv("PUBMED_TOOL", "RAGnosis"),
            pubmed_email=os.getenv("PUBMED_EMAIL", ""),
            pubmed_api_key=os.getenv("PUBMED_API_KEY", ""),
            max_upload_bytes=_get_int(
                "MULTIMODAL_MAX_UPLOAD_BYTES", 12 * 1024 * 1024
            ),
            max_image_pixels=_get_int("MULTIMODAL_MAX_IMAGE_PIXELS", 40_000_000),
            max_vision_dimension=_get_int("MULTIMODAL_MAX_VISION_DIMENSION", 2048),
        )
