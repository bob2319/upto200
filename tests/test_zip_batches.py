"""ZIP download behavior for mixed and sized batches."""

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
from api.storage import job_store
from tests.helpers import REDUCED_STATUSES, optimize_and_wait


def _jpeg_bytes(seed: int = 0) -> bytes:
    image = Image.new("RGB", (100 + seed, 80), (20 + seed, 90, 140))
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


@pytest.mark.parametrize("count", [1, 5, 20])
def test_zip_contains_all_successful_images(client: TestClient, count: int):
    files = [
        ("files", (f"ok_{index}.jpg", _jpeg_bytes(index), "image/jpeg"))
        for index in range(count)
    ]
    _response, payload = optimize_and_wait(
        client,
        files=files,
        data={"output_format": "webp", "target_size_kb": 150},
    )
    assert payload["zip_url"]
    assert sum(1 for item in payload["results"] if item["status"] in REDUCED_STATUSES) == count

    zip_response = client.get(payload["zip_url"])
    assert zip_response.status_code == 200
    with zipfile.ZipFile(io.BytesIO(zip_response.content)) as archive:
        assert len(archive.namelist()) == count


def test_mixed_batch_zip_excludes_failures(client: TestClient):
    files = [
        ("files", ("good_a.jpg", _jpeg_bytes(1), "image/jpeg")),
        ("files", ("bad.txt", b"not-an-image", "text/plain")),
        ("files", ("good_b.jpg", _jpeg_bytes(2), "image/jpeg")),
        ("files", ("broken.png", b"\x89PNG\r\n\x1a\nCORRUPT", "image/png")),
        ("files", ("good_c.jpg", _jpeg_bytes(3), "image/jpeg")),
    ]
    _response, payload = optimize_and_wait(
        client,
        files=files,
        data={"output_format": "webp", "target_size_kb": 150},
    )
    ok_items = [item for item in payload["results"] if item["status"] in REDUCED_STATUSES]
    failed_items = [item for item in payload["results"] if item["status"] == "error"]
    assert len(ok_items) == 3
    assert len(failed_items) == 2
    assert payload["zip_url"]
    assert payload["success_count"] == 3
    assert payload["error_count"] == 2

    zip_response = client.get(payload["zip_url"])
    assert zip_response.status_code == 200
    with zipfile.ZipFile(io.BytesIO(zip_response.content)) as archive:
        names = archive.namelist()
        assert len(names) == 3
        assert all(name.endswith(".webp") for name in names)
        joined = " ".join(names).lower()
        assert "bad" not in joined
        assert "broken" not in joined


def test_all_failed_batch_has_no_zip(client: TestClient):
    _response, payload = optimize_and_wait(
        client,
        files=[
            ("files", ("notes.txt", b"nope", "text/plain")),
            ("files", ("also.txt", b"still-nope", "text/plain")),
        ],
        data={"output_format": "webp"},
    )
    assert payload["zip_url"] is None
    assert all(item["status"] == "error" for item in payload["results"])


def test_frontend_exposes_prominent_zip_controls(client: TestClient):
    page = client.get("/")
    assert page.status_code == 200
    html = page.text
    assert "Download all (.ZIP)" in html
    assert 'id="download-zip"' in html
    assert 'id="results-summary"' in html
    assert 'id="zip-error"' in html
    assert 'id="processing"' in html
    assert 'id="processing-files"' in html
    assert 'id="processing-percent"' in html

    css = client.get("/css/main.css")
    assert css.status_code == 200
    assert ".results__zip" in css.text
    assert ".results__summary" in css.text
    assert ".processing__panel" in css.text
    assert "processing-spin" in css.text
    assert ".processing__bar--determinate" in css.text


def test_frontend_processing_ux_helpers(client: TestClient):
    js = client.get("/js/app.js")
    assert js.status_code == 200
    text = js.text
    assert "showProcessing" in text
    assert "hideProcessing" in text
    assert "scrollToResults" in text
    assert "scrollToProcessing" in text
    assert "pollUntilComplete" in text
    assert "state.isProcessing" in text
    assert "renderedSuccess > 0" in text
    assert "Processing..." in text
