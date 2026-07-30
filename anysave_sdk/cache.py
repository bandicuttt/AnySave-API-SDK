import logging

import httpx
from anysave_sdk._http import (
    build_auth_headers,
    raise_for_auth_and_rate_limit,
    unwrap_envelope,
)
from anysave_sdk.models import CachedResult


logger = logging.getLogger("api_client")


async def fetch_cache_lookup(
    *,
    api_base_url: str,
    url: str,
    mode: str = "auto",
    quality: str = "max",
    timeout_s: float = 10.0,
    api_token: str | None = None,
) -> CachedResult | None:
    """
    Проверяет наличие кэшированного результата в Telegram.

    max_file_size_mb не передаётся: CacheLookupRequest его не принимает
    (extra="forbid" → 422 Validation Error).
    Кэш ищется только по url + mode + quality.

    Returns:
        CachedResult при cache hit, None при cache miss.

    Raises:
        httpx.HTTPStatusError для 401/429.
        Остальные ошибки (5xx, network) логируются и возвращают None.
    """
    endpoint = f"{api_base_url}/downloader/cache/lookup"
    timeout = httpx.Timeout(timeout_s)

    body: dict = {
        "url": url,
        "mode": mode,
        "quality": quality,
    }

    headers = build_auth_headers(api_token)

    try:
        async with httpx.AsyncClient(
            timeout=timeout,
            follow_redirects=True,
        ) as client:
            r = await client.post(endpoint, json=body, headers=headers)

            if r.status_code == 404:
                return None

            raise_for_auth_and_rate_limit(r, label="Cache lookup")

            if r.status_code >= 500:
                logger.warning(
                    "Cache lookup server error | status=%s response=%s",
                    r.status_code,
                    r.text[:300],
                )
                return None

            if r.status_code >= 400:
                logger.warning(
                    "Cache lookup client error | status=%s response=%s",
                    r.status_code,
                    r.text[:300],
                )
                return None

            r.raise_for_status()
            data = unwrap_envelope(r.json())
            return CachedResult.model_validate(data)

    except httpx.HTTPStatusError:
        raise

    except Exception as e:
        logger.warning(
            "Cache lookup failed (non-critical) | url=%s error=%s",
            url,
            e,
        )
        return None