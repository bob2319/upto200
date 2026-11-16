"""REST API routes."""

from __future__ import annotations

import logging
import uuid
import zipfile
from datetime import datetime, timezone
from io import BytesIO
from pathlib import Path
from typing import List

from fastapi import APIRouter, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, Response, StreamingResponse
from starlette.background import BackgroundTask

from api.preview import render_preview
from api.processing import start_job_processing
from api.schemas import HealthResponse, ImageResult, OptimizeResponse
from api.security import client_key, rate_limiter, read_and_validate_upload
from api.settings import JOB_TTL_SECONDS, MAX_FILES_PER_REQUEST
from api.storage import JobFile, job_store
from optimizer.config import MAX_SIZE_KB, MAX_WIDTH, MIN_QUALITY, START_QUALITY
from optimizer.service import normalize_output_format

router = APIRouter()
logger = logging.getLogger(__name__)

REDUCED_STATUSES = frozenset({"optimized", "target_not_reached"})


def _build_response(job) -> OptimizeResponse:
    results: List[ImageResult] = []
    reduced_count = 0
    error_count = 0
    not_smaller_count = 0

    for item in job.files:
        file_status = item.file_status or "pending"

        if item.error or file_status == "error":
            error_count += 1
            results.append(
                ImageResult(
                    file_id=item.file_id,
                    original_filename=item.original_filename,
                    status="error",
                    error=item.error or "Optimization failed",
                    optimization_status="error",
                )
            )
            continue

        if item.result is None:
            results.append(
                ImageResult(
                    file_id=item.file_id,
                    original_filename=item.original_filename,
                    status=file_status,
                    optimization_status=file_status,
                )
            )
            continue

        payload = item.result
        optimization_status = payload.get("optimization_status") or file_status
        size_reduced = bool(payload.get("size_reduced"))
        if optimization_status == "not_smaller" or not size_reduced:
            not_smaller_count += 1
        elif optimization_status in REDUCED_STATUSES:
            reduced_count += 1

        download_url = None
        if size_reduced and item.optimized_path and item.optimized_path.exists():
            download_url = f"/api/jobs/{job.job_id}/files/{item.file_id}"

        results.append(
            ImageResult(
                file_id=item.file_id,
                original_filename=item.original_filename,
                status=optimization_status,
                original_size_bytes=payload["original_size_bytes"],
                optimized_size_bytes=payload["optimized_size_bytes"],
                compression_percent=payload["compression_percent"],
                original_width=payload["original_width"],
                original_height=payload["original_height"],
                output_width=payload["output_width"],
                output_height=payload["output_height"],
                quality=payload["quality"],
                target_achieved=payload["target_achieved"],
                size_reduced=size_reduced,
                optimization_status=optimization_status,
                output_format=payload["output_format"],
                download_url=download_url,
            )
        )

    total = job.total_count or len(job.files)
    processed = job.processed_count
    if job.status == "completed":
        progress_percent = 100
    elif total <= 0:
        progress_percent = 0
    else:
        # Percent reflects completed files only — never animated/fake progress.
        progress_percent = int((processed * 100) // total)
        progress_percent = max(0, min(99, progress_percent))

    return OptimizeResponse(
        job_id=job.job_id,
        status=job.status,
        expires_at=job.expires_at,
        results=results,
        zip_url=f"/api/jobs/{job.job_id}/zip" if reduced_count else None,
        progress_percent=progress_percent,
        processed_count=processed,
        total_count=total,
        current_filename=job.current_filename or "",
        success_count=reduced_count,
        error_count=error_count,
        not_smaller_count=not_smaller_count,
    )


@router.get("/health", response_model=HealthResponse)
def health() -> HealthResponse:
    return HealthResponse()


@router.post("/preview")
async def preview_image(file: UploadFile = File(...)) -> Response:
    """Convert an uploaded image into a browser-displayable preview."""
    filename = file.filename or "upload"
    logger.info("preview request filename=%s", filename)
    try:
        data, _original_name, _stored_name = await read_and_validate_upload(file)
        preview_bytes, media_type = render_preview(data)
    except HTTPException:
        logger.warning("preview rejected filename=%s", filename)
        raise
    except Exception as exc:  # noqa: BLE001
        logger.exception("preview failed filename=%s", filename)
        raise HTTPException(
            status_code=400, detail=f"Preview unavailable: {exc}"
        ) from exc

    logger.info(
        "preview ok filename=%s bytes=%s media=%s",
        filename,
        len(preview_bytes),
        media_type,
    )
    return Response(
        content=preview_bytes,
        media_type=media_type,
        headers={"Cache-Control": "no-store"},
    )


@router.post("/optimize", response_model=OptimizeResponse)
async def optimize(
    request: Request,
    files: List[UploadFile] = File(...),
    target_size_kb: int = Form(MAX_SIZE_KB),
    max_width: int = Form(MAX_WIDTH),
    output_format: str = Form("webp"),
    start_quality: int = Form(START_QUALITY),
    min_quality: int = Form(MIN_QUALITY),
    step_quality: int = Form(5),
) -> OptimizeResponse:
    rate_limiter.check(client_key(request))

    if not files:
        raise HTTPException(status_code=400, detail="At least one image file is required.")
    if len(files) > MAX_FILES_PER_REQUEST:
        raise HTTPException(
            status_code=400,
            detail=f"Too many files. Maximum is {MAX_FILES_PER_REQUEST} per request.",
        )

    if target_size_kb < 1 or target_size_kb > 10_000:
        raise HTTPException(status_code=400, detail="target_size_kb must be between 1 and 10000.")
    if max_width < 16 or max_width > 10_000:
        raise HTTPException(status_code=400, detail="max_width must be between 16 and 10000.")
    if not (1 <= min_quality <= start_quality <= 100):
        raise HTTPException(
            status_code=400,
            detail="Require 1 <= min_quality <= start_quality <= 100.",
        )
    if step_quality < 1 or step_quality > 50:
        raise HTTPException(status_code=400, detail="step_quality must be between 1 and 50.")

    try:
        fmt = normalize_output_format(output_format)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    job = job_store.create_job(ttl_seconds=JOB_TTL_SECONDS)

    for upload in files:
        file_id = uuid.uuid4().hex
        try:
            data, original_name, stored_name = await read_and_validate_upload(upload)
            original_path = job.directory / "original" / stored_name
            original_path.write_bytes(data)
            job.files.append(
                JobFile(
                    file_id=file_id,
                    original_filename=original_name,
                    original_path=original_path,
                    file_status="pending",
                )
            )
        except HTTPException as exc:
            detail = exc.detail if isinstance(exc.detail, str) else str(exc.detail)
            filename = upload.filename or "upload"
            job.files.append(
                JobFile(
                    file_id=file_id,
                    original_filename=filename,
                    error=detail,
                    file_status="error",
                )
            )

    if not job.files:
        job_store.delete(job.job_id)
        raise HTTPException(status_code=400, detail="No files received.")

    options = {
        "max_size_kb": target_size_kb,
        "max_width": max_width,
        "min_quality": min_quality,
        "step_quality": step_quality,
        "start_quality": start_quality,
        "output_format": fmt,
    }

    job.status = "processing"
    job.total_count = len(job.files)
    job.processed_count = 0
    job.current_filename = ""
    job_store.save(job)
    logger.info(
        "optimize accepted job=%s files=%s format=%s target_kb=%s",
        job.job_id,
        len(job.files),
        fmt,
        target_size_kb,
    )
    start_job_processing(job.job_id, options)
    response = _build_response(job)
    logger.info(
        "optimize immediate response job=%s status=%s progress=%s%%",
        response.job_id,
        response.status,
        response.progress_percent,
    )
    return response


@router.get("/jobs/{job_id}", response_model=OptimizeResponse)
def get_job(job_id: str) -> OptimizeResponse:
    job = job_store.get(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="Job not found or expired.")
    response = _build_response(job)
    logger.info(
        "poll job=%s status=%s progress=%s%% processed=%s/%s current=%s",
        response.job_id,
        response.status,
        response.progress_percent,
        response.processed_count,
        response.total_count,
        response.current_filename,
    )
    return response


@router.get("/jobs/{job_id}/files/{file_id}")
def download_file(job_id: str, file_id: str):
    job = job_store.get(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="Job not found or expired.")

    match = next((item for item in job.files if item.file_id == file_id), None)
    if match is None or not match.optimized_path or not match.optimized_path.exists():
        raise HTTPException(status_code=404, detail="Optimized file not found.")

    size_reduced = bool(match.result and match.result.get("size_reduced"))
    if not size_reduced:
        raise HTTPException(
            status_code=404,
            detail="No reduced output available for this file.",
        )

    if not job_store.pin(job_id):
        raise HTTPException(status_code=404, detail="Job not found or expired.")

    stem = Path(match.original_filename).stem or "optimized"
    download_name = f"{stem}{match.optimized_path.suffix}"
    media = {
        ".webp": "image/webp",
        ".jpg": "image/jpeg",
        ".jpeg": "image/jpeg",
        ".png": "image/png",
    }.get(match.optimized_path.suffix.lower(), "application/octet-stream")

    return FileResponse(
        path=match.optimized_path,
        media_type=media,
        filename=download_name,
        background=BackgroundTask(job_store.unpin, job_id),
    )


@router.get("/jobs/{job_id}/zip")
def download_zip(job_id: str):
    job = job_store.get(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="Job not found or expired.")

    if not job_store.pin(job_id):
        raise HTTPException(status_code=404, detail="Job not found or expired.")

    try:
        successful = [
            item
            for item in job.files
            if item.optimized_path
            and item.optimized_path.exists()
            and not item.error
            and item.result
            and item.result.get("size_reduced")
        ]
        if not successful:
            raise HTTPException(status_code=404, detail="No optimized files available for ZIP.")

        buffer = BytesIO()
        used_names = set()
        with zipfile.ZipFile(buffer, "w", compression=zipfile.ZIP_DEFLATED) as archive:
            for item in successful:
                stem = Path(item.original_filename).stem or "optimized"
                name = f"{stem}{item.optimized_path.suffix}"
                name = Path(name).name
                if name in used_names:
                    name = f"{stem}_{item.file_id[:8]}{item.optimized_path.suffix}"
                used_names.add(name)
                archive.write(item.optimized_path, arcname=name)
    except Exception:
        job_store.unpin(job_id)
        raise

    buffer.seek(0)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    headers = {
        "Content-Disposition": f'attachment; filename="optimized-{stamp}.zip"'
    }
    return StreamingResponse(
        buffer,
        media_type="application/zip",
        headers=headers,
        background=BackgroundTask(job_store.unpin, job_id),
    )
