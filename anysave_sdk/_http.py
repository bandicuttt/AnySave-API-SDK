# anysave_sdk/_http.py

"""
Внутренние HTTP-хелперы SDK (не публичный API).

Общая логика запросов к серверу:
- построение заголовка авторизации,
- разворачивание единого envelope-ответа,
- обработка 401/429.
"""

from __future__ import annotations

import httpx


def build_auth_headers(api_token: str | None) -> dict[str, str]:
    """Возвращает Authorization-заголовок при наличии токена, иначе пустой dict."""
    if api_token:
        return {"Authorization": f"Bearer {api_token}"}
    return {}


def unwrap_envelope(body):
    """
    Разворачивает единый API envelope.

    {"status": "ok", "data": {...}} → data
    error / legacy формат            → возвращается как есть
    """
    if not isinstance(body, dict):
        return body
    if body.get("status") == "ok" and "data" in body:
        return body["data"]
    return body


def raise_for_auth_and_rate_limit(response: httpx.Response, *, label: str = "API") -> None:
    """Бросает HTTPStatusError с понятным сообщением для 401/429."""
    if response.status_code == 401:
        raise httpx.HTTPStatusError(
            message=f"{label} authentication failed: {response.text[:200]}",
            request=response.request,
            response=response,
        )
    if response.status_code == 429:
        raise httpx.HTTPStatusError(
            message=f"{label} rate limit exceeded: {response.text[:200]}",
            request=response.request,
            response=response,
        )