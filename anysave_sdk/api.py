# anysave_sdk/api.py

"""Коммуникация с API сервера."""

import logging
import httpx

from typing import Optional

from anysave_sdk._http import (
    build_auth_headers,
    raise_for_auth_and_rate_limit,
    unwrap_envelope,
)

from anysave_sdk.models import (
    ApiFile,
    ApiPickerResponse,
    ApiTunnelResponse,
    DownloadResult,
    ClientError,
    DownloadStatus
)
from anysave_sdk.error_codes import ClientErrorCode

logger = logging.getLogger("api_client")


def _build_download_body(
    url: str,
    mode: str,
    quality: str,
    use_cookies: bool,
    max_file_size_mb: int | None,
) -> dict:
    """
    Тело запроса для /downloader/ и /downloader/tasks.
    """
    body: dict = {
        "url": url,
        "mode": mode,
        "quality": quality,
        "use_cookies": use_cookies,
    }
    if max_file_size_mb is not None:
        body["max_file_size_mb"] = max_file_size_mb
    return body


def _build_links_body(
    url: str,
    mode: str,
    quality: str,
    use_cookies: bool,
) -> dict:
    """
    Тело запроса для /downloader/links.
    """
    return {
        "url": url,
        "mode": mode,
        "quality": quality,
        "use_cookies": use_cookies,
    }


async def _post_download_endpoint(
    *,
    endpoint: str,
    body: dict,
    headers: dict[str, str],
    timeout_s: float,
    url: str,
    label: str,
) -> dict:
    """
    Общий POST к download-эндпоинтам сервера.

    4xx-ответы логируются как warning (это ожидаемое отклонение запроса,
    например 422 Unprocessable Entity — не повод шуметь ERROR-ами).
    """
    timeout = httpx.Timeout(timeout_s)

    async with httpx.AsyncClient(timeout=timeout, follow_redirects=True) as client:
        r = await client.post(endpoint, json=body, headers=headers)

        raise_for_auth_and_rate_limit(r, label=label)

        if 400 <= r.status_code < 500:
            logger.warning(
                "%s rejected request | status=%s url=%s response=%s",
                label,
                r.status_code,
                url,
                r.text[:500],
            )

        r.raise_for_status()
        return unwrap_envelope(r.json())


async def fetch_api_response(
    api_base_url: str,
    url: str,
    mode: str = "auto",
    quality: str = "max",
    use_cookies: bool = False,
    max_file_size_mb: int | None = None,
    timeout_s: float = 900.0,
    api_token: str | None = None,
) -> dict:
    """Запрос к /downloader/ (сервер скачивает файл локально)."""
    return await _post_download_endpoint(
        endpoint=f"{api_base_url}/downloader/",
        body=_build_download_body(url, mode, quality, use_cookies, max_file_size_mb),
        headers=build_auth_headers(api_token),
        timeout_s=timeout_s,
        url=url,
        label="Downloader API",
    )


async def fetch_links_api_response(
    api_base_url: str,
    url: str,
    mode: str = "auto",
    quality: str = "max",
    use_cookies: bool = False,
    timeout_s: float = 60.0,
    api_token: str | None = None,
) -> dict:
    """
    Запрос к /downloader/links (fast-link endpoint).

    Короткий таймаут по умолчанию (60s) — сервер не скачивает файл.
    max_file_size_mb не передаётся: сервер его не принимает на этом эндпоинте.
    """
    return await _post_download_endpoint(
        endpoint=f"{api_base_url}/downloader/links",
        body=_build_links_body(url, mode, quality, use_cookies),
        headers=build_auth_headers(api_token),
        timeout_s=timeout_s,
        url=url,
        label="Links API",
    )


def extract_remote_files(
    data: dict,
) -> tuple[list["ApiFile"], Optional["ApiFile"], bool] | None:
    """
    Парсит API-ответ /downloader/links и извлекает remote файлы.

    Логика та же, что у extract_files — переиспользуем те же схемы.
    """
    return extract_files(data)


def extract_files(
    data: dict,
) -> tuple[list[ApiFile], Optional[ApiFile], bool] | None:
    """Парсит API-ответ и извлекает файлы."""
    status = (data.get("status") or "").lower()

    if status == "tunnel":
        parsed = ApiTunnelResponse.model_validate(data)
        return [parsed.file], None, parsed.is_truncated

    if status == "picker":
        parsed = ApiPickerResponse.model_validate(data)
        return list(parsed.files), parsed.audio, parsed.is_truncated

    return None


def make_api_error_result(exc: Exception, api_base_url: str, timeout_s: float) -> DownloadResult:
    """Создаёт DownloadResult из ошибки API-запроса."""
    if isinstance(exc, httpx.ConnectTimeout):
        return DownloadResult(
            status=DownloadStatus.ERROR,
            files=[],
            error=ClientError(
                code=ClientErrorCode.API_CONNECT_TIMEOUT.value,
                detail=f"Connection timeout after {timeout_s}s",
            ),
        )

    if isinstance(exc, httpx.ReadTimeout):
        return DownloadResult(
            status=DownloadStatus.ERROR,
            files=[],
            error=ClientError(
                code=ClientErrorCode.API_READ_TIMEOUT.value,
                detail=(
                    f"Read timeout after {timeout_s}s — "
                    f"server is likely still processing"
                ),
            ),
        )

    if isinstance(exc, httpx.ConnectError):
        return DownloadResult(
            status=DownloadStatus.ERROR,
            files=[],
            error=ClientError(
                code=ClientErrorCode.API_CONNECT_ERROR.value,
                detail=f"Cannot connect to API at {api_base_url}: {exc}",
            ),
        )

    if isinstance(exc, httpx.HTTPStatusError):
        status_code = exc.response.status_code

        if status_code == 401:
            return DownloadResult(
                status=DownloadStatus.ERROR,
                files=[],
                error=ClientError(
                    code=ClientErrorCode.API_UNAUTHORIZED.value,
                    detail="Invalid or missing API token",
                    status=401,
                ),
            )

        if status_code == 429:
            retry_after = exc.response.headers.get("Retry-After")
            detail = "Rate limit exceeded"
            if retry_after:
                detail = f"Rate limit exceeded. Retry after {retry_after}s"

            return DownloadResult(
                status=DownloadStatus.ERROR,
                files=[],
                error=ClientError(
                    code=ClientErrorCode.API_RATE_LIMITED.value,
                    detail=detail,
                    status=429,
                ),
            )

        return DownloadResult(
            status=DownloadStatus.ERROR,
            files=[],
            error=ClientError(
                code=ClientErrorCode.API_HTTP_ERROR.value,
                detail=f"HTTP {status_code}: {exc.response.text[:200]}",
                status=status_code,
            ),
        )

    if isinstance(exc, httpx.TimeoutException):
        return DownloadResult(
            status=DownloadStatus.ERROR,
            files=[],
            error=ClientError(
                code=ClientErrorCode.API_TIMEOUT.value,
                detail=f"API timeout after {timeout_s}s: {type(exc).__name__}",
            ),
        )

    return DownloadResult(
        status=DownloadStatus.ERROR,
        files=[],
        error=ClientError(
            code=ClientErrorCode.API_REQUEST_FAILED.value,
            detail=f"{type(exc).__name__}: {exc}",
        ),
    )