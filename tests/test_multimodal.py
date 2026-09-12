from pathlib import Path

from PIL import Image

from multimodal.image import inspect_image
from multimodal.schemas import MultimodalRequest
from multimodal.safety import build_system_instruction


def test_request_modality():
    assert MultimodalRequest(question="x").modality == "text"
    assert MultimodalRequest(question="x", image_path="a.png").modality == "multimodal"


def test_image_inspection(tmp_path: Path):
    path = tmp_path / "sample.png"
    Image.new("RGB", (16, 12), "white").save(path)
    metadata = inspect_image(path)
    assert metadata["width"] == 16
    assert metadata["height"] == 12
    assert len(metadata["sha256"]) == 64


def test_safety_instruction_forbids_diagnosis():
    instruction = build_system_instruction().lower()
    assert "do not diagnose disease" in instruction
    assert "do not give a definitive diagnosis from an image" in instruction
