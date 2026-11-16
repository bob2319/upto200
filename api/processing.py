"""Sequential image processing with live job progress updates."""

from __future__ import annotations

import logging
import threading
from pathlib import Path
from typing import Any, Dict

from api.storage import Job, job_store
from optimizer.service import OptimizeResult, optimize_image

logger = logging.getLogger(__name__)


def _process_one(job_file, output_path: Path, options: Dict[str, Any]) -> None:
    try:
        result: OptimizeResult = optimize_image(
            str(job_file.original_path),
            str(output_path),
            original_filename=job_file.original_filename,
            **options,
        )
        job_file.optimized_path = output_path
        job_file.result = {
            "original_size_bytes": result.original_size_bytes,
            "optimized_size_bytes": result.optimized_size_bytes,
            "compression_percent": result.compression_percent,
            "original_width": result.original_width,
            "original_height": result.original_height,
            "output_width": result.output_width,
            "output_height": result.output_height,
            "quality": result.quality,
            "target_achieved": result.target_achieved,
            "size_reduced": result.size_reduced,
            "optimization_status": result.optimization_status,
            "output_format": result.output_format,
        }
        job_file.error = None
        job_file.file_status = result.optimization_status
    except Exception as exc:  # noqa: BLE001
        logger.exception("Failed to optimize %s", job_file.original_filename)
        job_file.error = str(exc)
        job_file.result = None
        job_file.optimized_path = None
        job_file.file_status = "error"


def process_job_sequential(job: Job, options: Dict[str, Any]) -> Job:
    """Process files one-by-one and persist progress after each file."""
    job.status = "processing"
    job.total_count = len(job.files)
    job.processed_count = 0
    job.current_filename = ""
    job_store.save(job)
    logger.info(
        "job %s: sequential processing started (%s files)",
        job.job_id,
        job.total_count,
    )

    for index, job_file in enumerate(job.files, start=1):
        if job_file.error or job_file.original_path is None:
            job_file.file_status = "error"
            job.processed_count += 1
            job.current_filename = job_file.original_filename
            job_store.save(job)
            logger.info(
                "job %s: skip/error file %s/%s %s",
                job.job_id,
                index,
                job.total_count,
                job_file.original_filename,
            )
            continue

        job_file.file_status = "processing"
        job.current_filename = job_file.original_filename
        job_store.save(job)
        logger.info(
            "job %s: start file %s/%s %s",
            job.job_id,
            index,
            job.total_count,
            job_file.original_filename,
        )

        ext = {
            "webp": ".webp",
            "jpeg": ".jpg",
            "png": ".png",
        }[options["output_format"]]
        output_path = job.directory / "optimized" / f"{job_file.file_id}{ext}"
        _process_one(job_file, output_path, options)

        job.processed_count += 1
        job_store.save(job)
        logger.info(
            "job %s: finished file %s/%s %s status=%s processed=%s",
            job.job_id,
            index,
            job.total_count,
            job_file.original_filename,
            job_file.file_status,
            job.processed_count,
        )

    job.current_filename = ""
    job.status = "completed"
    job_store.save(job)
    logger.info("job %s: completed", job.job_id)
    return job


def start_job_processing(job_id: str, options: Dict[str, Any]) -> None:
    def runner() -> None:
        logger.info("job %s: background worker started thread=%s", job_id, threading.current_thread().name)
        job = job_store.get(job_id)
        if job is None:
            logger.error("job %s: missing from store when worker started", job_id)
            return
        try:
            process_job_sequential(job, options)
        except Exception:  # noqa: BLE001
            logger.exception("Job %s failed", job_id)
            job = job_store.get(job_id)
            if job is not None:
                job.status = "completed"
                job_store.save(job)

    thread = threading.Thread(target=runner, name=f"optimize-{job_id[:8]}", daemon=True)
    thread.start()
    logger.info("job %s: processing thread launched", job_id)
