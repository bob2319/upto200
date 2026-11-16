"""Shared image loading helpers (preprocessing only; does not alter compression loops)."""

from __future__ import annotations

import io

from PIL import Image, ImageOps

try:
    import pillow_heif

    pillow_heif.register_heif_opener()
except ImportError:
    pass


def load_image(file_path: str) -> Image.Image:
    """Open an image and apply EXIF orientation so pixels match visual orientation."""
    img = Image.open(file_path)
    return ImageOps.exif_transpose(img) or img


def load_image_from_bytes(data: bytes) -> Image.Image:
    """Open image bytes and apply EXIF orientation."""
    img = Image.open(io.BytesIO(data))
    return ImageOps.exif_transpose(img) or img
