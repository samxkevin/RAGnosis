from __future__ import annotations

import json
import os
from typing import Any

import cohere

from .retrieval import BiomedicalRetriever
from .schemas import MultimodalRequest, MultimodalResponse
from .safety import build_system_instruction
from .vision import VisionProvider


class MultimodalRAGService:
    def __init__(self) -> None:
        self.vision = VisionProvider()
        self.retriever = BiomedicalRetriever()
        self.cohere_key = os.getenv("COHERE_API_KEY", "")
        self.cohere_model = os.getenv("COHERE_MULTIMODAL_RAG_MODEL", "command-a-03-2025")
        self._cohere: cohere.Client | None = None

    def _client(self) -> cohere.Client:
        if not self.cohere_key:
            raise RuntimeError("COHERE_API_KEY is not configured for multimodal RAG generation.")
        if self._cohere is None:
            self._cohere = cohere.Client(self.cohere_key)
        return self._cohere

    def run(self, request: MultimodalRequest) -> MultimodalResponse:
        observations = []
        vision_model = None
        if request.image_path:
            observations, vision_model = self.vision.observe(request.image_path, request.question)

        evidence = self.retriever.search(request.question, observations)
        prompt = self._build_prompt(request, observations, evidence)
        response = self._client().chat(model=self.cohere_model, message=prompt, temperature=0.1)
        answer = getattr(response, "text", "").strip()
        if not answer:
            raise RuntimeError("The generation provider returned an empty response.")

        limitations = [
            "Image observations are not diagnoses or confirmed clinical findings.",
            "Retrieved literature is supporting evidence, not patient-specific medical advice.",
            "Clinical decisions require qualified professional review of the complete patient record.",
        ]
        return MultimodalResponse(
            answer=answer,
            modality=request.modality,
            observations=observations,
            evidence=evidence,
            limitations=limitations,
            model=vision_model,
        )

    def _build_prompt(self, request: MultimodalRequest, observations: list[Any], evidence: list[Any]) -> str:
        observation_text = json.dumps([o.__dict__ for o in observations], indent=2)
        evidence_text = json.dumps([e.__dict__ for e in evidence], indent=2)
        conversation = json.dumps(request.conversation[-8:], indent=2)
        return f"""{build_system_instruction()}

USER QUESTION:
{request.question}

RECENT CONVERSATION:
{conversation}

MODEL IMAGE OBSERVATIONS:
{observation_text}

RETRIEVED BIOMEDICAL EVIDENCE:
{evidence_text}

Write a concise, evidence-grounded response. Explicitly separate image observations from conclusions supported by literature. If evidence does not support a conclusion, say so. Never turn a visual observation into a definitive diagnosis. Include PubMed links when they are present in the evidence metadata.
"""
