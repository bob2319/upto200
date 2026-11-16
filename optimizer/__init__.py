"""Reusable image optimization engine."""

from optimizer.config import (
    INPUT_FOLDER,
    MAX_SIZE_KB,
    MAX_WIDTH,
    MIN_QUALITY,
    OUTPUT_FOLDER,
    START_QUALITY,
    STEP_QUALITY,
    SUPPORTED_EXTENSIONS,
)
from optimizer.engine import compress_image
from optimizer.service import OptimizeResult, optimize_image

__all__ = [
    "compress_image",
    "optimize_image",
    "OptimizeResult",
    "INPUT_FOLDER",
    "OUTPUT_FOLDER",
    "MAX_SIZE_KB",
    "MAX_WIDTH",
    "START_QUALITY",
    "MIN_QUALITY",
    "STEP_QUALITY",
    "SUPPORTED_EXTENSIONS",
]
