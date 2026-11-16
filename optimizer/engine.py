"""Image optimization engine.

Core WebP quality loop preserved, with two correctness fixes:
1. Do not stop early when under the target but larger than the original.
2. Report the quality actually used for the encoded file on disk.
"""

from __future__ import annotations

import os
from typing import Tuple

from PIL import Image

from optimizer.config import (
    MAX_SIZE_KB,
    MAX_WIDTH,
    MIN_QUALITY,
    START_QUALITY,
    STEP_QUALITY,
)
from optimizer.imaging import load_image


def _save_webp(img: Image.Image, output_path: str, quality: int, has_alpha: bool) -> None:
    if has_alpha:
        # tradeoff: lossy storage with transparency
        img.save(output_path, "WEBP", quality=quality, method=6, lossless=False)
    else:
        img.save(output_path, "WEBP", quality=quality, method=6)


def compress_image(
    file_path: str,
    output_path: str,
    max_size_kb: int = MAX_SIZE_KB,
    max_width: int = MAX_WIDTH,
    min_quality: int = MIN_QUALITY,
    step_quality: int = STEP_QUALITY,
    start_quality: int = START_QUALITY,
) -> Tuple[float, int]:
    """Compress an image to WebP targeting a maximum file size in KB.

    Returns (size_kb, quality) for the encoded file on disk.
    """
    img = load_image(file_path)
    original_size_kb = os.path.getsize(file_path) / 1024

    # Resize proportionally
    if img.width > max_width:
        ratio = max_width / img.width
        new_size = (max_width, int(img.height * ratio))
        img = img.resize(new_size, Image.LANCZOS)

    # Definition of transparency
    has_alpha = img.mode in ("RGBA", "LA") or (img.mode == "P" and "transparency" in img.info)

    img = img.convert("RGBA" if has_alpha else "RGB")

    # Initial quality (for lossy)
    quality = start_quality
    best_size_kb = None
    best_quality = start_quality

    while quality >= min_quality:
        _save_webp(img, output_path, quality, has_alpha)
        size_kb = os.path.getsize(output_path) / 1024

        if best_size_kb is None or size_kb < best_size_kb:
            best_size_kb = size_kb
            best_quality = quality

        # Accept only when under target AND smaller than the original input.
        if size_kb <= max_size_kb and size_kb < original_size_kb:
            return size_kb, quality
        quality -= step_quality

    # Ensure the file on disk matches the best quality we observed.
    _save_webp(img, output_path, best_quality, has_alpha)
    return os.path.getsize(output_path) / 1024, best_quality
