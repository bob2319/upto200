"""FastAPI entrypoint for the Upto200 image optimizer API."""

from __future__ import annotations

import asyncio
import logging
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, HTMLResponse, PlainTextResponse, Response
from fastapi.staticfiles import StaticFiles

from api.cleanup import cleanup_loop
from api.routes import router
from api.seo import (
    assert_no_placeholders,
    is_indexable,
    public_origin_from_request,
    read_web_template,
    render_index_html,
    robots_txt,
    sitemap_xml,
)
from api.settings import TEMP_ROOT

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s [%(name)s] %(message)s",
)
logger = logging.getLogger(__name__)
WEB_ROOT = Path(__file__).resolve().parents[1] / "web"


@asynccontextmanager
async def lifespan(app: FastAPI):
    TEMP_ROOT.mkdir(parents=True, exist_ok=True)
    stop_event = asyncio.Event()
    task = asyncio.create_task(cleanup_loop(stop_event))
    logger.info("Temp root: %s", TEMP_ROOT)
    try:
        yield
    finally:
        stop_event.set()
        await task


app = FastAPI(
    title="Upto200 API",
    description="Optimize images for the web to a configurable target file size.",
    version="0.1.0",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(router, prefix="/api")


def _site_context(request: Request) -> tuple[str, bool]:
    site_url = public_origin_from_request(
        base_url=str(request.base_url),
        headers=dict(request.headers),
    )
    return site_url, is_indexable(site_url)


@app.get("/", response_class=HTMLResponse)
def index_page(request: Request) -> HTMLResponse:
    site_url, indexable = _site_context(request)
    html = render_index_html(
        read_web_template(WEB_ROOT),
        site_url=site_url,
        indexable=indexable,
    )
    assert_no_placeholders(html)
    headers = {"Cache-Control": "no-cache"}
    if not indexable:
        headers["X-Robots-Tag"] = "noindex, follow"
    return HTMLResponse(content=html, headers=headers)


@app.get("/robots.txt", response_class=PlainTextResponse)
def robots(request: Request) -> PlainTextResponse:
    site_url, indexable = _site_context(request)
    body = robots_txt(site_url, indexable)
    headers = {"Cache-Control": "public, max-age=300"}
    if not indexable:
        headers["X-Robots-Tag"] = "noindex, follow"
    return PlainTextResponse(content=body, media_type="text/plain", headers=headers)


@app.get("/sitemap.xml")
def sitemap(request: Request) -> Response:
    site_url, indexable = _site_context(request)
    body = sitemap_xml(site_url)
    headers = {"Cache-Control": "public, max-age=300"}
    if not indexable:
        headers["X-Robots-Tag"] = "noindex, follow"
    return Response(content=body, media_type="application/xml", headers=headers)


@app.get("/favicon.ico", include_in_schema=False)
def favicon_ico() -> FileResponse:
    return FileResponse(
        WEB_ROOT / "assets" / "favicon.svg",
        media_type="image/svg+xml",
        headers={"Cache-Control": "public, max-age=86400"},
    )


@app.get("/favicon.svg", include_in_schema=False)
def favicon_svg() -> FileResponse:
    return FileResponse(
        WEB_ROOT / "assets" / "favicon.svg",
        media_type="image/svg+xml",
        headers={"Cache-Control": "public, max-age=86400"},
    )


app.mount("/css", StaticFiles(directory=WEB_ROOT / "css"), name="css")
app.mount("/js", StaticFiles(directory=WEB_ROOT / "js"), name="js")
app.mount("/assets", StaticFiles(directory=WEB_ROOT / "assets"), name="assets")
