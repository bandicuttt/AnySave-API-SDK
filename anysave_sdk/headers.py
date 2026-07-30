# anysave_sdk/headers.py

"""Построение browser-like headers для нестабильных CDN."""

from anysave_sdk.models import ApiFile

BROWSER_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/137.0.0.0 Safari/537.36"
)


def build_download_headers(file: ApiFile) -> dict[str, str]:
    """
    Формирует browser-like headers для нестабильных CDN.

    Особенно важно для TikTok CDN, где photomode image URL
    нестабильно отдают body без browser-like заголовков.
    """
    url = (file.url or "").lower()
    file_type = (file.type or "").lower()

    headers: dict[str, str] = {}

    if "tiktokcdn" in url or "tiktokcdn-us.com" in url:
        headers["User-Agent"] = BROWSER_USER_AGENT
        headers["Referer"] = "https://www.tiktok.com/"
        headers["Accept-Language"] = "en-US,en;q=0.9"

        if file_type in {"image", "photo", "picture"}:
            headers["Accept"] = (
                "image/avif,image/webp,image/apng,image/svg+xml,image/*,*/*;q=0.8"
            )
        else:
            headers["Accept"] = "*/*"

    return headers