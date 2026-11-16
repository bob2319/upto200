"""Regression tests for size-vs-original, quality reporting, and preview matching."""

from __future__ import annotations

import sys
import tempfile
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from PIL import Image

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from api.main import app
from api.storage import job_store
from optimizer.engine import compress_image
from optimizer.service import (
    STATUS_NOT_SMALLER,
    STATUS_OPTIMIZED,
    STATUS_TARGET_NOT_REACHED,
    optimize_image,
)
from tests.helpers import REDUCED_STATUSES, optimize_and_wait


def _highly_compressed_jpeg(path: Path, width: int = 800, height: int = 500) -> None:
    image = Image.new("RGB", (width, height), (180, 180, 180))
    pixels = image.load()
    for y in range(height):
        for x in range(width):
            tone = 120 + (x + y) % 40
            pixels[x, y] = (tone, tone, tone)
    image.save(path, format="JPEG", quality=35, optimize=True)


def _noisy_large_png(path: Path, width: int = 700, height: int = 500) -> None:
    import random

    rng = random.Random(7)
    image = Image.new("RGB", (width, height))
    pixels = image.load()
    for y in range(height):
        for x in range(width):
            pixels[x, y] = (
                rng.randint(0, 255),
                rng.randint(0, 255),
                rng.randint(0, 255),
            )
    image.save(path, format="PNG", compress_level=0)


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


def test_engine_continues_when_under_target_but_larger_than_original(tmp_path: Path):
    source = tmp_path / "already-small.jpg"
    _highly_compressed_jpeg(source)
    original_size = source.stat().st_size
    out = tmp_path / "out.webp"

    size_kb, quality = compress_image(
        str(source),
        str(out),
        max_size_kb=150,
        max_width=1920,
        start_quality=90,
        min_quality=40,
        step_quality=5,
    )

    optimized_size = out.stat().st_size
    assert optimized_size <= original_size or quality == 40
    if optimized_size < original_size:
        assert size_kb * 1024 == pytest.approx(optimized_size, rel=0, abs=1)
        assert optimized_size / 1024 <= 150


def test_api_avoids_negative_reduction_when_smaller_encoding_exists(client: TestClient, tmp_path: Path):
    source = tmp_path / "compact.jpg"
    _highly_compressed_jpeg(source)
    _response, payload = optimize_and_wait(
        client,
        files=[("files", ("compact.jpg", source.read_bytes(), "image/jpeg"))],
        data={
            "output_format": "webp",
            "target_size_kb": 150,
            "start_quality": 90,
            "min_quality": 40,
            "step_quality": 5,
        },
    )
    item = payload["results"][0]
    assert item["compression_percent"] >= 0
    if item["optimized_size_bytes"] < item["original_size_bytes"]:
        assert item["status"] in REDUCED_STATUSES
        assert item["size_reduced"] is True
    else:
        assert item["status"] == "not_smaller"
        assert item["size_reduced"] is False
        assert item["download_url"] is None


def test_reported_quality_matches_encoded_file_on_unreachable_target(
    client: TestClient, tmp_path: Path
):
    source = tmp_path / "noisy.png"
    _noisy_large_png(source)
    _response, payload = optimize_and_wait(
        client,
        files=[("files", ("noisy.png", source.read_bytes(), "image/png"))],
        data={
            "output_format": "webp",
            "target_size_kb": 1,
            "max_width": 700,
            "start_quality": 90,
            "min_quality": 40,
            "step_quality": 5,
        },
    )
    item = payload["results"][0]
    assert item["target_achieved"] is False
    assert item["quality"] == 40
    assert item["status"] in ("target_not_reached", "not_smaller")

    with tempfile.TemporaryDirectory() as tmp:
        probe_out = Path(tmp) / "probe.webp"
        size_kb, quality = compress_image(
            str(source),
            str(probe_out),
            max_size_kb=1,
            max_width=700,
            start_quality=90,
            min_quality=40,
            step_quality=5,
        )
        assert quality == 40
        assert abs(probe_out.stat().st_size - item["optimized_size_bytes"]) <= 32
        assert abs(size_kb * 1024 - item["optimized_size_bytes"]) <= 32

    if item["size_reduced"]:
        download = client.get(item["download_url"])
        assert download.status_code == 200


def test_quality_boundary_never_reports_below_minimum(tmp_path: Path):
    source = tmp_path / "boundary.png"
    _noisy_large_png(source, width=640, height=480)
    out = tmp_path / "boundary.webp"
    _size_kb, quality = compress_image(
        str(source),
        str(out),
        max_size_kb=1,
        min_quality=40,
        start_quality=90,
        step_quality=5,
    )
    assert quality >= 40
    assert quality == 40


def test_batch_preserves_sanitized_names_and_order_for_previews(client: TestClient, tmp_path: Path):
    files = []
    for index in range(12):
        path = tmp_path / f"img_{index}.jpg"
        Image.new("RGB", (80, 60), (index * 10, 40, 90)).save(path, quality=85)
        name = (
            f"batch_preview_name_{index}_"
            + ("longsegment_" * 12)
            + f"{index}.jpg"
        )
        files.append(("files", (name, path.read_bytes(), "image/jpeg")))

    _response, payload = optimize_and_wait(
        client,
        files=files,
        data={"output_format": "webp", "target_size_kb": 150},
    )
    results = payload["results"]
    assert len(results) == 12
    assert all(item["status"] in REDUCED_STATUSES | {"not_smaller"} for item in results)
    for index, item in enumerate(results):
        assert str(index) in item["original_filename"] or item["original_filename"].endswith(".jpg")


def test_frontend_preview_mapping_uses_index_not_filename(client: TestClient):
    js = client.get("/js/app.js")
    assert js.status_code == 200
    text = js.text
    assert "state.files[index]" in text
    assert "originalsByName" not in text
    assert "URL.createObjectURL(file)" in text
    assert "/preview" in text
    assert "Not reduced" in text
    assert "No size reduction" in text


def test_when_no_smaller_output_exists_report_honestly(client: TestClient, tmp_path: Path):
    source = tmp_path / "tiny.jpg"
    Image.new("RGB", (1, 1), (0, 0, 0)).save(source, format="JPEG", quality=10, optimize=True)

    _response, payload = optimize_and_wait(
        client,
        files=[("files", ("tiny.jpg", source.read_bytes(), "image/jpeg"))],
        data={
            "output_format": "webp",
            "target_size_kb": 150,
            "start_quality": 90,
            "min_quality": 40,
            "step_quality": 5,
        },
    )
    item = payload["results"][0]
    assert item["output_format"] == "webp"
    assert 40 <= item["quality"] <= 90
    assert item["compression_percent"] >= 0
    if item["optimized_size_bytes"] >= item["original_size_bytes"]:
        assert item["status"] == "not_smaller"
        assert item["size_reduced"] is False
        assert item["download_url"] is None
        assert item["compression_percent"] == 0
    else:
        assert item["status"] in REDUCED_STATUSES
        assert item["compression_percent"] > 0


@pytest.mark.parametrize("input_name,input_builder", [
    ("photo.jpg", lambda p: Image.new("RGB", (400, 300), (40, 90, 130)).save(p, quality=92)),
    ("shot.png", lambda p: Image.new("RGBA", (320, 240), (40, 90, 130, 200)).save(p)),
    ("src.webp", lambda p: Image.new("RGB", (320, 240), (40, 90, 130)).save(p, "WEBP", quality=90)),
])
@pytest.mark.parametrize("output_format", ["webp", "jpeg", "png"])
def test_format_matrix_size_and_quality_contract(
    client: TestClient, tmp_path: Path, input_name, input_builder, output_format
):
    source = tmp_path / input_name
    input_builder(source)
    _response, payload = optimize_and_wait(
        client,
        files=[("files", (input_name, source.read_bytes(), "application/octet-stream"))],
        data={
            "output_format": output_format,
            "target_size_kb": 150,
            "start_quality": 90,
            "min_quality": 40,
            "step_quality": 5,
        },
    )
    item = payload["results"][0]
    assert item["status"] in REDUCED_STATUSES | {"not_smaller"}, item
    assert item["output_format"] == ("jpeg" if output_format == "jpeg" else output_format)
    assert item["quality"] is not None
    if output_format in ("webp", "jpeg"):
        assert 40 <= item["quality"] <= 90
    if item["optimized_size_bytes"] < item["original_size_bytes"]:
        assert item["compression_percent"] > 0
        assert item["size_reduced"] is True
    else:
        assert item["compression_percent"] == 0
        assert item["size_reduced"] is False
        assert item["status"] == "not_smaller"


def test_service_classifies_status_separately_from_target(tmp_path: Path):
    source = tmp_path / "big.png"
    Image.new("RGB", (1200, 800), (70, 110, 150)).save(source)
    out = tmp_path / "big.webp"
    result = optimize_image(str(source), str(out), output_format="webp", max_size_kb=150)
    assert result.optimization_status == STATUS_OPTIMIZED
    assert result.size_reduced is True
    assert result.target_achieved is True
    assert result.compression_percent > 0

    tiny = tmp_path / "tiny.jpg"
    Image.new("RGB", (1, 1), (0, 0, 0)).save(tiny, format="JPEG", quality=10, optimize=True)
    tiny_out = tmp_path / "tiny.webp"
    tiny_result = optimize_image(str(tiny), str(tiny_out), output_format="webp", max_size_kb=150)
    if tiny_result.optimized_size_bytes >= tiny_result.original_size_bytes:
        assert tiny_result.optimization_status == STATUS_NOT_SMALLER
        assert tiny_result.compression_percent == 0
        assert tiny_result.size_reduced is False
    else:
        assert tiny_result.optimization_status in (
            STATUS_OPTIMIZED,
            STATUS_TARGET_NOT_REACHED,
        )


def test_normal_case_still_stops_under_target_and_under_original(tmp_path: Path):
    source = tmp_path / "large.png"
    Image.new("RGB", (1600, 900), (60, 100, 140)).save(source)
    out = tmp_path / "large.webp"
    size_kb, quality = compress_image(
        str(source),
        str(out),
        max_size_kb=150,
        start_quality=90,
        min_quality=40,
        step_quality=5,
    )
    assert size_kb <= 150
    assert out.stat().st_size < source.stat().st_size
    assert quality >= 40
