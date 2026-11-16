"""Browser-safe preview rendering (does not alter optimization inputs)."""

from __future__ import annotations

import io
from typing import Tuple

from PIL import Image

from optimizer.imaging import load_image_from_bytes

PREVIEW_MAX_EDGE = 960


def render_preview(data: bytes, max_edge: int = PREVIEW_MAX_EDGE) -> Tuple[bytes, str]:
    """Return (bytes, media_type) suitable for <img> tags in common browsers.

    HEIC/HEIF and other non-browser formats are converted.
    Alpha-channel images are exported as PNG; otherwise JPEG.
    """
    img = load_image_from_bytes(data)
    try:
        has_alpha = img.mode in ("RGBA", "LA") or (
            img.mode == "P" and "transparency" in img.info
        )
        img = img.convert("RGBA" if has_alpha else "RGB")

        width, height = img.size
        longest = max(width, height)
        if longest > max_edge:
            scale = max_edge / float(longest)
            img = img.resize(
                (max(1, int(width * scale)), max(1, int(height * scale))),
                Image.LANCZOS,
            )

        buffer = io.BytesIO()
        if has_alpha:
            img.save(buffer, format="PNG", optimize=True)
            return buffer.getvalue(), "image/png"

        img.save(buffer, format="JPEG", quality=85, optimize=True)
        return buffer.getvalue(), "image/jpeg"
    finally:
        img.close()
