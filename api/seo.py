"""SEO helpers: site URL, indexability, markup rendering."""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Optional
from urllib.parse import urlparse

from api.settings import SEO_INDEXABLE_ENV, SITE_URL_ENV

STAGING_HOST_SUFFIXES = (
    ".railway.app",
    ".up.railway.app",
    ".loca.lt",
    ".ngrok-free.app",
    ".ngrok.app",
    ".trycloudflare.com",
)


def configured_site_url() -> str:
    return (SITE_URL_ENV or "").strip().rstrip("/")


def _host_is_staging(hostname: str) -> bool:
    host = (hostname or "").lower().strip(".")
    if not host:
        return True
    if host in {"localhost", "127.0.0.1", "0.0.0.0"}:
        return True
    return any(host.endswith(suffix) for suffix in STAGING_HOST_SUFFIXES)


def resolve_site_url(request_base_url: Optional[str] = None) -> str:
    """Prefer explicit SITE_URL; otherwise fall back to the current request origin."""
    configured = configured_site_url()
    if configured:
        return configured
    if request_base_url:
        return str(request_base_url).rstrip("/")
    return ""


def public_origin_from_request(
    *,
    base_url: str,
    headers: Optional[dict[str, str]] = None,
) -> str:
    """Build a public origin, honoring reverse-proxy forwarded headers.

    Railway terminates TLS and may expose request.base_url as http://.
    Prefer X-Forwarded-Proto / X-Forwarded-Host when SITE_URL is unset.
    """
    configured = configured_site_url()
    if configured:
        return configured

    hdrs = {str(k).lower(): str(v) for k, v in (headers or {}).items()}
    forwarded_proto = (hdrs.get("x-forwarded-proto") or "").split(",")[0].strip()
    forwarded_host = (hdrs.get("x-forwarded-host") or "").split(",")[0].strip()
    host_header = (hdrs.get("host") or "").split(",")[0].strip()

    parsed = urlparse(str(base_url or ""))
    scheme = forwarded_proto or parsed.scheme or "https"
    if scheme == "http" and forwarded_proto == "" and (parsed.hostname or "").endswith(
        (".railway.app", ".up.railway.app")
    ):
        # Defensive: public Railway URLs are always HTTPS even if the proxy
        # forgot to set X-Forwarded-Proto.
        scheme = "https"
    host = forwarded_host or host_header or parsed.netloc
    if not host:
        return str(base_url or "").rstrip("/")
    return f"{scheme}://{host}".rstrip("/")


def is_indexable(site_url: Optional[str] = None) -> bool:
    """Indexing requires an explicit non-staging SITE_URL, or SEO_INDEXABLE=true.

    Empty SITE_URL (typical for local/Railway smoke tests) defaults to noindex.
    The optional site_url argument is ignored for policy; only SITE_URL / SEO_INDEXABLE matter.
    """
    del site_url  # kept for call-site compatibility; policy uses env only
    raw = SEO_INDEXABLE_ENV
    if raw is not None and str(raw).strip() != "":
        return str(raw).strip().lower() in {"1", "true", "yes", "on"}

    configured = configured_site_url()
    if not configured:
        return False

    hostname = urlparse(configured).hostname or ""
    return not _host_is_staging(hostname)


def robots_content(indexable: bool) -> str:
    return "index,follow" if indexable else "noindex,follow"


def absolute_url(site_url: str, path: str = "/") -> str:
    if not path.startswith("/"):
        path = "/" + path
    if not site_url:
        return path
    return f"{site_url.rstrip('/')}{path}"


def website_json_ld(site_url: str) -> dict:
    return {
        "@context": "https://schema.org",
        "@type": "WebSite",
        "name": "Upto200",
        "url": absolute_url(site_url, "/"),
        "description": (
            "Online image optimizer and compressor for websites. "
            "Reduce JPG, PNG, WebP, and HEIC file size with a configurable target, "
            "optional WebP conversion, batch processing, and ZIP download."
        ),
    }


def web_application_json_ld(site_url: str) -> dict:
    return {
        "@context": "https://schema.org",
        "@type": "WebApplication",
        "name": "Upto200",
        "url": absolute_url(site_url, "/"),
        "applicationCategory": "MultimediaApplication",
        "operatingSystem": "Any",
        "browserRequirements": "Requires a modern browser with JavaScript enabled.",
        "description": (
            "Online image optimizer that compresses images for the web. "
            "Set a target file size, convert to WebP, JPEG, or PNG, process multiple "
            "files in one batch, and download individually or as a ZIP archive."
        ),
        "featureList": [
            "Online image compression with a configurable size target",
            "Convert images to WebP, JPEG, or PNG",
            "Supports JPG, PNG, WebP, HEIC, and HEIF uploads",
            "Batch optimization with per-file progress",
            "Side-by-side original and optimized previews",
            "Individual downloads and ZIP archive export",
        ],
    }


def json_ld_script(site_url: str) -> str:
    payload = [website_json_ld(site_url), web_application_json_ld(site_url)]
    serialized = json.dumps(payload, ensure_ascii=True, separators=(",", ":"))
    return (
        '<script type="application/ld+json">\n'
        f"{serialized}\n"
        "</script>"
    )


def render_index_html(template: str, *, site_url: str, indexable: bool) -> str:
    canonical = absolute_url(site_url, "/")
    og_image = absolute_url(site_url, "/assets/og-cover.svg")
    replacements = {
        "__SEO_ROBOTS__": robots_content(indexable),
        "__SEO_CANONICAL__": canonical,
        "__SEO_OG_URL__": canonical,
        "__SEO_OG_IMAGE__": og_image,
        "__SEO_SITE_URL__": site_url or canonical,
        "__SEO_JSON_LD__": json_ld_script(site_url or ""),
    }
    rendered = template
    for key, value in replacements.items():
        rendered = rendered.replace(key, value)
    return rendered


def robots_txt(site_url: str, indexable: bool) -> str:
    lines = [
        "User-agent: *",
        "Allow: /",
        "Disallow: /api/",
        "",
    ]
    # Allow crawling for SEO review; indexing is controlled by meta robots.
    if site_url:
        lines.append(f"Sitemap: {absolute_url(site_url, '/sitemap.xml')}")
        lines.append("")
    if not indexable:
        lines.append("# Indexing is disabled via meta robots (SEO_INDEXABLE).")
        lines.append("# Crawling remains allowed so SEO tooling can inspect the page.")
        lines.append("")
    return "\n".join(lines)


def sitemap_xml(site_url: str) -> str:
    loc = absolute_url(site_url, "/")
    # Only the public homepage belongs in the sitemap.
    return (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">\n'
        "  <url>\n"
        f"    <loc>{loc}</loc>\n"
        "    <changefreq>weekly</changefreq>\n"
        "    <priority>1.0</priority>\n"
        "  </url>\n"
        "</urlset>\n"
    )


def read_web_template(web_root: Path) -> str:
    return (web_root / "index.html").read_text(encoding="utf-8")


_PLACEHOLDER_RE = re.compile(r"__SEO_[A-Z0-9_]+__")


def assert_no_placeholders(html: str) -> None:
    leftover = _PLACEHOLDER_RE.findall(html)
    if leftover:
        raise RuntimeError(f"Unreplaced SEO placeholders: {sorted(set(leftover))}")
