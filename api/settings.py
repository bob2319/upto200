"""Runtime settings for the FastAPI service (env-overridable)."""

from __future__ import annotations

import os
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parents[1]
TEMP_ROOT = Path(os.getenv("OPTIMIZER_TEMP_ROOT", ROOT_DIR / "temp" / "jobs"))

MAX_UPLOAD_BYTES = int(os.getenv("OPTIMIZER_MAX_UPLOAD_BYTES", 15 * 1024 * 1024))
MAX_FILES_PER_REQUEST = int(os.getenv("OPTIMIZER_MAX_FILES", 20))
MAX_CONCURRENT_PROCESSES = int(os.getenv("OPTIMIZER_MAX_CONCURRENT", 2))
JOB_TTL_SECONDS = int(os.getenv("OPTIMIZER_JOB_TTL_SECONDS", 3600))
CLEANUP_INTERVAL_SECONDS = int(os.getenv("OPTIMIZER_CLEANUP_INTERVAL_SECONDS", 60))

RATE_LIMIT_REQUESTS = int(os.getenv("OPTIMIZER_RATE_LIMIT_REQUESTS", 30))
RATE_LIMIT_WINDOW_SECONDS = int(os.getenv("OPTIMIZER_RATE_LIMIT_WINDOW_SECONDS", 60))

# Public site origin for canonical/OG/sitemap. Leave empty on staging.
SITE_URL_ENV = os.getenv("SITE_URL", "").strip()
# Explicit indexability override: "true"/"false". Empty = auto (noindex for staging hosts).
SEO_INDEXABLE_ENV = os.getenv("SEO_INDEXABLE")

ALLOWED_INPUT_EXTENSIONS = {".jpg", ".jpeg", ".png", ".webp", ".heic", ".heif"}
ALLOWED_PIL_FORMATS = {"JPEG", "PNG", "WEBP", "HEIF", "HEIC"}
