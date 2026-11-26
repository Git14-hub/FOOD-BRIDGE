"""Safe file upload handling: extension whitelist, magic-byte check, random names."""

from __future__ import annotations

import os
import uuid
from pathlib import Path

from werkzeug.datastructures import FileStorage

ALLOWED_EXTENSIONS = {".jpg", ".jpeg", ".png", ".webp"}
MAX_IMAGE_BYTES = 5 * 1024 * 1024  # 5 MB


def _detect_image_type(header: bytes) -> str | None:
    """Verify image format via file magic bytes."""
    if header.startswith(b"\xff\xd8\xff"):
        return "jpeg"
    if header.startswith(b"\x89PNG\r\n\x1a\n"):
        return "png"
    if len(header) >= 12 and header[:4] == b"RIFF" and header[8:12] == b"WEBP":
        return "webp"
    return None


def save_food_image(file_storage: FileStorage | None, upload_folder: str) -> tuple[str | None, str | None]:
    """Validate and save an uploaded food photo.

    Returns (relative_static_path, error_message). If no file was provided, returns (None, None).
    """
    if not file_storage or not file_storage.filename:
        return None, None

    ext = Path(file_storage.filename).suffix.lower()
    if ext not in ALLOWED_EXTENSIONS:
        return None, "Allowed image formats: JPG, JPEG, PNG, or WEBP."

    stream = file_storage.stream
    stream.seek(0)
    data = stream.read(MAX_IMAGE_BYTES + 1)
    stream.seek(0)

    if not data:
        return None, "Uploaded image file is empty."
    if len(data) > MAX_IMAGE_BYTES:
        return None, "Image must be 5 MB or smaller."

    detected = _detect_image_type(data[:16])
    expected_map = {
        ".jpg": "jpeg",
        ".jpeg": "jpeg",
        ".png": "png",
        ".webp": "webp",
    }
    if detected is None or detected != expected_map[ext]:
        return None, "File content does not match a valid JPG, PNG, or WEBP image."

    os.makedirs(upload_folder, exist_ok=True)
    normalized_ext = ".jpg" if detected == "jpeg" else f".{detected}"
    filename = f"{uuid.uuid4().hex}{normalized_ext}"
    dest = Path(upload_folder) / filename
    dest.write_bytes(data)

    return f"uploads/{filename}", None
