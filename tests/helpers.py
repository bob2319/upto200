"""Shared helpers for API tests with async job processing."""

from __future__ import annotations

import time
from typing import Any, Dict

from fastapi.testclient import TestClient

REDUCED_STATUSES = frozenset({"optimized", "target_not_reached"})


def wait_for_job(
    client: TestClient,
    payload_or_job_id: Any,
    *,
    timeout: float = 60.0,
    interval: float = 0.05,
) -> Dict[str, Any]:
    if isinstance(payload_or_job_id, dict):
        payload = payload_or_job_id
        if payload.get("status") == "completed":
            return payload
        job_id = payload["job_id"]
    else:
        job_id = str(payload_or_job_id)

    deadline = time.time() + timeout
    last: Dict[str, Any] = {}
    while time.time() < deadline:
        response = client.get(f"/api/jobs/{job_id}")
        assert response.status_code == 200, response.text
        last = response.json()
        if last.get("status") == "completed":
            return last
        time.sleep(interval)
    raise TimeoutError(f"Job {job_id} did not complete within {timeout}s: {last}")


def optimize_and_wait(client: TestClient, *, files, data=None, timeout: float = 60.0):
    response = client.post("/api/optimize", files=files, data=data or {})
    assert response.status_code == 200, response.text
    return response, wait_for_job(client, response.json(), timeout=timeout)
