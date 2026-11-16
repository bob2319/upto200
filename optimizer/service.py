"""High-level optimization helpers around the WebP engine."""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Optional, Tuple

from PIL import Image

from optimizer.config import (
    MAX_SIZE_KB,
    MAX_WIDTH,
    MIN_QUALITY,
    START_QUALITY,
    STEP_QUALITY,
)
from optimizer.engine import compress_image
from optimizer.imaging import load_image

OUTPUT_FORMATS = ("webp", "jpeg", "jpg", "png")

STATUS_OPTIMIZED = "optimized"
STATUS_TARGET_NOT_REACHED = "target_not_reached"
STATUS_NOT_SMALLER = "not_smaller"


@dataclass(frozen=True)
class OptimizeResult:
    original_filename: str
    original_size_bytes: int
    optimized_size_bytes: int
    compression_percent: float
    original_width: int
    original_height: int
    output_width: int
    output_height: int
    quality: int
    target_achieved: bool
    size_reduced: bool
    optimization_status: str
    output_format: str
    output_path: str


def normalize_output_format(output_format: str) -> str:
    fmt = output_format.lower().strip()
    if fmt == "jpg":
        return "jpeg"
    if fmt not in ("webp", "jpeg", "png"):
        raise ValueError(f"Unsupported output format: {output_format}")
    return fmt


def _normalize_format(output_format: str) -> str:
    return normalize_output_format(output_format)


def _prepare_image(
    file_path: str, max_width: int
) -> Tuple[Image.Image, bool, int, int]:
    img = load_image(file_path)
    original_width, original_height = img.size

    if img.width > max_width:
        ratio = max_width / img.width
        new_size = (max_width, int(img.height * ratio))
        img = img.resize(new_size, Image.LANCZOS)

    has_alpha = img.mode in ("RGBA", "LA") or (
        img.mode == "P" and "transparency" in img.info
    )
    return img, has_alpha, original_width, original_height


def _compress_jpeg(
    file_path: str,
    output_path: str,
    max_size_kb: int,
    max_width: int,
    min_quality: int,
    step_quality: int,
    start_quality: int,
) -> Tuple[float, int, int, int]:
    img, has_alpha, original_width, original_height = _prepare_image(
        file_path, max_width
    )
    if has_alpha:
        background = Image.new("RGB", img.size, (255, 255, 255))
        rgba = img.convert("RGBA")
        background.paste(rgba, mask=rgba.split()[3])
        img = background
    else:
        img = img.convert("RGB")

    original_size_kb = os.path.getsize(file_path) / 1024
    quality = start_quality
    best_size_kb = None
    best_quality = start_quality
    while quality >= min_quality:
        img.save(output_path, "JPEG", quality=quality, optimize=True)
        size_kb = os.path.getsize(output_path) / 1024
        if best_size_kb is None or size_kb < best_size_kb:
            best_size_kb = size_kb
            best_quality = quality
        if size_kb <= max_size_kb and size_kb < original_size_kb:
            return size_kb, quality, original_width, original_height
        quality -= step_quality

    img.save(output_path, "JPEG", quality=best_quality, optimize=True)
    return os.path.getsize(output_path) / 1024, best_quality, original_width, original_height


def _compress_png(
    file_path: str,
    output_path: str,
    max_size_kb: int,
    max_width: int,
    min_quality: int,
    step_quality: int,
    start_quality: int,
) -> Tuple[float, int, int, int]:
    """PNG size targeting via increasing compress_level (0 → 9)."""
    del min_quality, step_quality, start_quality
    img, has_alpha, original_width, original_height = _prepare_image(
        file_path, max_width
    )
    img = img.convert("RGBA" if has_alpha else "RGB")

    original_size_kb = os.path.getsize(file_path) / 1024
    best_level = 0
    best_size_kb = None
    for level in range(0, 10):
        img.save(output_path, "PNG", optimize=True, compress_level=level)
        size_kb = os.path.getsize(output_path) / 1024
        if best_size_kb is None or size_kb < best_size_kb:
            best_size_kb = size_kb
            best_level = level
        if size_kb <= max_size_kb and size_kb < original_size_kb:
            return size_kb, level, original_width, original_height

    img.save(output_path, "PNG", optimize=True, compress_level=best_level)
    return os.path.getsize(output_path) / 1024, best_level, original_width, original_height


def _classify_result(
    original_size_bytes: int, optimized_size_bytes: int, max_size_kb: int
) -> Tuple[bool, bool, float, str]:
    size_reduced = optimized_size_bytes < original_size_bytes
    target_achieved = (optimized_size_bytes / 1024) <= max_size_kb

    if size_reduced:
        compression_percent = round(
            (1 - (optimized_size_bytes / original_size_bytes)) * 100, 2
        )
        if target_achieved:
            return True, True, compression_percent, STATUS_OPTIMIZED
        return True, False, compression_percent, STATUS_TARGET_NOT_REACHED

    # No real reduction — never report negative percentages.
    return False, target_achieved, 0.0, STATUS_NOT_SMALLER


def optimize_image(
    file_path: str,
    output_path: str,
    *,
    original_filename: Optional[str] = None,
    max_size_kb: int = MAX_SIZE_KB,
    max_width: int = MAX_WIDTH,
    min_quality: int = MIN_QUALITY,
    step_quality: int = STEP_QUALITY,
    start_quality: int = START_QUALITY,
    output_format: str = "webp",
) -> OptimizeResult:
    fmt = _normalize_format(output_format)
    original_size_bytes = os.path.getsize(file_path)
    name = original_filename or os.path.basename(file_path)

    probe = load_image(file_path)
    try:
        original_width, original_height = probe.size
    finally:
        probe.close()

    if fmt == "webp":
        _size_kb, quality = compress_image(
            file_path,
            output_path,
            max_size_kb=max_size_kb,
            max_width=max_width,
            min_quality=min_quality,
            step_quality=step_quality,
            start_quality=start_quality,
        )
    elif fmt == "jpeg":
        _size_kb, quality, original_width, original_height = _compress_jpeg(
            file_path,
            output_path,
            max_size_kb,
            max_width,
            min_quality,
            step_quality,
            start_quality,
        )
    else:
        _size_kb, quality, original_width, original_height = _compress_png(
            file_path,
            output_path,
            max_size_kb,
            max_width,
            min_quality,
            step_quality,
            start_quality,
        )

    with Image.open(output_path) as out_img:
        output_width, output_height = out_img.size

    optimized_size_bytes = os.path.getsize(output_path)
    size_reduced, target_achieved, compression_percent, optimization_status = (
        _classify_result(original_size_bytes, optimized_size_bytes, max_size_kb)
    )

    return OptimizeResult(
        original_filename=name,
        original_size_bytes=original_size_bytes,
        optimized_size_bytes=optimized_size_bytes,
        compression_percent=compression_percent,
        original_width=original_width,
        original_height=original_height,
        output_width=output_width,
        output_height=output_height,
        quality=quality,
        target_achieved=target_achieved,
        size_reduced=size_reduced,
        optimization_status=optimization_status,
        output_format=fmt,
        output_path=output_path,
    )
