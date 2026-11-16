"""Upload validation, safe filenames, and simple in-memory rate limiting."""

from __future__ import annotations

import re
import time
import uuid
from collections import defaultdict, deque
from io import BytesIO
from pathlib import Path
from threading import Lock
from typing import Deque, Dict, Optional, Tuple

from fastapi import HTTPException, Request, UploadFile
from PIL import Image

from api.settings import (
    ALLOWED_INPUT_EXTENSIONS,
    ALLOWED_PIL_FORMATS,
    MAX_UPLOAD_BYTES,
    RATE_LIMIT_REQUESTS,
    RATE_LIMIT_WINDOW_SECONDS,
)

try:
    import pillow_heif

    pillow_heif.register_heif_opener()
except ImportError:
    pass

# Allow unicode letters/digits in stems; spaces and other symbols become underscores.
_SAFE_STEM_RE = re.compile(r"[^\w\-]+", re.UNICODE)

PIL_FORMAT_TO_EXTENSION = {
    "JPEG": ".jpg",
    "PNG": ".png",
    "WEBP": ".webp",
    "HEIF": ".heic",
    "HEIC": ".heic",
}


class RateLimiter:
    def __init__(
        self,
        max_requests: int = RATE_LIMIT_REQUESTS,
        window_seconds: int = RATE_LIMIT_WINDOW_SECONDS,
    ) -> None:
        self.max_requests = max_requests
        self.window_seconds = window_seconds
        self._hits: Dict[str, Deque[float]] = defaultdict(deque)
        self._lock = Lock()

    def check(self, key: str) -> None:
        now = time.time()
        with self._lock:
            bucket = self._hits[key]
            while bucket and now - bucket[0] > self.window_seconds:
                bucket.popleft()
            if len(bucket) >= self.max_requests:
                raise HTTPException(status_code=429, detail="Rate limit exceeded. Try again later.")
            bucket.append(now)


rate_limiter = RateLimiter()


def client_key(request: Request) -> str:
    forwarded = request.headers.get("x-forwarded-for")
    if forwarded:
        return forwarded.split(",")[0].strip()
    if request.client:
        return request.client.host
    return "unknown"


def declared_extension(filename: Optional[str]) -> str:
    """Return a normalized extension from the raw client filename, if allowed."""
    if not filename:
        return ""
    suffix = Path(Path(filename.replace("\\", "/")).name).suffix.lower()
    if suffix == ".jpeg":
        suffix = ".jpg"
    return suffix if suffix in ALLOWED_INPUT_EXTENSIONS else ""


def sanitize_filename(filename: str, max_length: int = 120) -> str:
    """Sanitize a display/storage basename while preserving a real extension.

    Truncation applies to the stem only so long names cannot lose `.jpg`.
    """
    raw = Path((filename or "upload").replace("\\", "/")).name.replace("\x00", "")
    original_suffix = Path(raw).suffix
    stem = raw[: -len(original_suffix)] if original_suffix else raw

    stem = _SAFE_STEM_RE.sub("_", stem).strip("._") or "upload"

    suffix = original_suffix.lower()
    if suffix == ".jpeg":
        suffix = ".jpg"
    if suffix and suffix not in ALLOWED_INPUT_EXTENSIONS:
        # Keep only simple alphanumeric suffixes for display; content decides acceptance.
        if not re.fullmatch(r"\.[A-Za-z0-9]{1,10}", original_suffix or ""):
            suffix = ""
        else:
            suffix = original_suffix.lower()

    max_stem_len = max(1, max_length - len(suffix))
    stem = stem[:max_stem_len].rstrip("._") or "upload"
    return f"{stem}{suffix}"


def randomized_stored_name(extension: str) -> str:
    suffix = extension.lower()
    if suffix == ".jpeg":
        suffix = ".jpg"
    if suffix not in ALLOWED_INPUT_EXTENSIONS:
        suffix = ""
    return f"{uuid.uuid4().hex}{suffix}"


def detect_image_format(data: bytes) -> str:
    try:
        with Image.open(BytesIO(data)) as img:
            img.verify()
        with Image.open(BytesIO(data)) as img:
            return (img.format or "").upper()
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=400, detail="Invalid or corrupted image") from exc


async def read_and_validate_upload(upload: UploadFile) -> Tuple[bytes, str, str]:
    raw_name = upload.filename or "upload"
    display_name = sanitize_filename(raw_name)

    data = await upload.read()
    if not data:
        raise HTTPException(status_code=400, detail=f"Empty file: {display_name}")
    if len(data) > MAX_UPLOAD_BYTES:
        raise HTTPException(
            status_code=400,
            detail=f"File too large: {display_name}. Max {MAX_UPLOAD_BYTES} bytes.",
        )

    image_format = detect_image_format(data)
    if image_format not in ALLOWED_PIL_FORMATS:
        raise HTTPException(
            status_code=400,
            detail=f"Unsupported image content type for {display_name}: {image_format or 'unknown'}",
        )

    content_ext = PIL_FORMAT_TO_EXTENSION.get(image_format, "")
    name_ext = declared_extension(raw_name)

    # Prefer a valid declared extension; otherwise trust detected content.
    # This accepts missing/mangled extensions when Pillow confirms an allowed image.
    if name_ext:
        extension = name_ext
    elif content_ext:
        extension = content_ext
    else:
        raise HTTPException(
            status_code=400,
            detail=(
                f"Unsupported file extension ''. Allowed: {sorted(ALLOWED_INPUT_EXTENSIONS)}"
            ),
        )

    if not Path(display_name).suffix:
        display_name = f"{display_name}{extension}"
    elif Path(display_name).suffix.lower() not in ALLOWED_INPUT_EXTENSIONS:
        display_name = f"{Path(display_name).stem}{extension}"

    return data, display_name, randomized_stored_name(extension)
