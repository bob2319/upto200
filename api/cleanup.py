"""Periodic TTL cleanup for temporary job directories."""

from __future__ import annotations

import asyncio
import logging

from api.settings import CLEANUP_INTERVAL_SECONDS
from api.storage import job_store

logger = logging.getLogger(__name__)


async def cleanup_loop(stop_event: asyncio.Event) -> None:
    while not stop_event.is_set():
        try:
            removed = await asyncio.to_thread(job_store.cleanup_expired)
            if removed:
                logger.info("Cleaned up %s expired job directories", removed)
        except Exception:  # noqa: BLE001
            logger.exception("Temp cleanup failed")
        try:
            await asyncio.wait_for(stop_event.wait(), timeout=CLEANUP_INTERVAL_SECONDS)
        except asyncio.TimeoutError:
            continue
