"""End-to-end integration tests for MVP hardening."""

from __future__ import annotations

import io
import sys
import zipfile
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from PIL import Image

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from api.main import app
from api.storage import job_store
from optimizer.imaging import load_image
from optimizer.service import optimize_image
from tests.fixtures import FIXTURES_DIR, ensure_fixtures
from tests.helpers import REDUCED_STATUSES, wait_for_job

INPUTS = {
    "jpg": "photo.jpg",
    "png": "screenshot.png",
    "webp": "source.webp",
    "heic": "sample.heic",
    "heif": "sample.heif",
}
OUTPUTS = ("webp", "jpeg", "png")


@pytest.fixture(scope="module")
def fixtures_dir() -> Path:
    return ensure_fixtures()


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


def _optimize_files(client: TestClient, paths: list[Path], **data):
    files = []
    handles = []
    try:
        for path in paths:
            handle = path.open("rb")
            handles.append(handle)
            mime = {
                ".jpg": "image/jpeg",
                ".jpeg": "image/jpeg",
                ".png": "image/png",
                ".webp": "image/webp",
                ".heic": "image/heic",
                ".heif": "image/heif",
            }.get(path.suffix.lower(), "application/octet-stream")
            files.append(("files", (path.name, handle, mime)))
        response = client.post("/api/optimize", files=files, data=data)
    finally:
        for handle in handles:
            handle.close()
    if response.status_code != 200:
        return response

    completed = wait_for_job(client, response.json())

    class _Completed:
        status_code = 200
        text = response.text

        @staticmethod
        def json():
            return completed

    return _Completed()


@pytest.mark.parametrize("input_key", ["jpg", "png", "webp", "heic", "heif"])
@pytest.mark.parametrize("output_format", OUTPUTS)
def test_supported_input_output_matrix(client, fixtures_dir, input_key, output_format):
    source = fixtures_dir / INPUTS[input_key]
    if not source.exists():
        pytest.skip(f"Missing fixture {source.name}")

    response = _optimize_files(
        client,
        [source],
        target_size_kb=150,
        max_width=1920,
        output_format=output_format,
        start_quality=90,
        min_quality=40,
    )
    assert response.status_code == 200, response.text
    item = response.json()["results"][0]
    assert item["status"] in REDUCED_STATUSES | {"not_smaller"}, item
    assert item["output_format"] == ("jpeg" if output_format == "jpeg" else output_format)
    assert item["optimized_size_bytes"] > 0

    if item["size_reduced"]:
        download = client.get(item["download_url"])
        assert download.status_code == 200
        with Image.open(io.BytesIO(download.content)) as img:
            assert img.size[0] > 0 and img.size[1] > 0
            if output_format == "png" and input_key == "png":
                assert img.format == "PNG"


@pytest.mark.parametrize("count", [1, 5, 20])
def test_batch_sizes(client, fixtures_dir, count):
    base = fixtures_dir / "photo.jpg"
    paths = []
    for index in range(count):
        clone = fixtures_dir / f"batch_{count}_{index}.jpg"
        if not clone.exists():
            clone.write_bytes(base.read_bytes())
        paths.append(clone)

    response = _optimize_files(
        client,
        paths,
        target_size_kb=150,
        output_format="webp",
    )
    assert response.status_code == 200, response.text
    payload = response.json()
    assert len(payload["results"]) == count
    assert all(item["status"] in REDUCED_STATUSES for item in payload["results"])
    if count > 1:
        assert payload["zip_url"]
        zip_response = client.get(payload["zip_url"])
        assert zip_response.status_code == 200
        with zipfile.ZipFile(io.BytesIO(zip_response.content)) as archive:
            assert len(archive.namelist()) == count


@pytest.mark.parametrize("target_kb", [50, 100, 150])
def test_target_sizes_including_unachievable(client, fixtures_dir, target_kb):
    response = _optimize_files(
        client,
        [fixtures_dir / "large.jpg"],
        target_size_kb=target_kb,
        max_width=1920,
        output_format="webp",
        start_quality=90,
        min_quality=40,
    )
    assert response.status_code == 200, response.text
    item = response.json()["results"][0]
    assert item["status"] in REDUCED_STATUSES | {"not_smaller"}
    size_kb = item["optimized_size_bytes"] / 1024
    if item["target_achieved"]:
        assert size_kb <= target_kb
    else:
        assert size_kb > target_kb
    if item["status"] == "optimized":
        assert item["size_reduced"] and item["target_achieved"]
    if item["status"] == "target_not_reached":
        assert item["size_reduced"] and not item["target_achieved"]
    if item["status"] == "not_smaller":
        assert not item["size_reduced"]


def test_transparent_png_to_png_and_webp(client, fixtures_dir, tmp_path):
    # Small transparent fixtures may not shrink further; alpha must still be preserved.
    source = fixtures_dir / "transparent.png"
    for fmt in ("png", "webp"):
        out = tmp_path / f"alpha.{fmt}"
        result = optimize_image(
            str(source),
            str(out),
            output_format=fmt,
            max_size_kb=200,
        )
        with Image.open(out) as img:
            assert img.mode in ("RGBA", "LA", "RGBa")
            assert img.getpixel((5, 5))[3] == 0
        assert result.optimization_status in REDUCED_STATUSES | {"not_smaller"}
        assert result.compression_percent >= 0

    response = _optimize_files(
        client,
        [source],
        output_format="webp",
        target_size_kb=200,
    )
    assert response.status_code == 200, response.text
    item = response.json()["results"][0]
    assert item["status"] in REDUCED_STATUSES | {"not_smaller"}
    assert item["compression_percent"] >= 0
    if item["status"] == "not_smaller":
        assert item["size_reduced"] is False
        assert item["download_url"] is None
        assert item["target_achieved"] is True  # under 200 KB, but not a successful reduction
    else:
        content = client.get(item["download_url"]).content
        with Image.open(io.BytesIO(content)) as img:
            assert img.mode in ("RGBA", "LA", "RGBa")
            assert img.getpixel((5, 5))[3] == 0


def test_text_and_screenshot_outputs(client, fixtures_dir):
    for name in ("text.png", "screenshot.png", "photo.jpg"):
        response = _optimize_files(
            client,
            [fixtures_dir / name],
            output_format="webp",
            target_size_kb=100,
        )
        assert response.status_code == 200, response.text
        item = response.json()["results"][0]
        assert item["status"] in REDUCED_STATUSES | {"not_smaller"}
        assert item["output_width"] <= 1920


def test_exif_orientation_corrected(fixtures_dir, tmp_path):
    source = fixtures_dir / "exif_orient.jpg"
    raw = Image.open(source)
    assert raw.size == (200, 100)
    corrected = load_image(str(source))
    assert corrected.size == (100, 200)

    out = tmp_path / "exif.webp"
    result = optimize_image(str(source), str(out), output_format="webp", max_size_kb=150)
    assert result.original_width == 100
    assert result.original_height == 200
    assert result.output_width == 100
    assert result.output_height == 200


def test_individual_and_zip_download(client, fixtures_dir):
    response = _optimize_files(
        client,
        [fixtures_dir / "photo.jpg", fixtures_dir / "text.png"],
        output_format="jpeg",
        target_size_kb=150,
    )
    payload = response.json()
    reduced = [item for item in payload["results"] if item["size_reduced"]]
    assert payload["zip_url"]
    assert reduced
    for item in reduced:
        dl = client.get(item["download_url"])
        assert dl.status_code == 200
        assert dl.content[:2] == b"\xff\xd8"
    zip_response = client.get(payload["zip_url"])
    assert zip_response.status_code == 200
    with zipfile.ZipFile(io.BytesIO(zip_response.content)) as archive:
        assert len(archive.namelist()) == len(reduced)


def test_concurrent_downloads_remain_stable(client, fixtures_dir):
    response = _optimize_files(
        client,
        [fixtures_dir / "photo.jpg"],
        output_format="webp",
        target_size_kb=150,
    )
    url = response.json()["results"][0]["download_url"]
    assert url

    def fetch(_):
        return client.get(url).status_code

    with ThreadPoolExecutor(max_workers=8) as pool:
        futures = [pool.submit(fetch, i) for i in range(8)]
        codes = [future.result() for future in as_completed(futures)]
    assert codes == [200] * 8


def test_security_limits(client, fixtures_dir, monkeypatch):
    monkeypatch.setattr("api.security.MAX_UPLOAD_BYTES", 2048)
    oversized = b"x" * 4096
    response = client.post(
        "/api/optimize",
        files=[("files", ("big.png", oversized, "image/png"))],
        data={"output_format": "webp"},
    )
    assert response.status_code == 200
    oversized_payload = wait_for_job(client, response.json())
    assert oversized_payload["results"][0]["status"] == "error"
    assert "too large" in oversized_payload["results"][0]["error"].lower()

    invalid = client.post(
        "/api/optimize",
        files=[("files", ("notes.txt", b"hello", "text/plain"))],
        data={"output_format": "webp"},
    )
    invalid_payload = wait_for_job(client, invalid.json())
    assert invalid_payload["results"][0]["status"] == "error"
    assert "Invalid or corrupted image" in invalid_payload["results"][0]["error"]

    corrupted = client.post(
        "/api/optimize",
        files=[("files", ("bad.png", b"\x89PNG\r\n\x1a\nCORRUPT", "image/png"))],
        data={"output_format": "webp"},
    )
    corrupted_payload = wait_for_job(client, corrupted.json())
    assert corrupted_payload["results"][0]["status"] == "error"
    assert "Invalid or corrupted image" in corrupted_payload["results"][0]["error"]


def test_concurrent_optimize_requests(client, fixtures_dir):
    def run(_):
        return _optimize_files(
            client,
            [fixtures_dir / "photo.jpg"],
            output_format="webp",
            target_size_kb=150,
        ).status_code

    with ThreadPoolExecutor(max_workers=4) as pool:
        futures = [pool.submit(run, i) for i in range(4)]
        statuses = [future.result() for future in as_completed(futures)]
    assert all(status == 200 for status in statuses)


def test_frontend_index_served(client):
    response = client.get("/")
    assert response.status_code == 200
    assert "Upto200" in response.text
    assert "OptiSize" not in response.text
    assert "Online image optimizer for faster websites" in response.text
    assert "Compress images online" in response.text or "compress images online" in response.text.lower()
    assert 'id="dropzone"' in response.text
    assert 'name="robots"' in response.text
    css = client.get("/css/main.css")
    js = client.get("/js/app.js")
    assert css.status_code == 200
    assert js.status_code == 200
    og = client.get("/assets/og-cover.svg")
    assert og.status_code == 200
    assert "Upto200" in og.text
    assert "OptiSize" not in og.text
