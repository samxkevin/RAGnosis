from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Any

from PIL import Image

SUPPORTED_IMAGE_TYPES = {".png", ".jpg", ".jpeg", ".webp", ".bmp", ".tif", ".tiff"}


def inspect_image(path: str | Path) -> dict[str, Any]:
    """Validate an image and return non-sensitive technical metadata."""
    file_path = Path(path)
    if not file_path.is_file():
        raise FileNotFoundError(f"Image not found: {file_path}")
    if file_path.suffix.lower() not in SUPPORTED_IMAGE_TYPES:
        raise ValueError(f"Unsupported image type: {file_path.suffix}")

    digest = hashlib.sha256(file_path.read_bytes()).hexdigest()
    with Image.open(file_path) as image:
        image.verify()
    with Image.open(file_path) as image:
        return {
            "sha256": digest,
            "format": image.format,
            "width": image.width,
            "height": image.height,
            "mode": image.mode,
            "size_bytes": file_path.stat().st_size,
        }
