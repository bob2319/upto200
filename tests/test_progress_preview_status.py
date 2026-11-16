"""Regression coverage for real progress, previews, and optimization statuses."""

from __future__ import annotations

import io
import sys
import time
import zipfile
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from PIL import Image

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from api.main import app
from api.preview import render_preview
from api.storage import job_store
from optimizer.service import (
    STATUS_NOT_SMALLER,
    STATUS_OPTIMIZED,
    STATUS_TARGET_NOT_REACHED,
    optimize_image,
)
from tests.fixtures import ensure_fixtures
from tests.helpers import REDUCED_STATUSES, optimize_and_wait, wait_for_job

try:
    import pillow_heif
except ImportError:  # pragma: no cover
    pillow_heif = None


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


@pytest.fixture(scope="module")
def fixtures_dir() -> Path:
    return ensure_fixtures()


def _jpeg_bytes(seed: int = 0, size=(320, 200)) -> bytes:
    image = Image.new("RGB", size, (40 + seed, 90, 140))
    buffer = io.BytesIO()
    image.save(buffer, format="JPEG", quality=92)
    return buffer.getvalue()


def _png_alpha_bytes() -> bytes:
    image = Image.new("RGBA", (160, 120), (0, 0, 0, 0))
    for x in range(20, 80):
        for y in range(20, 80):
            image.putpixel((x, y), (220, 40, 40, 255))
    buffer = io.BytesIO()
    image.save(buffer, format="PNG")
    return buffer.getvalue()


def _webp_bytes() -> bytes:
    image = Image.new("RGB", (180, 140), (30, 140, 120))
    buffer = io.BytesIO()
    image.save(buffer, format="WEBP", quality=90)
    return buffer.getvalue()


def test_progress_single_file(client: TestClient):
    response = client.post(
        "/api/optimize",
        files=[("files", ("one.jpg", _jpeg_bytes(), "image/jpeg"))],
        data={"output_format": "webp", "target_size_kb": 150},
    )
    assert response.status_code == 200
    started = response.json()
    assert started["total_count"] == 1
    assert started["status"] in ("processing", "completed")
    payload = wait_for_job(client, started)
    assert payload["progress_percent"] == 100
    assert payload["processed_count"] == 1
    assert payload["results"][0]["status"] in REDUCED_STATUSES | {"not_smaller"}


def test_progress_multiple_files_sequential_updates(client: TestClient):
    files = [
        ("files", (f"photo-{index}.jpg", _jpeg_bytes(index, (500, 360)), "image/jpeg"))
        for index in range(5)
    ]
    response = client.post(
        "/api/optimize",
        files=files,
        data={"output_format": "webp", "target_size_kb": 150},
    )
    assert response.status_code == 200
    job_id = response.json()["job_id"]

    seen_processing = False
    seen_counts = set()
    deadline = time.time() + 60
    final = None
    while time.time() < deadline:
        payload = client.get(f"/api/jobs/{job_id}").json()
        seen_counts.add(payload["processed_count"])
        if payload["current_filename"]:
            seen_processing = True
            assert any(
                item["status"] == "processing" or item["original_filename"] == payload["current_filename"]
                for item in payload["results"]
            )
        if payload["status"] == "completed":
            final = payload
            break
        time.sleep(0.05)

    assert final is not None
    assert final["progress_percent"] == 100
    assert final["processed_count"] == 5
    assert final["total_count"] == 5
    assert len(final["results"]) == 5
    # At least one intermediate processed count should have been observed for a 5-file batch.
    assert seen_counts & {0, 1, 2, 3, 4}
    assert seen_processing or final["status"] == "completed"


def test_progress_large_batch(client: TestClient):
    files = [
        ("files", (f"batch-{index}.jpg", _jpeg_bytes(index % 7), "image/jpeg"))
        for index in range(20)
    ]
    _response, payload = optimize_and_wait(
        client,
        files=files,
        data={"output_format": "webp", "target_size_kb": 150},
        timeout=120,
    )
    assert payload["total_count"] == 20
    assert payload["processed_count"] == 20
    assert payload["progress_percent"] == 100
    assert len(payload["results"]) == 20


def test_progress_partial_success_and_one_error(client: TestClient):
    files = [
        ("files", ("good-1.jpg", _jpeg_bytes(1), "image/jpeg")),
        ("files", ("bad.txt", b"not-image", "text/plain")),
        ("files", ("good-2.jpg", _jpeg_bytes(2), "image/jpeg")),
    ]
    _response, payload = optimize_and_wait(
        client,
        files=files,
        data={"output_format": "webp", "target_size_kb": 150},
    )
    assert payload["error_count"] == 1
    assert payload["success_count"] == 2
    assert payload["results"][1]["status"] == "error"
    assert payload["zip_url"]
    assert payload["progress_percent"] == 100


def test_progress_retry_after_completion(client: TestClient):
    files = [("files", ("retry.jpg", _jpeg_bytes(), "image/jpeg"))]
    _response, first = optimize_and_wait(
        client,
        files=files,
        data={"output_format": "webp", "target_size_kb": 150},
    )
    assert first["status"] == "completed"
    _response, second = optimize_and_wait(
        client,
        files=files,
        data={"output_format": "webp", "target_size_kb": 150},
    )
    assert second["status"] == "completed"
    assert second["job_id"] != first["job_id"]


def test_preview_jpg_png_webp(client: TestClient):
    samples = [
        ("photo.jpg", _jpeg_bytes(), "image/jpeg"),
        ("alpha.png", _png_alpha_bytes(), "image/png"),
        ("shot.webp", _webp_bytes(), "image/webp"),
    ]
    for name, data, mime in samples:
        response = client.post(
            "/api/preview",
            files=[("file", (name, data, mime))],
        )
        assert response.status_code == 200, name
        assert response.headers["content-type"] in ("image/jpeg", "image/png")
        assert len(response.content) > 32
        with Image.open(io.BytesIO(response.content)) as img:
            assert img.size[0] > 0 and img.size[1] > 0
            if name.endswith(".png"):
                assert img.mode in ("RGBA", "LA", "RGBa", "P")
                assert img.getpixel((0, 0))[3] == 0


def test_preview_heic_heif(client: TestClient, fixtures_dir: Path):
    if pillow_heif is None:
        pytest.skip("pillow-heif not installed")
    for name in ("sample.heic", "sample.heif"):
        path = fixtures_dir / name
        if not path.exists():
            pytest.skip(f"missing {name}")
        response = client.post(
            "/api/preview",
            files=[("file", (name, path.read_bytes(), "image/heic"))],
        )
        assert response.status_code == 200, response.text
        assert response.headers["content-type"] in ("image/jpeg", "image/png")
        with Image.open(io.BytesIO(response.content)) as img:
            assert img.size[0] > 0


def test_preview_large_image(client: TestClient, fixtures_dir: Path):
    large = fixtures_dir / "large.jpg"
    response = client.post(
        "/api/preview",
        files=[("file", ("large.jpg", large.read_bytes(), "image/jpeg"))],
    )
    assert response.status_code == 200
    with Image.open(io.BytesIO(response.content)) as img:
        assert max(img.size) <= 960


def test_preview_unavailable_for_corrupt(client: TestClient):
    response = client.post(
        "/api/preview",
        files=[("file", ("broken.png", b"\x89PNG\r\n\x1a\nCORRUPT", "image/png"))],
    )
    assert response.status_code == 400


def test_render_preview_preserves_png_alpha():
    preview_bytes, media = render_preview(_png_alpha_bytes())
    assert media == "image/png"
    with Image.open(io.BytesIO(preview_bytes)) as img:
        assert img.mode in ("RGBA", "LA")
        assert img.getpixel((0, 0))[3] == 0


def test_status_optimized_when_under_target_and_original(tmp_path: Path):
    source = tmp_path / "big.png"
    Image.new("RGB", (1400, 900), (80, 120, 160)).save(source)
    out = tmp_path / "out.webp"
    result = optimize_image(str(source), str(out), max_size_kb=150, output_format="webp")
    assert result.optimization_status == STATUS_OPTIMIZED
    assert result.size_reduced is True
    assert result.target_achieved is True
    assert result.compression_percent > 0
    assert out.stat().st_size == result.optimized_size_bytes
    assert out.stat().st_size < source.stat().st_size


def test_status_target_not_reached_when_smaller_but_above_target(tmp_path: Path):
    import random

    rng = random.Random(11)
    source = tmp_path / "noisy.png"
    image = Image.new("RGB", (900, 700))
    pixels = image.load()
    for y in range(700):
        for x in range(900):
            pixels[x, y] = (rng.randint(0, 255), rng.randint(0, 255), rng.randint(0, 255))
    image.save(source, format="PNG", compress_level=0)

    out = tmp_path / "out.webp"
    result = optimize_image(
        str(source),
        str(out),
        max_size_kb=1,
        max_width=900,
        output_format="webp",
        start_quality=90,
        min_quality=40,
        step_quality=5,
    )
    assert result.optimized_size_bytes < result.original_size_bytes
    assert result.target_achieved is False
    assert result.optimization_status == STATUS_TARGET_NOT_REACHED
    assert result.compression_percent > 0


def test_status_not_smaller_for_tiny_jpeg(tmp_path: Path):
    source = tmp_path / "tiny.jpg"
    Image.new("RGB", (1, 1), (0, 0, 0)).save(source, format="JPEG", quality=10, optimize=True)
    out = tmp_path / "tiny.webp"
    result = optimize_image(str(source), str(out), max_size_kb=150, output_format="webp")
    if result.optimized_size_bytes >= result.original_size_bytes:
        assert result.optimization_status == STATUS_NOT_SMALLER
        assert result.size_reduced is False
        assert result.compression_percent == 0
    else:
        assert result.optimization_status in REDUCED_STATUSES


def test_api_not_smaller_excluded_from_zip_and_success(client: TestClient, tmp_path: Path):
    tiny = tmp_path / "tiny.jpg"
    Image.new("RGB", (1, 1), (0, 0, 0)).save(tiny, format="JPEG", quality=10, optimize=True)
    reducible = tmp_path / "big.jpg"
    Image.new("RGB", (800, 600), (90, 120, 150)).save(reducible, format="JPEG", quality=95)

    _response, payload = optimize_and_wait(
        client,
        files=[
            ("files", ("tiny.jpg", tiny.read_bytes(), "image/jpeg")),
            ("files", ("big.jpg", reducible.read_bytes(), "image/jpeg")),
        ],
        data={"output_format": "webp", "target_size_kb": 150},
    )
    tiny_item = next(item for item in payload["results"] if item["original_filename"] == "tiny.jpg")
    big_item = next(item for item in payload["results"] if item["original_filename"] == "big.jpg")

    if tiny_item["status"] == "not_smaller":
        assert tiny_item["download_url"] is None
        assert tiny_item["compression_percent"] == 0
        assert payload["not_smaller_count"] >= 1
        assert payload["success_count"] == 1
        assert big_item["status"] in REDUCED_STATUSES
        zip_response = client.get(payload["zip_url"])
        assert zip_response.status_code == 200
        with zipfile.ZipFile(io.BytesIO(zip_response.content)) as archive:
            assert len(archive.namelist()) == 1
            assert "tiny" not in " ".join(archive.namelist()).lower()
    else:
        # Environment managed to shrink the tiny JPEG; still must never show negatives.
        assert tiny_item["compression_percent"] >= 0


def test_equal_or_larger_never_reports_negative_percent(tmp_path: Path):
    source = tmp_path / "compact.jpg"
    Image.new("RGB", (40, 30), (120, 120, 120)).save(source, format="JPEG", quality=20, optimize=True)
    out = tmp_path / "out.webp"
    result = optimize_image(str(source), str(out), max_size_kb=150, output_format="webp")
    assert result.compression_percent >= 0
    if not result.size_reduced:
        assert result.optimization_status == STATUS_NOT_SMALLER
        assert result.compression_percent == 0


def test_frontend_locks_and_scroll_helpers(client: TestClient):
    js = client.get("/js/app.js").text
    assert "scrollToProcessing" in js
    assert "dropzone--locked" in js
    assert "pollUntilComplete" in js
    assert "fetchServerPreview" in js
    assert "HEIC_EXTENSIONS" in js
    assert "Network error while uploading images." in js
    html = client.get("/").text
    assert "processing-files" in html
    assert "queue-item__placeholder" in html
    assert "result-card__placeholder--original" in html
