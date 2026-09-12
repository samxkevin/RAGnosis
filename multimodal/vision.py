from __future__ import annotations

import base64
import json
import os
from pathlib import Path
from typing import Any

import requests

from .image import inspect_image
from .schemas import ImageObservation
from .safety import build_system_instruction


class VisionProvider:
    """Small OpenAI-compatible vision adapter.

    The endpoint and model are configuration, not application logic. This keeps
    RAGnosis independent of a single model vendor and makes local VLM adapters
    possible later without changing the service layer.
    """

    def __init__(self) -> None:
        self.api_key = os.getenv("OPENAI_API_KEY", "")
        self.base_url = os.getenv("MULTIMODAL_BASE_URL", "https://api.openai.com/v1").rstrip("/")
        self.model = os.getenv("MULTIMODAL_VISION_MODEL", "gpt-4.1-mini")
        self.timeout = float(os.getenv("MULTIMODAL_TIMEOUT", "90"))

    def configured(self) -> bool:
        return bool(self.api_key)

    def _data_url(self, path: str | Path) -> str:
        suffix = Path(path).suffix.lower()
        mime = {".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".png": "image/png", ".webp": "image/webp"}.get(suffix)
        if mime is None:
            raise ValueError(f"Unsupported vision input: {suffix}")
        encoded = base64.b64encode(Path(path).read_bytes()).decode("ascii")
        return f"data:{mime};base64,{encoded}"

    def observe(self, path: str | Path, question: str) -> tuple[list[ImageObservation], str]:
        metadata = inspect_image(path)
        if not self.configured():
            raise RuntimeError("OPENAI_API_KEY is not configured for multimodal analysis.")

        prompt = f"""Analyze this biomedical image only for observable, research-oriented features.

User question: {question}

Return JSON only with this shape:
{{
  "observations": [
    {{"label": "...", "description": "...", "confidence": null, "location": "...", "caveat": "..."}}
  ]
}}

Rules:
- Do not diagnose disease.
- Do not claim a tumor, cancer, infection, fracture, or other condition is confirmed.
- Describe visible patterns conservatively and include uncertainty.
- If the image is not a medical image or is inadequate, report that.
- Confidence must be null unless the model can justify it as an observation confidence.
- Do not infer patient identity or demographics not visible in the image.

Technical image metadata: {json.dumps(metadata, sort_keys=True)}
"""

        payload = {
            "model": self.model,
            "temperature": 0,
            "response_format": {"type": "json_object"},
            "messages": [
                {"role": "system", "content": build_system_instruction()},
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": prompt},
                        {"type": "image_url", "image_url": {"url": self._data_url(path)}},
                    ],
                },
            ],
        }
        response = requests.post(
            f"{self.base_url}/chat/completions",
            headers={"Authorization": f"Bearer {self.api_key}", "Content-Type": "application/json"},
            json=payload,
            timeout=self.timeout,
        )
        response.raise_for_status()
        body: dict[str, Any] = response.json()
        content = body["choices"][0]["message"]["content"]
        parsed = json.loads(content) if isinstance(content, str) else content

        observations: list[ImageObservation] = []
        for item in parsed.get("observations", []):
            observations.append(
                ImageObservation(
                    label=str(item.get("label", "unspecified")),
                    description=str(item.get("description", "")),
                    confidence=item.get("confidence"),
                    location=item.get("location"),
                    caveat=item.get("caveat"),
                )
            )
        return observations, self.model
