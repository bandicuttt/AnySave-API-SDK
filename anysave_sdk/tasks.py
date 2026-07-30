"""
Transport-слой для task-based download API.

Отвечает за HTTP-взаимодействие с:
- POST /downloader/tasks
- GET  /downloader/tasks/{id}
- GET  /downloader/tasks/{id}/result
"""

import logging
from typing import Any

import httpx

from anysave_sdk._http import (
    build_auth_headers,
    raise_for_auth_and_rate_limit,
    unwrap_envelope,
)
from anysave_sdk.models import TaskCreateResult, TaskStatusResult

logger = logging.getLogger("api_client")


async def create_download_task(
    *,
    api_base_url: str,
    url: str,
    mode: str = "auto",
    quality: str = "max",
    use_cookies: bool = False,
    max_file_size_mb: int | None = None,
    timeout_s: float = 30.0,
    api_token: str | None = None,
) -> TaskCreateResult:
    """
    POST /downloader/tasks → TaskCreateResult.

    Raises:
        httpx.HTTPStatusError для 401/429/4xx/5xx.
    """
    endpoint = f"{api_base_url}/downloader/tasks"
    timeout = httpx.Timeout(timeout_s)

    body: dict[str, Any] = {
        "url": url,
        "mode": mode,
        "quality": quality,
        "use_cookies": use_cookies,
    }
    if max_file_size_mb is not None:
        body["max_file_size_mb"] = max_file_size_mb

    headers = build_auth_headers(api_token)

    async with httpx.AsyncClient(timeout=timeout, follow_redirects=True) as client:
        r = await client.post(endpoint, json=body, headers=headers)
        raise_for_auth_and_rate_limit(r, label="Task API")
        r.raise_for_status()
        return TaskCreateResult.model_validate(unwrap_envelope(r.json()))


async def fetch_task_status(
    *,
    api_base_url: str,
    task_id: str,
    timeout_s: float = 10.0,
    api_token: str | None = None,
) -> TaskStatusResult:
    """
    GET /downloader/tasks/{id} → TaskStatusResult.

    Raises:
        httpx.HTTPStatusError для 401/429/404/5xx.
    """
    endpoint = f"{api_base_url}/downloader/tasks/{task_id}"
    timeout = httpx.Timeout(timeout_s)
    headers = build_auth_headers(api_token)

    async with httpx.AsyncClient(timeout=timeout, follow_redirects=True) as client:
        r = await client.get(endpoint, headers=headers)
        raise_for_auth_and_rate_limit(r, label="Task API")
        r.raise_for_status()
        return TaskStatusResult.model_validate(unwrap_envelope(r.json()))


async def fetch_task_result(
    *,
    api_base_url: str,
    task_id: str,
    timeout_s: float = 30.0,
    api_token: str | None = None,
) -> dict | None:
    """
    GET /downloader/tasks/{id}/result → dict (raw DownloaderResponse) или None.

    Returns:
        Parsed JSON dict при 200, None при 202 (task_not_ready).

    Raises:
        httpx.HTTPStatusError для 401/429/404/5xx.
    """
    endpoint = f"{api_base_url}/downloader/tasks/{task_id}/result"
    timeout = httpx.Timeout(timeout_s)
    headers = build_auth_headers(api_token)

    async with httpx.AsyncClient(timeout=timeout, follow_redirects=True) as client:
        r = await client.get(endpoint, headers=headers)
        raise_for_auth_and_rate_limit(r, label="Task API")

        if r.status_code == 202:
            return None

        r.raise_for_status()
        return unwrap_envelope(r.json())