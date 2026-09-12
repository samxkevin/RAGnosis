from __future__ import annotations

import logging
import os
import tempfile
from pathlib import Path

from flask import Flask, jsonify, request

from multimodal.config import MultimodalConfig
from multimodal.image import ImageValidationError, SUPPORTED_IMAGE_TYPES
from multimodal.schemas import MultimodalRequest
from multimodal.service import MultimodalRAGService

logger = logging.getLogger("ragnosis.multimodal_api")

CONFIG = MultimodalConfig.from_env()
app = Flask(__name__)
# Reject oversized uploads at the WSGI layer before buffering the whole body.
app.config["MAX_CONTENT_LENGTH"] = CONFIG.max_upload_bytes
service = MultimodalRAGService(CONFIG)


@app.get("/health")
def health():
    return jsonify(
        {
            "status": "ok",
            "service": "ragnosis-multimodal",
            "vision_configured": service.vision.configured(),
            "generation_configured": service.generator.configured(),
        }
    )


@app.errorhandler(413)
def too_large(_error):
    return jsonify({"error": "image exceeds upload limit"}), 413


@app.post("/analyze")
def analyze():
    question = (request.form.get("question") or "").strip()
    image = request.files.get("image")
    if not question:
        return jsonify({"error": "question is required"}), 400
    if image is None:
        return jsonify({"error": "image is required"}), 400

    suffix = Path(image.filename or "image.png").suffix.lower() or ".png"
    if suffix not in SUPPORTED_IMAGE_TYPES:
        return jsonify(
            {
                "error": (
                    f"unsupported image type '{suffix}'. supported: "
                    f"{', '.join(sorted(SUPPORTED_IMAGE_TYPES))}"
                )
            }
        ), 400

    temp_path = None
    try:
        with tempfile.NamedTemporaryFile(delete=False, suffix=suffix) as handle:
            image.save(handle)
            temp_path = handle.name
        result = service.run(MultimodalRequest(question=question, image_path=temp_path))
        return jsonify(result.to_dict())
    except ImageValidationError as exc:
        # Client-side problem with the uploaded image -> 400, not 502.
        return jsonify({"error": str(exc)}), 400
    except Exception as exc:  # noqa: BLE001 - upstream/provider failure
        logger.exception("multimodal analysis failed")
        return jsonify({"error": str(exc)}), 502
    finally:
        if temp_path:
            try:
                os.unlink(temp_path)
            except OSError:
                pass


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.getenv("PORT", "8001")), debug=False)
