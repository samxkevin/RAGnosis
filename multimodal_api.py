from __future__ import annotations

import os
import tempfile
from pathlib import Path

from flask import Flask, jsonify, request

from multimodal.schemas import MultimodalRequest
from multimodal.service import MultimodalRAGService

app = Flask(__name__)
service = MultimodalRAGService()
MAX_UPLOAD_BYTES = int(os.getenv("MULTIMODAL_MAX_UPLOAD_BYTES", str(12 * 1024 * 1024)))


@app.get("/health")
def health():
    return jsonify({
        "status": "ok",
        "service": "ragnosis-multimodal",
        "vision_configured": service.vision.configured(),
        "generation_configured": bool(service.cohere_key),
    })


@app.post("/analyze")
def analyze():
    question = (request.form.get("question") or "").strip()
    image = request.files.get("image")
    if not question:
        return jsonify({"error": "question is required"}), 400
    if image is None:
        return jsonify({"error": "image is required"}), 400

    image.stream.seek(0, 2)
    size = image.stream.tell()
    image.stream.seek(0)
    if size > MAX_UPLOAD_BYTES:
        return jsonify({"error": "image exceeds upload limit"}), 413

    suffix = Path(image.filename or "image.png").suffix.lower() or ".png"
    temp_path = None
    try:
        with tempfile.NamedTemporaryFile(delete=False, suffix=suffix) as handle:
            image.save(handle)
            temp_path = handle.name
        result = service.run(MultimodalRequest(question=question, image_path=temp_path))
        return jsonify({
            "answer": result.answer,
            "modality": result.modality,
            "observations": [o.__dict__ for o in result.observations],
            "evidence": [e.__dict__ for e in result.evidence],
            "limitations": result.limitations,
            "vision_model": result.model,
        })
    except Exception as exc:
        app.logger.exception("multimodal analysis failed")
        return jsonify({"error": str(exc)}), 502
    finally:
        if temp_path:
            try:
                os.unlink(temp_path)
            except OSError:
                pass


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.getenv("PORT", "8001")), debug=False)
