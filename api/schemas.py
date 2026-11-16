"""Pydantic response models."""

from __future__ import annotations

from typing import List, Optional

from pydantic import BaseModel, Field


class ImageResult(BaseModel):
    file_id: str
    original_filename: str
    status: str
    error: Optional[str] = None
    original_size_bytes: Optional[int] = None
    optimized_size_bytes: Optional[int] = None
    compression_percent: Optional[float] = None
    original_width: Optional[int] = None
    original_height: Optional[int] = None
    output_width: Optional[int] = None
    output_height: Optional[int] = None
    quality: Optional[int] = None
    target_achieved: Optional[bool] = None
    size_reduced: Optional[bool] = None
    optimization_status: Optional[str] = None
    output_format: Optional[str] = None
    download_url: Optional[str] = None


class OptimizeResponse(BaseModel):
    job_id: str
    status: str
    expires_at: float
    results: List[ImageResult]
    zip_url: Optional[str] = None
    progress_percent: int = 0
    processed_count: int = 0
    total_count: int = 0
    current_filename: str = ""
    success_count: int = 0
    error_count: int = 0
    not_smaller_count: int = 0


class HealthResponse(BaseModel):
    status: str = "ok"
    service: str = "upto200"


class ErrorResponse(BaseModel):
    detail: str = Field(..., examples=["Invalid or corrupted image: bad.bin"])
