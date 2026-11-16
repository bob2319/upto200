"""Verification tests: PNG transparency, target_achieved, download pinning."""

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
from api.storage import job_store
from optimizer.service import optimize_image
from tests.helpers import REDUCED_STATUSES, optimize_and_wait


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


def _transparent_png_bytes(width: int = 120, height: int = 80) -> bytes:
    image = Image.new("RGBA", (width, height), (0, 0, 0, 0))
    for x in range(20, 60):
        for y in range(20, 60):
            image.putpixel((x, y), (220, 40, 40, 255))
    for x in range(70, 100):
        for y in range(20, 50):
            image.putpixel((x, y), (40, 120, 220, 128))
    buffer = io.BytesIO()
    image.save(buffer, format="PNG")
    return buffer.getvalue()


def _noisy_png_bytes(width: int = 800, height: int = 600) -> bytes:
    import random

    image = Image.new("RGB", (width, height))
    pixels = image.load()
    rng = random.Random(42)
    for y in range(height):
        for x in range(width):
            pixels[x, y] = (rng.randint(0, 255), rng.randint(0, 255), rng.randint(0, 255))
    buffer = io.BytesIO()
    image.save(buffer, format="PNG", compress_level=0)
    return buffer.getvalue()


def test_png_output_preserves_transparency(client: TestClient, tmp_path: Path):
    source = tmp_path / "alpha-source.png"
    source.write_bytes(_transparent_png_bytes())

    out = tmp_path / "alpha-out.png"
    result = optimize_image(
        str(source),
        str(out),
        output_format="png",
        max_size_kb=500,
        max_width=1920,
    )

    with Image.open(out) as optimized:
        assert optimized.mode in ("RGBA", "LA", "PA")
        assert optimized.getpixel((0, 0))[3] == 0
        assert optimized.getpixel((30, 30))[3] == 255
        assert 0 < optimized.getpixel((80, 30))[3] < 255

    _response, payload = optimize_and_wait(
        client,
        files=[("files", ("alpha.png", source.read_bytes(), "image/png"))],
        data={"output_format": "png", "target_size_kb": 500},
    )
    item = payload["results"][0]
    assert item["status"] in REDUCED_STATUSES
    assert item["output_format"] == "png"

    download = client.get(item["download_url"])
    assert download.status_code == 200
    with Image.open(io.BytesIO(download.content)) as downloaded:
        assert downloaded.mode in ("RGBA", "LA", "PA")
        assert downloaded.getpixel((0, 0))[3] == 0


def test_target_achieved_false_when_size_unreachable(client: TestClient, tmp_path: Path):
    source = tmp_path / "noisy.png"
    source.write_bytes(_noisy_png_bytes())

    _response, payload = optimize_and_wait(
        client,
        files=[("files", ("noisy.png", source.read_bytes(), "image/png"))],
        data={
            "output_format": "webp",
            "target_size_kb": 1,
            "max_width": 800,
            "start_quality": 90,
            "min_quality": 40,
            "step_quality": 5,
        },
    )
    item = payload["results"][0]
    assert item["status"] in ("target_not_reached", "not_smaller")
    assert item["optimized_size_bytes"] > 1024
    assert item["target_achieved"] is False
    assert item["quality"] == 40
    if item["status"] == "target_not_reached":
        assert item["size_reduced"] is True
        assert item["download_url"]
    else:
        assert item["size_reduced"] is False
        assert item["download_url"] is None


def test_cleanup_skips_pinned_downloads(tmp_path, monkeypatch):
    monkeypatch.setattr("api.storage.TEMP_ROOT", tmp_path / "jobs")
    (tmp_path / "jobs").mkdir(parents=True, exist_ok=True)
    job_store._jobs.clear()
    job_store._pins.clear()
    job_store._pending_delete.clear()

    job = job_store.create_job(ttl_seconds=1)
    marker = job.directory / "optimized" / "keep.webp"
    marker.write_bytes(b"RIFF....WEBP")

    assert job_store.pin(job.job_id)
    job.expires_at = 0
    removed = job_store.cleanup_expired()
    assert removed == 0
    assert marker.exists()
    assert job.job_id in job_store._jobs
    assert job.job_id in job_store._pending_delete

    job_store.unpin(job.job_id)
    assert not marker.exists()
    assert job.job_id not in job_store._jobs
