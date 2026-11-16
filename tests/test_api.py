"""API tests for optimize, validation, multi-upload, and downloads."""

from __future__ import annotations

import io
import sys
import zipfile
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from PIL import Image

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from api.main import app
from tests.helpers import REDUCED_STATUSES, optimize_and_wait, wait_for_job

IMAGES_DIR = ROOT / "images"


@pytest.fixture()
def client(tmp_path, monkeypatch):
    monkeypatch.setattr("api.settings.TEMP_ROOT", tmp_path / "jobs")
    monkeypatch.setattr("api.storage.TEMP_ROOT", tmp_path / "jobs")
    monkeypatch.setattr("api.main.TEMP_ROOT", tmp_path / "jobs")
    monkeypatch.setattr("api.security.rate_limiter.max_requests", 10_000)
    from api.security import rate_limiter
    from api.storage import job_store

    rate_limiter._hits.clear()
    job_store._jobs.clear()
    job_store._pins.clear()
    job_store._pending_delete.clear()
    (tmp_path / "jobs").mkdir(parents=True, exist_ok=True)

    with TestClient(app) as test_client:
        yield test_client


def _png_bytes(width: int = 64, height: int = 64, color=(30, 144, 255, 128)) -> bytes:
    image = Image.new("RGBA", (width, height), color)
    buffer = io.BytesIO()
    image.save(buffer, format="PNG")
    return buffer.getvalue()


def _sample_path() -> Path:
    samples = sorted(IMAGES_DIR.glob("*.png"))
    assert samples, "Expected sample PNGs in images/"
    small = IMAGES_DIR / "6.png"
    return small if small.exists() else samples[0]


def test_health(client: TestClient):
    response = client.get("/api/health")
    assert response.status_code == 200
    assert response.json()["status"] == "ok"
    assert response.json()["service"] == "upto200"


def test_optimize_single_image_success(client: TestClient):
    sample = _sample_path()
    with sample.open("rb") as handle:
        _response, payload = optimize_and_wait(
            client,
            files=[("files", (sample.name, handle, "image/png"))],
            data={
                "target_size_kb": 150,
                "max_width": 1920,
                "output_format": "webp",
                "start_quality": 90,
                "min_quality": 40,
            },
        )

    assert payload["job_id"]
    assert payload["status"] == "completed"
    assert len(payload["results"]) == 1
    assert payload["progress_percent"] == 100

    result = payload["results"][0]
    assert result["status"] in REDUCED_STATUSES
    assert result["original_filename"] == sample.name
    assert result["optimized_size_bytes"] > 0
    assert result["original_size_bytes"] > 0
    assert result["output_format"] == "webp"
    assert result["quality"] is not None
    assert isinstance(result["target_achieved"], bool)
    assert result["size_reduced"] is True
    assert result["download_url"]

    download = client.get(result["download_url"])
    assert download.status_code == 200
    assert download.headers["content-type"].startswith("image/")
    assert download.content[:4] == b"RIFF"


def test_optimize_multiple_uploads(client: TestClient):
    sample = _sample_path()
    generated = _png_bytes(80, 40)
    with sample.open("rb") as handle:
        _response, payload = optimize_and_wait(
            client,
            files=[
                ("files", (sample.name, handle, "image/png")),
                ("files", ("generated.png", generated, "image/png")),
            ],
            data={"target_size_kb": 150, "output_format": "webp"},
        )

    assert len(payload["results"]) == 2
    assert all(item["status"] in REDUCED_STATUSES for item in payload["results"])
    assert payload["zip_url"]
    assert payload["success_count"] == 2

    zip_response = client.get(payload["zip_url"])
    assert zip_response.status_code == 200
    assert zip_response.headers["content-type"] == "application/zip"

    with zipfile.ZipFile(io.BytesIO(zip_response.content)) as archive:
        names = archive.namelist()
        assert len(names) == 2
        assert all(".." not in name and not name.startswith("/") for name in names)


def test_invalid_file_reported_in_results(client: TestClient):
    _response, payload = optimize_and_wait(
        client,
        files=[("files", ("notes.txt", b"not-an-image", "text/plain"))],
        data={"output_format": "webp"},
    )
    result = payload["results"][0]
    assert result["status"] == "error"
    assert "Invalid or corrupted image" in result["error"]
    assert payload["zip_url"] is None


def test_corrupted_image_reported_in_results(client: TestClient):
    _response, payload = optimize_and_wait(
        client,
        files=[("files", ("broken.png", b"\x89PNG\r\n\x1a\n\x00\x00corrupted", "image/png"))],
        data={"output_format": "webp"},
    )
    result = payload["results"][0]
    assert result["status"] == "error"
    assert "Invalid or corrupted image" in result["error"]


def test_mixed_valid_and_invalid_uploads(client: TestClient):
    sample = _sample_path()
    with sample.open("rb") as handle:
        _response, payload = optimize_and_wait(
            client,
            files=[
                ("files", (sample.name, handle, "image/png")),
                ("files", ("notes.txt", b"nope", "text/plain")),
            ],
            data={"output_format": "webp"},
        )
    results = payload["results"]
    assert results[0]["status"] in REDUCED_STATUSES
    assert results[1]["status"] == "error"
    assert payload["zip_url"]
    assert payload["success_count"] == 1
    assert payload["error_count"] == 1


def test_get_job_and_missing_job(client: TestClient):
    sample = _sample_path()
    with sample.open("rb") as handle:
        created = client.post(
            "/api/optimize",
            files=[("files", (sample.name, handle, "image/png"))],
            data={"output_format": "webp"},
        )
    job_id = created.json()["job_id"]
    payload = wait_for_job(client, job_id)

    fetched = client.get(f"/api/jobs/{job_id}")
    assert fetched.status_code == 200
    assert fetched.json()["job_id"] == job_id
    assert payload["status"] == "completed"

    missing = client.get("/api/jobs/00000000-0000-0000-0000-000000000000")
    assert missing.status_code == 404


def test_jpeg_output_format(client: TestClient):
    data = _png_bytes(100, 100, (255, 0, 0, 255))
    _response, payload = optimize_and_wait(
        client,
        files=[("files", ("red.png", data, "image/png"))],
        data={"output_format": "jpeg", "target_size_kb": 50},
    )
    result = payload["results"][0]
    assert result["status"] in REDUCED_STATUSES | {"not_smaller"}
    assert result["output_format"] == "jpeg"
    if result["size_reduced"]:
        download = client.get(result["download_url"])
        assert download.status_code == 200
        assert download.content[:2] == b"\xff\xd8"
