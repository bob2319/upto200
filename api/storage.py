"""Temporary job storage using UUID directories."""

from __future__ import annotations

import json
import shutil
import threading
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional

from api.settings import JOB_TTL_SECONDS, TEMP_ROOT


@dataclass
class JobFile:
    file_id: str
    original_filename: str
    original_path: Optional[Path] = None
    optimized_path: Optional[Path] = None
    result: Optional[Dict[str, Any]] = None
    error: Optional[str] = None
    file_status: str = "pending"  # pending | processing | optimized | target_not_reached | not_smaller | error


@dataclass
class Job:
    job_id: str
    created_at: float
    expires_at: float
    directory: Path
    files: List[JobFile] = field(default_factory=list)
    status: str = "pending"
    processed_count: int = 0
    total_count: int = 0
    current_filename: str = ""

    def to_meta(self) -> Dict[str, Any]:
        return {
            "job_id": self.job_id,
            "created_at": self.created_at,
            "expires_at": self.expires_at,
            "status": self.status,
            "processed_count": self.processed_count,
            "total_count": self.total_count,
            "current_filename": self.current_filename,
            "files": [
                {
                    "file_id": item.file_id,
                    "original_filename": item.original_filename,
                    "original_path": str(item.original_path),
                    "optimized_path": str(item.optimized_path) if item.optimized_path else None,
                    "result": item.result,
                    "error": item.error,
                    "file_status": item.file_status,
                }
                for item in self.files
            ],
        }


class JobStore:
    def __init__(self) -> None:
        self._jobs: Dict[str, Job] = {}
        self._pins: Dict[str, int] = {}
        self._pending_delete: set[str] = set()
        self._lock = threading.Lock()
        TEMP_ROOT.mkdir(parents=True, exist_ok=True)

    def create_job(self, ttl_seconds: int = JOB_TTL_SECONDS) -> Job:
        job_id = str(uuid.uuid4())
        now = time.time()
        directory = TEMP_ROOT / job_id
        (directory / "original").mkdir(parents=True, exist_ok=True)
        (directory / "optimized").mkdir(parents=True, exist_ok=True)

        job = Job(
            job_id=job_id,
            created_at=now,
            expires_at=now + ttl_seconds,
            directory=directory,
        )
        with self._lock:
            self._jobs[job_id] = job
        self._write_meta(job)
        return job

    def get(self, job_id: str) -> Optional[Job]:
        with self._lock:
            job = self._jobs.get(job_id)
            pinned = self._pins.get(job_id, 0) > 0
        if job is None:
            return None
        if time.time() > job.expires_at and not pinned:
            self.delete(job_id)
            return None
        return job

    def save(self, job: Job) -> None:
        with self._lock:
            self._jobs[job.job_id] = job
        self._write_meta(job)

    def pin(self, job_id: str) -> bool:
        """Keep job files on disk while a download/ZIP is in progress."""
        with self._lock:
            if job_id not in self._jobs:
                return False
            self._pins[job_id] = self._pins.get(job_id, 0) + 1
            return True

    def unpin(self, job_id: str) -> None:
        should_delete = False
        with self._lock:
            count = self._pins.get(job_id, 0) - 1
            if count <= 0:
                self._pins.pop(job_id, None)
                if job_id in self._pending_delete:
                    self._pending_delete.discard(job_id)
                    should_delete = True
            else:
                self._pins[job_id] = count
        if should_delete:
            self.delete(job_id)

    def delete(self, job_id: str) -> bool:
        with self._lock:
            if self._pins.get(job_id, 0) > 0:
                self._pending_delete.add(job_id)
                return False
            self._pending_delete.discard(job_id)
            job = self._jobs.pop(job_id, None)
        if job is not None and job.directory.exists():
            shutil.rmtree(job.directory, ignore_errors=True)
            return True
        return False

    def cleanup_expired(self) -> int:
        now = time.time()
        with self._lock:
            expired = [job_id for job_id, job in self._jobs.items() if now > job.expires_at]
        removed = 0
        for job_id in expired:
            if self.delete(job_id):
                removed += 1

        # Also remove orphan directories (e.g. after restart).
        if TEMP_ROOT.exists():
            for path in TEMP_ROOT.iterdir():
                if not path.is_dir():
                    continue
                with self._lock:
                    known = path.name in self._jobs
                    pinned = self._pins.get(path.name, 0) > 0
                if known or pinned:
                    continue
                meta_path = path / "meta.json"
                if meta_path.exists():
                    try:
                        meta = json.loads(meta_path.read_text(encoding="utf-8"))
                        if now > float(meta.get("expires_at", 0)):
                            shutil.rmtree(path, ignore_errors=True)
                            removed += 1
                            continue
                    except (OSError, ValueError, TypeError, json.JSONDecodeError):
                        pass
                try:
                    age = now - path.stat().st_mtime
                except OSError:
                    continue
                if age > JOB_TTL_SECONDS:
                    shutil.rmtree(path, ignore_errors=True)
                    removed += 1
        return removed

    @staticmethod
    def _write_meta(job: Job) -> None:
        meta_path = job.directory / "meta.json"
        meta_path.write_text(json.dumps(job.to_meta(), indent=2), encoding="utf-8")


job_store = JobStore()
