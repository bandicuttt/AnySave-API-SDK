# anysave_sdk/search.py

"""
Transport-слой Search API.

Эндпоинты:

- ``POST /search/music``   — поиск треков (Deezer → iTunes fallback)
- ``POST /search/youtube`` — поиск видео YouTube (yt-dlp)
- ``GET  /search/chart``   — чарт Deezer (глобальный / по стране)

Общая логика:

- локальная валидация параметров (ловим ошибку до запроса и списания токенов),
- единый envelope ``{"status": "ok", "data": ...}``,
- все не-2xx ответы и сетевые ошибки → :class:`SearchError`,
- 429/502 ретраятся с backoff (спека: max 3 attempts), затем — ошибка.
"""

from __future__ import annotations

import asyncio
import logging
from typing import TypeVar

import httpx
from pydantic import BaseModel, ValidationError

from anysave_sdk._http import build_auth_headers, unwrap_envelope
from anysave_sdk.error_codes import ClientErrorCode
from anysave_sdk.exceptions import SearchError
from anysave_sdk.models import MusicChartResponse, MusicSearchResponse, YoutubeSearchResponse

logger = logging.getLogger("api_client")

_T = TypeVar("_T", bound=BaseModel)

# Дефолтные таймауты (сек). YouTube — yt-dlp subprocess на сервере, даём больше.
MUSIC_TIMEOUT_S = 30.0
YOUTUBE_TIMEOUT_S = 60.0
CHART_TIMEOUT_S = 30.0

# Ограничения backoff (спека: 429 → Retry-After, 502 → exponential, max 3 attempts).
_MAX_RETRY_AFTER_S = 60.0
_MAX_BACKOFF_S = 8.0

# Fallback-коды для ответов без parseable error envelope.
_STATUS_FALLBACK_CODES: dict[int, str] = {
    400: "bad_request",
    401: "unauthorized",
    403: "access_forbidden",
    404: "not_found",
    422: "validation_error",
    429: "rate_limit_exceeded",
    500: "internal_error",
    502: "upstream_error",
    503: "service_unavailable",
}


# ─── Local validation (developer errors до списания токенов) ──


def _validate_query(query: str, *, max_length: int) -> str:
    if not isinstance(query, str) or not query.strip():
        raise ValueError("query: must be a non-empty string")
    value = query.strip()
    if len(value) > max_length:
        raise ValueError(
            f"query: max length is {max_length} characters, got {len(value)}"
        )
    return value


def _validate_limit(limit: int) -> int:
    if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= 25:
        raise ValueError(f"limit: must be an integer between 1 and 25, got {limit!r}")
    return limit


def _validate_cursor(cursor: str | None) -> str | None:
    if cursor is not None and not isinstance(cursor, str):
        raise ValueError(
            "cursor: must be an opaque string from a previous next_cursor "
            f"or None, got {type(cursor).__name__}"
        )
    return cursor





# ─── Response helpers ─────────────────────────────────────────


def _parse(response: httpx.Response, model: type[_T], *, label: str) -> _T:
    """Разворачивает envelope и валидирует ответ моделью."""
    try:
        payload = response.json()
    except ValueError as exc:
        raise SearchError(
            code=ClientErrorCode.UNKNOWN_RESPONSE.value,
            detail=f"{label}: non-JSON response: {response.text[:200]!r}",
            status=response.status_code,
        ) from exc

    data = unwrap_envelope(payload)
    try:
        return model.model_validate(data)
    except ValidationError as exc:
        raise SearchError(
            code=ClientErrorCode.UNKNOWN_RESPONSE.value,
            detail=f"{label}: unexpected response shape: {str(exc)[:300]}",
            status=response.status_code,
        ) from exc


def _retry_after_seconds(response: httpx.Response) -> float | None:
    """Parses Retry-After header (seconds) → float | None."""
    raw = response.headers.get("Retry-After")
    if not raw:
        return None
    try:
        return max(0.0, float(raw))
    except ValueError:
        return None


def _retry_delay(response: httpx.Response, attempt: int) -> float:
    """Задержка перед retry: 429 → Retry-After, иначе exponential backoff."""
    if response.status_code == 429:
        retry_after = _retry_after_seconds(response)
        if retry_after is not None:
            return min(retry_after, _MAX_RETRY_AFTER_S)
    return min(2.0**attempt, _MAX_BACKOFF_S)


def _search_error(response: httpx.Response) -> SearchError:
    """Собирает SearchError из не-2xx ответа (envelope ошибки или fallback)."""
    code = _STATUS_FALLBACK_CODES.get(
        response.status_code, f"http_{response.status_code}"
    )
    detail: str | None = None
    extra: dict | None = None

    try:
        body = response.json()
    except ValueError:
        body = None

    if isinstance(body, dict):
        err = body.get("error")
        if isinstance(err, dict):
            code = str(err.get("code") or code)
            detail = err.get("detail")
            extra = err.get("extra")

    if not detail:
        detail = (response.text or "").strip()[:200] or None

    retry_after: float | None = None
    if response.status_code == 429:
        retry_after = _retry_after_seconds(response)

    return SearchError(
        code=code,
        detail=detail,
        status=response.status_code,
        extra=extra,
        retry_after=retry_after,
    )


# ─── Core request ────────────────────────────────────────────


async def _send_once(
    *,
    method: str,
    endpoint: str,
    headers: dict[str, str],
    json_body: dict | None,
    params: dict[str, str] | None,
    timeout_s: float,
    label: str,
) -> httpx.Response:
    timeout = httpx.Timeout(timeout_s)
    try:
        async with httpx.AsyncClient(timeout=timeout, follow_redirects=True) as client:
            if method == "GET":
                return await client.get(endpoint, params=params, headers=headers)
            return await client.post(endpoint, json=json_body, headers=headers)
    except httpx.TimeoutException as exc:
        raise SearchError(
            code=ClientErrorCode.API_TIMEOUT.value,
            detail=f"{label}: timeout after {timeout_s}s",
        ) from exc
    except httpx.ConnectError as exc:
        raise SearchError(
            code=ClientErrorCode.API_CONNECT_ERROR.value,
            detail=f"{label}: cannot connect to {endpoint}: {exc}",
        ) from exc
    except httpx.HTTPError as exc:
        raise SearchError(
            code=ClientErrorCode.API_REQUEST_FAILED.value,
            detail=f"{label}: {type(exc).__name__}: {exc}",
        ) from exc


async def _request(
    *,
    method: str,
    endpoint: str,
    label: str,
    timeout_s: float,
    max_retries: int,
    api_token: str | None,
    json_body: dict | None = None,
    params: dict[str, str] | None = None,
) -> httpx.Response:
    """
    Один запрос к Search API с ретраями транзиентных ошибок.

    По спеке error_handling_guidelines:

    - 429 — honour ``Retry-After`` (fallback: exponential backoff),
    - 502 — exponential backoff,
    - всего ``max_retries + 1`` попыток (по умолчанию 3),
    - затем — :class:`SearchError`.
    """
    headers = build_auth_headers(api_token)
    attempts = max(0, int(max_retries)) + 1

    response: httpx.Response | None = None
    for attempt in range(attempts):
        response = await _send_once(
            method=method,
            endpoint=endpoint,
            headers=headers,
            json_body=json_body,
            params=params,
            timeout_s=timeout_s,
            label=label,
        )

        if response.status_code in {429, 502} and attempt < attempts - 1:
            delay = _retry_delay(response, attempt)
            logger.warning(
                "%s transient error | status=%s attempt=%d/%d retry_in=%.1fs",
                label,
                response.status_code,
                attempt + 1,
                attempts,
                delay,
            )
            await asyncio.sleep(delay)
            continue
        break

    assert response is not None  # attempts >= 1
    if not 200 <= response.status_code < 300:
        raise _search_error(response)

    return response


# ─── Public transport functions ──────────────────────────────


async def fetch_music_search(
    *,
    api_base_url: str,
    query: str,
    limit: int = 10,
    cursor: str | None = None,
    timeout_s: float = MUSIC_TIMEOUT_S,
    api_token: str | None = None,
    max_retries: int = 2,
) -> MusicSearchResponse:
    """
    ``POST /search/music`` — поиск треков по текстовому запросу.

    Deezer с автоматическим fallback на iTunes (описано на стороне сервера).
    Пустой результат — успешный ответ (200), а не ошибка.

    Args:
        api_base_url: Базовый URL API (без слэша на конце).
        query:        Запрос (название, исполнитель или комбинация), 1..200 символов.
        limit:        Максимум результатов на страницу, 1..25 (default 10).
        cursor:       Непрозрачный ``next_cursor`` из предыдущего ответа (пагинация).
        timeout_s:    Таймаут запроса.
        api_token:    Bearer токен.
        max_retries:  Ретраев для 429/502 (default 2 → до 3 попыток).

    Returns:
        :class:`MusicSearchResponse`.

    Raises:
        ValueError:  локальная ошибка валидации query/limit/cursor.
        SearchError: ошибка API (401/403/422/429/502) или сетевая ошибка.
    """
    query = _validate_query(query, max_length=200)
    limit = _validate_limit(limit)
    cursor = _validate_cursor(cursor)

    body: dict = {"query": query, "limit": limit}
    if cursor is not None:
        body["cursor"] = cursor

    response = await _request(
        method="POST",
        endpoint=f"{api_base_url}/search/music",
        label="Search music",
        timeout_s=timeout_s,
        max_retries=max_retries,
        api_token=api_token,
        json_body=body,
    )
    return _parse(response, MusicSearchResponse, label="Search music")


async def fetch_youtube_search(
    *,
    api_base_url: str,
    query: str,
    limit: int = 5,
    cursor: str | None = None,
    timeout_s: float = YOUTUBE_TIMEOUT_S,
    api_token: str | None = None,
    max_retries: int = 2,
) -> YoutubeSearchResponse:
    """
    ``POST /search/youtube`` — поиск видео YouTube (yt-dlp flat-playlist).

    Эндпоинт намеренно универсальный — принимает любой текстовый запрос.
    Для музыки передавайте ``yt_query`` из :func:`fetch_music_search`.
    Пагинация в v1 не поддерживается (``next_cursor`` всегда ``None``).

    Args:
        api_base_url: Базовый URL API (без слэша на конце).
        query:        Запрос, 1..300 символов.
        limit:        Максимум результатов, 1..25 (default 5).
        cursor:       Зарезервировано для будущих версий (v1: не поддерживается).
        timeout_s:    Таймаут запроса.
        api_token:    Bearer токен.
        max_retries:  Ретраев для 429/502 (default 2 → до 3 попыток).

    Returns:
        :class:`YoutubeSearchResponse`.

    Raises:
        ValueError:  локальная ошибка валидации query/limit/cursor.
        SearchError: ошибка API (401/403/422/429/502) или сетевая ошибка.
    """
    query = _validate_query(query, max_length=300)
    limit = _validate_limit(limit)
    cursor = _validate_cursor(cursor)

    body: dict = {"query": query, "limit": limit}
    if cursor is not None:
        body["cursor"] = cursor

    response = await _request(
        method="POST",
        endpoint=f"{api_base_url}/search/youtube",
        label="Search youtube",
        timeout_s=timeout_s,
        max_retries=max_retries,
        api_token=api_token,
        json_body=body,
    )
    return _parse(response, YoutubeSearchResponse, label="Search youtube")


async def fetch_music_chart(
    *,
    api_base_url: str,
    limit: int = 10,
    cursor: str | None = None,
    timeout_s: float = CHART_TIMEOUT_S,
    api_token: str | None = None,
    max_retries: int = 2,
) -> MusicChartResponse:
    """
    ``GET /search/chart`` — глобальный чарт Deezer.

    Fallback на iTunes отсутствует —
    при недоступности Deezer сервер вернёт 502 ``chart_unavailable``
    (обрабатывается ретраями, затем :class:`SearchError`).

    Args:
        api_base_url: Базовый URL API (без слэша на конце).
        limit:        Максимум треков, 1..25 (default 10).
        cursor:       Непрозрачный ``next_cursor`` из предыдущего ответа.
        timeout_s:    Таймаут запроса.
        api_token:    Bearer токен.
        max_retries:  Ретраев для 429/502 (default 2 → до 3 попыток).

    Returns:
        :class:`MusicChartResponse`.

    Raises:
        ValueError:  локальная ошибка валидации limit/cursor.
        SearchError: ошибка API (401/403/422/429/502) или сетевая ошибка.
    """
    limit = _validate_limit(limit)
    cursor = _validate_cursor(cursor)

    params: dict[str, str] = {"limit": str(limit)}
    if cursor is not None:
        params["cursor"] = cursor

    response = await _request(
        method="GET",
        endpoint=f"{api_base_url}/search/chart",
        label="Search chart",
        timeout_s=timeout_s,
        max_retries=max_retries,
        api_token=api_token,
        params=params,
    )
    return _parse(response, MusicChartResponse, label="Search chart")



