# anysave_sdk/transport.py

"""
HTTP-транспорт: унифицированное скачивание через aiohttp/httpx,
resume, валидация partial image.

StreamResponse — общий интерфейс HTTP response.
AiohttpStreamResponse / HttpxStreamResponse — адаптеры.
download_stream — единая функция записи stream в файл.
"""

import asyncio
import logging
import time
from contextlib import suppress
from pathlib import Path
from typing import AsyncIterator, Protocol

import aiofiles
import aiohttp
import httpx

from anysave_sdk.exceptions import IncompleteDownloadError, SlowDownloadError
from anysave_sdk.models import ApiFile, DownloadedFile
from anysave_sdk.utils import fmt_size, map_type

logger = logging.getLogger("api_client")


# ─── Stream Protocol + Adapters ──────────────────────────────


class StreamResponse(Protocol):
    """Унифицированный интерфейс HTTP response для download."""

    @property
    def status_code(self) -> int: ...

    @property
    def content_length(self) -> int | None: ...

    def iter_chunks(self, chunk_size: int) -> AsyncIterator[bytes]: ...


class AiohttpStreamResponse:
    """Адаптер aiohttp.ClientResponse → StreamResponse."""

    def __init__(self, resp: aiohttp.ClientResponse) -> None:
        self._resp = resp

    @property
    def status_code(self) -> int:
        return self._resp.status

    @property
    def content_length(self) -> int | None:
        cl = self._resp.headers.get("Content-Length")
        return int(cl) if cl and cl.isdigit() else None

    async def iter_chunks(self, chunk_size: int) -> AsyncIterator[bytes]:
        async for chunk in self._resp.content.iter_chunked(chunk_size):
            if chunk:
                yield chunk


class HttpxStreamResponse:
    """Адаптер httpx.Response → StreamResponse."""

    def __init__(self, resp: httpx.Response) -> None:
        self._resp = resp

    @property
    def status_code(self) -> int:
        return self._resp.status_code

    @property
    def content_length(self) -> int | None:
        cl = self._resp.headers.get("Content-Length")
        return int(cl) if cl and cl.isdigit() else None

    async def iter_chunks(self, chunk_size: int) -> AsyncIterator[bytes]:
        async for chunk in self._resp.aiter_bytes(chunk_size):
            if chunk:
                yield chunk


# ─── Unified download ────────────────────────────────────────


async def download_stream(
    stream: StreamResponse,
    dest: Path,
    *,
    max_size_bytes: int | None = None,
    chunk_size: int = 512 * 1024,
    min_speed_bytes_per_sec: int = 0,
    check_speed_after: float = 5.0,
) -> tuple[int, bool]:
    """
    Универсальное скачивание из stream в файл.

    Returns:
        (total_bytes, was_cut)

    Raises:
        SlowDownloadError — скорость ниже минимума
    """
    total_bytes = 0
    was_cut = False
    dl_start = time.monotonic()

    content_length = stream.content_length
    if max_size_bytes and content_length and content_length <= max_size_bytes:
        max_size_bytes = None

    async with aiofiles.open(dest, mode="wb") as f:
        async for chunk in stream.iter_chunks(chunk_size):
            await f.write(chunk)
            total_bytes += len(chunk)

            if max_size_bytes and total_bytes >= max_size_bytes:
                was_cut = True
                logger.info(
                    f"Size limit reached: {fmt_size(total_bytes)} >= "
                    f"{fmt_size(max_size_bytes)} — stopping download"
                )
                break

            if min_speed_bytes_per_sec > 0:
                elapsed = time.monotonic() - dl_start
                if elapsed >= check_speed_after:
                    speed = total_bytes / elapsed
                    if speed < min_speed_bytes_per_sec:
                        raise SlowDownloadError(
                            bytes_downloaded=total_bytes,
                            elapsed=elapsed,
                            speed=speed,
                        )

    return total_bytes, was_cut


# ─── Transport-specific wrappers ─────────────────────────────


async def download_with_aiohttp(
    session: aiohttp.ClientSession,
    url: str,
    dest: Path,
    *,
    max_size_bytes: int | None = None,
    headers: dict[str, str] | None = None,
    timeout_s: float = 900.0,
    chunk_size: int = 512 * 1024,
    min_speed_bytes_per_sec: int = 0,
) -> tuple[int, bool]:
    """
    Скачивание через aiohttp.

    Raises:
        IncompleteDownloadError — CDN оборвал body
        SlowDownloadError — скорость ниже минимума
    """
    timeout = aiohttp.ClientTimeout(total=timeout_s)

    async with session.get(
        url, allow_redirects=True, timeout=timeout, headers=headers,
    ) as resp:
        if resp.status < 200 or resp.status >= 300:
            raise RuntimeError(f"aiohttp bad status: {resp.status}")

        adapter = AiohttpStreamResponse(resp)
        try:
            return await download_stream(
                stream=adapter,
                dest=dest,
                max_size_bytes=max_size_bytes,
                chunk_size=chunk_size,
                min_speed_bytes_per_sec=min_speed_bytes_per_sec,
            )
        except (aiohttp.ClientPayloadError, aiohttp.ClientConnectionError) as e:
            raise IncompleteDownloadError(
                bytes_downloaded=dest.stat().st_size if dest.exists() else 0,
                expected_bytes=adapter.content_length,
                original=e,
            ) from e


async def download_with_httpx(
    client: httpx.AsyncClient,
    url: str,
    dest: Path,
    *,
    max_size_bytes: int | None = None,
    headers: dict[str, str] | None = None,
    chunk_size: int = 512 * 1024,
) -> tuple[int, bool]:
    """
    Скачивание через httpx.

    Raises:
        IncompleteDownloadError — сервер оборвал body
    """
    async with client.stream(
        "GET", url, follow_redirects=True, headers=headers,
    ) as resp:
        resp.raise_for_status()

        adapter = HttpxStreamResponse(resp)
        try:
            return await download_stream(
                stream=adapter,
                dest=dest,
                max_size_bytes=max_size_bytes,
                chunk_size=chunk_size,
                min_speed_bytes_per_sec=0,
            )
        except (
            httpx.RemoteProtocolError,
            httpx.ReadError,
            httpx.TransportError,
        ) as e:
            raise IncompleteDownloadError(
                bytes_downloaded=dest.stat().st_size if dest.exists() else 0,
                expected_bytes=adapter.content_length,
                original=e,
            ) from e


# ─── Resume ──────────────────────────────────────────────────


async def resume_incomplete_download(
    file: ApiFile,
    dest: Path,
    headers: dict[str, str],
    bytes_downloaded: int,
    expected_bytes: int | None,
    is_truncated: bool,
    max_file_size_bytes: int | None,
    retry_attempts: int = 3,
    timeout_s: float = 900.0,
    chunk_size: int = 512 * 1024,
    finalize_fn=None,
) -> DownloadedFile | None:
    """Пытается дозагрузить оборванный файл через Range requests."""
    if not dest.exists():
        return None

    current_size = dest.stat().st_size
    if current_size <= 0:
        return None

    if not expected_bytes or expected_bytes <= 0:
        if is_acceptable_partial_image(dest, file):
            logger.warning(
                f"Accepting partial image without known expected size: {file.filename}"
            )
            return DownloadedFile(
                type=map_type(file.type),
                path=str(dest),
                file_size_bytes=current_size,
                is_truncated=True,
            )
        return None

    if current_size >= expected_bytes and finalize_fn:
        return await finalize_fn(
            dest=dest, file=file, bytes_downloaded=current_size,
            was_cut=False, is_truncated=is_truncated,
            max_file_size_bytes=max_file_size_bytes,
        )

    for attempt in range(1, retry_attempts + 1):
        range_headers = dict(headers or {})
        range_headers["Range"] = f"bytes={current_size}-"

        logger.warning(
            f"Trying Range resume for {file.filename} "
            f"attempt={attempt}/{retry_attempts} "
            f"from={current_size} expected={expected_bytes}"
        )

        timeout = httpx.Timeout(timeout_s)
        try:
            async with httpx.AsyncClient(
                timeout=timeout, follow_redirects=True
            ) as client:
                async with client.stream(
                    "GET", file.url, headers=range_headers, follow_redirects=True,
                ) as resp:
                    if resp.status_code not in (200, 206):
                        logger.warning(
                            f"Range resume bad status for {file.filename}: "
                            f"{resp.status_code}"
                        )
                        continue

                    file_mode = "ab" if resp.status_code == 206 else "wb"
                    if resp.status_code == 200:
                        current_size = 0

                    async with aiofiles.open(dest, mode=file_mode) as f:
                        async for chunk in resp.aiter_bytes(chunk_size):
                            if chunk:
                                await f.write(chunk)
                                current_size += len(chunk)

                                if (
                                    max_file_size_bytes
                                    and current_size >= max_file_size_bytes
                                    and finalize_fn
                                ):
                                    logger.info(
                                        f"Size limit reached during resume: "
                                        f"{fmt_size(current_size)} >= "
                                        f"{fmt_size(max_file_size_bytes)}"
                                    )
                                    return await finalize_fn(
                                        dest=dest, file=file,
                                        bytes_downloaded=current_size,
                                        was_cut=True,
                                        is_truncated=is_truncated,
                                        max_file_size_bytes=max_file_size_bytes,
                                    )

            if dest.exists():
                current_size = dest.stat().st_size

            if current_size >= expected_bytes:
                logger.info(
                    f"Range resume completed for {file.filename}: "
                    f"{fmt_size(current_size)}"
                )
                if finalize_fn:
                    return await finalize_fn(
                        dest=dest, file=file,
                        bytes_downloaded=current_size, was_cut=False,
                        is_truncated=is_truncated,
                        max_file_size_bytes=max_file_size_bytes,
                    )
                return DownloadedFile(
                    type=map_type(file.type),
                    path=str(dest),
                    file_size_bytes=current_size,
                    is_truncated=is_truncated,
                )

        except Exception as e:
            logger.warning(
                f"Range resume failed for {file.filename} "
                f"| attempt={attempt}/{retry_attempts} "
                f"| {type(e).__name__}: {e}"
            )

        await asyncio.sleep(min(0.5 * attempt, 2.0))

    if is_acceptable_partial_image(dest, file):
        final_size = dest.stat().st_size if dest.exists() else current_size
        logger.warning(
            f"Accepting partial image for {file.filename}: "
            f"{fmt_size(final_size)} of expected {fmt_size(expected_bytes)}"
        )
        return DownloadedFile(
            type=map_type(file.type),
            path=str(dest),
            file_size_bytes=final_size,
            is_truncated=True,
        )

    with suppress(Exception):
        if dest.exists():
            dest.unlink()

    return None


# ─── Helpers ─────────────────────────────────────────────────


def is_acceptable_partial_image(dest: Path, file: ApiFile) -> bool:
    """Проверяет, можно ли принять частичное изображение как degraded fallback."""
    file_type = (file.type or "").lower()
    if file_type not in {"image", "photo", "picture"}:
        return False

    if not dest.exists():
        return False

    try:
        size = dest.stat().st_size
        if size < 8 * 1024:
            return False

        with open(dest, "rb") as f:
            head = f.read(16)

        if head.startswith(b"\xff\xd8\xff"):
            return True
        if head.startswith(b"\x89PNG\r\n\x1a\n"):
            return True
        if head.startswith((b"GIF87a", b"GIF89a")):
            return True
        if len(head) >= 12 and head[:4] == b"RIFF" and head[8:12] == b"WEBP":
            return True

        return False
    except Exception:
        return False