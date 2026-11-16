"""SEO configuration, robots, sitemap, and markup tests."""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from api.main import app
from api.seo import (
    is_indexable,
    public_origin_from_request,
    render_index_html,
    robots_txt,
    sitemap_xml,
)
from api.storage import job_store


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


def test_default_without_site_url_is_not_indexable(monkeypatch):
    monkeypatch.setattr("api.seo.SITE_URL_ENV", "")
    monkeypatch.setattr("api.seo.SEO_INDEXABLE_ENV", None)
    assert is_indexable("http://testserver") is False
    assert is_indexable("http://127.0.0.1:8000") is False


def test_railway_site_url_is_not_indexable_by_default(monkeypatch):
    monkeypatch.setattr(
        "api.seo.SITE_URL_ENV", "https://upto200-production.up.railway.app"
    )
    monkeypatch.setattr("api.seo.SEO_INDEXABLE_ENV", None)
    assert is_indexable() is False


def test_production_site_url_is_indexable_by_default(monkeypatch):
    monkeypatch.setattr("api.seo.SITE_URL_ENV", "https://upto200.com")
    monkeypatch.setattr("api.seo.SEO_INDEXABLE_ENV", None)
    assert is_indexable() is True


def test_seo_indexable_env_override(monkeypatch):
    monkeypatch.setattr("api.seo.SITE_URL_ENV", "https://upto200.com")
    monkeypatch.setattr("api.seo.SEO_INDEXABLE_ENV", "false")
    assert is_indexable() is False
    monkeypatch.setattr("api.seo.SITE_URL_ENV", "https://example-staging.up.railway.app")
    monkeypatch.setattr("api.seo.SEO_INDEXABLE_ENV", "true")
    assert is_indexable() is True


def test_homepage_noindex_by_default(client: TestClient, monkeypatch):
    monkeypatch.setattr("api.seo.SITE_URL_ENV", "")
    monkeypatch.setattr("api.seo.SEO_INDEXABLE_ENV", None)
    response = client.get("/")
    assert response.status_code == 200
    html = response.text
    assert 'name="robots" content="noindex,follow"' in html
    assert response.headers.get("x-robots-tag") == "noindex, follow"
    assert "Online Image Optimizer" in html
    assert "Online image optimizer for faster websites" in html
    assert 'type="application/ld+json"' in html
    assert "__SEO_" not in html
    assert "upto200-production.up.railway.app" not in html


def test_homepage_indexable_with_site_url(client: TestClient, monkeypatch):
    monkeypatch.setattr("api.seo.SITE_URL_ENV", "https://upto200.example")
    monkeypatch.setattr("api.seo.SEO_INDEXABLE_ENV", "true")
    response = client.get("/")
    assert response.status_code == 200
    html = response.text
    assert 'name="robots" content="index,follow"' in html
    assert 'rel="canonical" href="https://upto200.example/"' in html
    assert 'property="og:url" content="https://upto200.example/"' in html
    assert 'property="og:image" content="https://upto200.example/assets/og-cover.svg"' in html
    assert "x-robots-tag" not in {k.lower() for k in response.headers.keys()} or (
        response.headers.get("x-robots-tag") is None
    )


def test_robots_txt_allows_crawl_disallows_api(client: TestClient, monkeypatch):
    monkeypatch.setattr("api.seo.SITE_URL_ENV", "https://upto200.example")
    monkeypatch.setattr("api.seo.SEO_INDEXABLE_ENV", "false")
    response = client.get("/robots.txt")
    assert response.status_code == 200
    body = response.text
    assert "User-agent: *" in body
    assert "Allow: /" in body
    assert "Disallow: /api/" in body
    assert "Sitemap: https://upto200.example/sitemap.xml" in body
    assert "Disallow: /$" not in body


def test_sitemap_contains_only_homepage(client: TestClient, monkeypatch):
    monkeypatch.setattr("api.seo.SITE_URL_ENV", "https://upto200.example")
    response = client.get("/sitemap.xml")
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("application/xml")
    body = response.text
    assert "<loc>https://upto200.example/</loc>" in body
    assert "/api/" not in body
    assert "?" not in body.split("<loc>")[1].split("</loc>")[0]


def test_json_ld_is_valid_and_honest():
    html = render_index_html(
        (ROOT / "web" / "index.html").read_text(encoding="utf-8"),
        site_url="https://upto200.example",
        indexable=True,
    )
    match = re.search(
        r'<script type="application/ld\+json">\s*(\[.*?\])\s*</script>',
        html,
        flags=re.S,
    )
    assert match
    data = json.loads(match.group(1))
    assert isinstance(data, list)
    types = {item["@type"] for item in data}
    assert "WebSite" in types
    assert "WebApplication" in types
    joined = json.dumps(data)
    assert "aggregateRating" not in joined
    assert '"review"' not in joined.lower()
    assert '"offers"' not in joined.lower()
    assert '"author"' not in joined.lower()


def test_favicon_available(client: TestClient):
    response = client.get("/favicon.svg")
    assert response.status_code == 200
    assert "svg" in response.headers["content-type"]
    ico = client.get("/favicon.ico")
    assert ico.status_code == 200


def test_robots_and_sitemap_helpers_do_not_embed_api_urls():
    body = robots_txt("https://upto200.example", False)
    assert "/api/" in body
    map_body = sitemap_xml("https://upto200.example")
    assert map_body.count("<url>") == 1


def test_public_origin_prefers_forwarded_https(monkeypatch):
    monkeypatch.setattr("api.seo.SITE_URL_ENV", "")
    origin = public_origin_from_request(
        base_url="http://upto200-production.up.railway.app/",
        headers={
            "x-forwarded-proto": "https",
            "x-forwarded-host": "upto200-production.up.railway.app",
        },
    )
    assert origin == "https://upto200-production.up.railway.app"


def test_public_origin_upgrades_railway_http_fallback(monkeypatch):
    monkeypatch.setattr("api.seo.SITE_URL_ENV", "")
    origin = public_origin_from_request(
        base_url="http://upto200-production.up.railway.app/",
        headers={"host": "upto200-production.up.railway.app"},
    )
    assert origin == "https://upto200-production.up.railway.app"


def test_live_fallback_canonical_uses_https(client: TestClient, monkeypatch):
    monkeypatch.setattr("api.seo.SITE_URL_ENV", "")
    monkeypatch.setattr("api.seo.SEO_INDEXABLE_ENV", "false")
    response = client.get(
        "/",
        headers={
            "host": "upto200-production.up.railway.app",
            "x-forwarded-proto": "https",
        },
    )
    assert response.status_code == 200
    assert 'rel="canonical" href="https://upto200-production.up.railway.app/"' in response.text
    robots = client.get(
        "/robots.txt",
        headers={
            "host": "upto200-production.up.railway.app",
            "x-forwarded-proto": "https",
        },
    )
    assert "https://upto200-production.up.railway.app/sitemap.xml" in robots.text
