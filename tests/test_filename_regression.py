"""Regression tests for JPEG uploads with awkward filenames."""

from __future__ import annotations

import io
import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from PIL import Image

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from api.main import app
from api.security import declared_extension, sanitize_filename
from api.storage import job_store
from tests.helpers import REDUCED_STATUSES, optimize_and_wait


def _jpeg_bytes(size: tuple[int, int] = (120, 80), color=(30, 90, 160)) -> bytes:
    image = Image.new("RGB", size, color)
    buffer = io.BytesIO()
    image.save(buffer, format="JPEG", quality=90)
    return buffer.getvalue()


@pytest.fixture()
def client(tmp_path, monkeypatch):
    monkeypatch.setattr("api.settings.TEMP_ROOT", tmp_path / "jobs")
    monkeypatch.setattr("api.storage.TEMP_ROOT", tmp_path / "jobs")
    monkeypatch.setattr("api.main.TEMP_ROOT", tmp_path / "jobs")
    monkeypatch.setattr("api.security.rate_limiter.max_requests", 10_000)
    from api.security import rate_limiter

    rate_limiter._hits.clear()
    job_store._jobs.clear()
    job_store._pins.clear()
    job_store._pending_delete.clear()
    (tmp_path / "jobs").mkdir(parents=True, exist_ok=True)
    with TestClient(app) as test_client:
        yield test_client


def test_sanitize_preserves_extension_for_long_names():
    long_name = ("xM8KF5_dqYP57t8kQkTybS3QijVnVj472BeC9slC8EYNO-Jq21-93jJDYefivvDvViBLKDfjm5F3hgBoGuJMjdRtuc9UG4sn_dh3Ockhg9PJfPXyZ4Qqf_5yAsku_qfpxAuhT6m7YWl4lrs73L0zjiAyty2LiUB7daacfK2QXoF3lyzVMWiQIsY4aIH4-qTX.jpg")
    assert len(long_name) > 120
    safe = sanitize_filename(long_name)
    assert len(safe) <= 120
    assert Path(safe).suffix == ".jpg"
    assert declared_extension(long_name) == ".jpg"


def test_sanitize_unicode_and_spaces_keep_jpeg_extension():
    safe = sanitize_filename("праздник фото (1).JPEG")
    assert Path(safe).suffix == ".jpg"
    assert " " not in safe


@pytest.mark.parametrize(
    "filename",
    [
        "photo.jpg",
        "holiday photo (2).jpeg",
        "IMG_UPPER.JPG",
        "shot.JPEG",
        "A" * 180 + ".jpg",
        "фото_отпуск.jpg",
        "no-extension-name",
    ],
)
def test_jpeg_uploads_with_awkward_filenames(client: TestClient, filename: str):
    payload_bytes = _jpeg_bytes()
    _response, payload = optimize_and_wait(
        client,
        files=[("files", (filename, payload_bytes, "image/jpeg"))],
        data={"output_format": "webp", "target_size_kb": 150},
    )
    item = payload["results"][0]
    assert item["status"] in REDUCED_STATUSES | {"not_smaller"}, item
    assert item["output_format"] == "webp"
    assert item["optimized_size_bytes"] > 0


def test_batch_of_six_long_jpeg_names(client: TestClient):
    files = []
    for index in range(6):
        name = (
            f"PgvQ7vxj1S0pkk6JzuJ0ur0VI3r4U1VVY_XltA6CGDM30c9K59A0f3GbaCXWxsPUhFFC4XJR81gTJZKD6QCzg1dDeCJ_Qty4SdtOVqZdoO5l8FzY6HNF9zGcqLxHerVtfmXTH9J5TuGKfmQjI-yMGmuzGTd3DRnWIZxHQD8ELNwihHDKA9cHmqv4szeb_ZwS_{index}.jpg"
        )
        files.append(("files", (name, _jpeg_bytes((140 + index, 90)), "image/jpeg")))

    _response, payload = optimize_and_wait(
        client,
        files=files,
        data={"output_format": "webp", "target_size_kb": 150},
    )
    results = payload["results"]
    assert len(results) == 6
    assert all(item["status"] in REDUCED_STATUSES | {"not_smaller"} for item in results), results
