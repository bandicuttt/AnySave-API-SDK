# anysave_sdk/downloader.py

"""
Orchestrator клиента скачивания.

AnySaveClient выбирает стратегию (cache / fast-link / tasks / sync)
и делегирует физическое скачивание в DownloadPipeline.
"""

from __future__ import annotations

import asyncio
import logging
import time
from pathlib import Path

import httpx

from anysave_sdk._concurrency import LocalProcessingLimiter
from anysave_sdk.api import fetch_api_response, fetch_links_api_response, make_api_error_result
from anysave_sdk.cache import fetch_cache_lookup
from anysave_sdk.download_pipeline import DownloadPipeline
from anysave_sdk.error_codes import ClientErrorCode
from anysave_sdk.exceptions import ConcurrencyLimitTimeoutError
from anysave_sdk.models import (
    CachedResult,
    ClientError,
    DownloadResult,
    DownloadStatus,
    RemoteDownloadResult,
)
from anysave_sdk.tasks import create_download_task, fetch_task_result

logger = logging.getLogger("api_client")


def _log_api_failure(context: str, exc: Exception) -> None:
    """
    Логирует сбой API на корректном уровне.

    4xx (ожидаемые отклонения, напр. 422) → warning.
    Всё остальное (сеть, таймауты, 5xx) → error.
    """
    if isinstance(exc, httpx.HTTPStatusError) and 400 <= exc.response.status_code < 500:
        logger.warning("%s: %s: %s", context, type(exc).__name__, exc)
    else:
        logger.error("%s: %s: %s", context, type(exc).__name__, exc)


class AnySaveClient:
    """
    Асинхронный клиент для скачивания медиа через downloader API.

    Рекомендуемый метод — ``smart_download()``: автоматически выбирает
    оптимальную стратегию и делает fallback при сбоях.

    Быстрый старт::

        anysave = AnySaveClient(
            api_base_url="https://api.example.com",
            api_token="YOUR_TOKEN",
            download_dir="./downloads",
        )
        result = await anysave.smart_download("https://youtu.be/dQw4w9WgXcQ")
        if result.ok:
            for f in result.files:
                print(f.type, f.path)

    Методы:
        - ``smart_download()``      — рекомендуется, auto-fallback.
        - ``download_links()``      — только fast-link путь.
        - ``download_via_tasks()``  — только task pipeline.
        - ``download()``            — только sync endpoint.
        - ``get_remote_links()``    — CDN-ссылки без скачивания.

    Требования окружения:
        ffmpeg и ffprobe в PATH (для HLS, merge, truncate, thumbnails).
    """

    def __init__(
        self,
        api_base_url: str,
        download_dir: str | Path | None = None,
        chunk_size: int = 1024 * 512,
        max_concurrency: int = 4,
        concurrency_max_ttl_s: float = 60.0,
        api_timeout_s: float = 900.0,
        download_timeout_s: float = 900.0,
        min_speed_bytes_per_sec: int = 10 * 1024,
        download_retry_attempts: int = 3,
        thumbnail_placeholder: str | None = None,
        api_token: str | None = None,
        prefer_telegram_cache: bool = False,
        cache_lookup_timeout_s: float = 10.0,
        task_poll_interval_s: float = 1.0,
        task_max_poll_time_s: float = 900.0,
        task_create_timeout_s: float = 30.0,
    ) -> None:
        self.api_base_url = (api_base_url or "").rstrip("/")
        self.download_dir = Path(download_dir) if download_dir is not None else Path.cwd()
        self.api_timeout_s = float(api_timeout_s)
        self.api_token = api_token
        self.prefer_telegram_cache = bool(prefer_telegram_cache)
        self.cache_lookup_timeout_s = float(cache_lookup_timeout_s)
        self.task_poll_interval_s = float(task_poll_interval_s)
        self.task_max_poll_time_s = float(task_max_poll_time_s)
        self.task_create_timeout_s = float(task_create_timeout_s)
        self.thumbnail_placeholder = thumbnail_placeholder

        self.max_concurrency = max(1, int(max_concurrency))
        self.concurrency_max_ttl_s = float(concurrency_max_ttl_s)
        if self.concurrency_max_ttl_s <= 0:
            raise ValueError("concurrency_max_ttl_s must be > 0")

        self._local_processing_limiter = LocalProcessingLimiter(
            max_concurrency=self.max_concurrency,
            wait_timeout_s=self.concurrency_max_ttl_s,
        )

        self._pipeline = DownloadPipeline(
            download_dir=self.download_dir,
            chunk_size=int(chunk_size),
            max_concurrency=self.max_concurrency,
            download_timeout_s=float(download_timeout_s),
            min_speed_bytes_per_sec=int(min_speed_bytes_per_sec),
            download_retry_attempts=max(1, int(download_retry_attempts)),
            thumbnail_placeholder=thumbnail_placeholder,
        )

    # ─── Public API ──────────────────────────────────────────

    async def smart_download(
        self,
        url: str,
        mode: str = "auto",
        quality: str = "max",
        use_cookies: bool = False,
        max_file_size_mb: int | None = None,
        _skip_cache: bool = False,
    ) -> DownloadResult:
        """
        Рекомендуемый метод — выбирает стратегию автоматически.

        Порядок попыток:

        1. **Telegram cache** (если ``prefer_telegram_cache=True`` и ``_skip_cache=False``):
        Проверяется ровно один раз. При попадании мгновенно возвращается
        ``status=CACHED`` с ``file_id``.

        2. **Fast-link** (``/downloader/links``):
        Сервер отдаёт CDN-ссылки, SDK скачивает и обрабатывает файлы локально.
        Весь local pipeline ограничен глобальным concurrency limiter'ом.

        3. **Task fallback** (``/downloader/tasks``):
        Если fast-link не справился, local slot не освободился за TTL
        или локальная обработка упала — создаётся серверная задача,
        где heavy operations выполняются на стороне API.

        Args:
            url: Ссылка на медиа (YouTube, TikTok, Instagram, Twitter/X и др.).
            mode: ``"auto"`` | ``"audio"`` | ``"video"``.
            quality: ``"max"`` | ``"2160"`` | ``"1440"`` | ``"1080"`` |
                    ``"720"`` | ``"480"`` | ``"360"``.
            use_cookies: Серверные cookies для приватного контента.
            max_file_size_mb:
                Лимит размера в MB. При fast-link — SDK обрезает локально
                через ffmpeg. При fallback ``/downloader/tasks`` — сервер
                выполняет обработку на своей стороне.
            _skip_cache:
                Внутренний флаг для принудительного пропуска Telegram cache lookup.
                Нужен сценариям, которым обязательно требуются локальные файлы.

        Returns:
            :class:`DownloadResult`:

            - ``SUCCESS`` — файлы в ``result.files``.
            - ``PARTIAL`` — часть файлов скачана, ``result.error`` описывает потери.
            - ``CACHED`` — ``result.cached`` содержит ``file_id`` для пересылки.
            - ``ERROR`` — оба метода не дали результата, см. ``result.error_msg``.

        Raises:
            Не выбрасывает исключений — все ошибки через ``DownloadResult(status=ERROR)``.
        """
        if self.prefer_telegram_cache and not _skip_cache:
            cached = await self._try_cache_lookup(url=url, mode=mode, quality=quality)
            if cached is not None:
                return DownloadResult(
                    status=DownloadStatus.CACHED,
                    files=[],
                    cached=cached,
                )

        # 1-я попытка: fast-link + локальная обработка
        result = await self.download_links(
            url=url,
            mode=mode,
            quality=quality,
            use_cookies=use_cookies,
            max_file_size_mb=max_file_size_mb,
            _skip_cache=True,
        )

        if result.ok:
            return result

        logger.info(
            "Fast-link failed (%s), falling back to task pipeline | url=%s",
            result.error_msg,
            url,
        )

        # 2-я попытка: task pipeline
        return await self.download_via_tasks(
            url=url,
            mode=mode,
            quality=quality,
            use_cookies=use_cookies,
            max_file_size_mb=max_file_size_mb,
            _skip_cache=True,
        )

    async def download(
        self,
        url: str,
        mode: str = "auto",
        quality: str = "max",
        use_cookies: bool = False,
        max_file_size_mb: int | None = None,
        _skip_cache: bool = False,
    ) -> DownloadResult:
        """
        Прямое скачивание через ``/downloader/`` (сервер качает на своей стороне).

        Args:
            url: Ссылка на медиа.
            mode: ``"auto"`` | ``"audio"`` | ``"video"``.
            quality: ``"max"`` | ``"2160"`` | ``"1440"`` | ``"1080"`` |
                     ``"720"`` | ``"480"`` | ``"360"``.
            use_cookies: Серверные cookies.
            max_file_size_mb: Лимит размера в MB (сервер обрежет).

        Returns:
            :class:`DownloadResult`.
        """
        if not self.api_base_url:
            return DownloadResult(
                status=DownloadStatus.ERROR,
                files=[],
                error=ClientError(code=ClientErrorCode.NO_API_URL.value),
            )

        if self.prefer_telegram_cache and not _skip_cache:
            cached = await self._try_cache_lookup(url=url, mode=mode, quality=quality)
            if cached is not None:
                return DownloadResult(
                    status=DownloadStatus.CACHED,
                    files=[],
                    cached=cached,
                )

        total_start = time.monotonic()
        api_start = time.monotonic()

        try:
            data = await fetch_api_response(
                api_base_url=self.api_base_url,
                url=url,
                mode=mode,
                quality=quality,
                use_cookies=use_cookies,
                max_file_size_mb=max_file_size_mb,
                timeout_s=self.api_timeout_s,
                api_token=self.api_token,
            )
        except Exception as exc:
            _log_api_failure("API request failed", exc)
            return make_api_error_result(exc, self.api_base_url, self.api_timeout_s)

        logger.info(
            "API response: %.2fs | status=%s",
            time.monotonic() - api_start,
            data.get("status"),
        )

        return await self._pipeline.process_api_response(data, max_file_size_mb, total_start)

    async def download_via_tasks(
        self,
        url: str,
        mode: str = "auto",
        quality: str = "max",
        use_cookies: bool = False,
        max_file_size_mb: int | None = None,
        _skip_cache: bool = False,
    ) -> DownloadResult:
        """
        Скачивание через очередь задач: create → poll → result → download.

        Args:
            url: Ссылка на медиа.
            mode: ``"auto"`` | ``"audio"`` | ``"video"``.
            quality: ``"max"`` | ``"2160"`` | ``"1440"`` | ``"1080"`` |
                     ``"720"`` | ``"480"`` | ``"360"``.
            use_cookies: Серверные cookies.
            max_file_size_mb: Лимит размера в MB.

        Returns:
            :class:`DownloadResult`. При таймауте: ``code=task_timeout``.
        """
        if not self.api_base_url:
            return DownloadResult(
                status=DownloadStatus.ERROR,
                files=[],
                error=ClientError(code=ClientErrorCode.NO_API_URL.value),
            )

        if self.prefer_telegram_cache and not _skip_cache:
            cached = await self._try_cache_lookup(url=url, mode=mode, quality=quality)
            if cached is not None:
                return DownloadResult(
                    status=DownloadStatus.CACHED,
                    files=[],
                    cached=cached,
                )

        total_start = time.monotonic()

        try:
            task = await create_download_task(
                api_base_url=self.api_base_url,
                url=url,
                mode=mode,
                quality=quality,
                use_cookies=use_cookies,
                max_file_size_mb=max_file_size_mb,
                timeout_s=self.task_create_timeout_s,
                api_token=self.api_token,
            )
        except Exception as exc:
            _log_api_failure("Task create failed", exc)
            return _task_error_result(exc, ClientErrorCode.TASK_CREATE_FAILED)

        logger.info("Task created | task_id=%s", task.task_id)

        data = await self._poll_until_ready(task.task_id, total_start)
        if data is None:
            return DownloadResult(
                status=DownloadStatus.ERROR,
                files=[],
                error=ClientError(
                    code=ClientErrorCode.TASK_TIMEOUT.value,
                    detail=f"Task did not complete within {self.task_max_poll_time_s}s",
                ),
            )

        return await self._pipeline.process_api_response(data, max_file_size_mb, total_start)

    async def get_remote_links(
        self,
        url: str,
        mode: str = "auto",
        quality: str = "max",
        use_cookies: bool = False,
        links_timeout_s: float = 60.0,
        _skip_cache: bool = False,
    ) -> RemoteDownloadResult:
        """
        Low-level: возвращает CDN-ссылки без скачивания.

        .. note::
            ``max_file_size_mb`` не передаётся — ``/downloader/links`` его не принимает.
            Для truncate используйте ``download_links()``.

        Args:
            url: Ссылка на медиа.
            mode: ``"auto"`` | ``"audio"`` | ``"video"``.
            quality: ``"max"`` | ``"2160"`` | ``"1440"`` | ``"1080"`` |
                     ``"720"`` | ``"480"`` | ``"360"``.
            use_cookies: Серверные cookies.
            links_timeout_s: Таймаут запроса (по умолчанию 60s).

        Returns:
            :class:`RemoteDownloadResult` со статусами ``SUCCESS`` | ``CACHED`` | ``ERROR``.

        Examples:
            Прямая ссылка без скачивания::

                remote = await anysave.get_remote_links("https://youtu.be/dQw4w9WgXcQ")
                if remote.ok and not remote.files[0].is_hls_manifest:
                    await bot.send_video(chat_id, remote.files[0].url)
        """
        if not self.api_base_url:
            return RemoteDownloadResult(
                status=DownloadStatus.ERROR,
                files=[],
                error=ClientError(code=ClientErrorCode.NO_API_URL.value),
            )

        if self.prefer_telegram_cache and not _skip_cache:
            cached = await self._try_cache_lookup(url=url, mode=mode, quality=quality)
            if cached is not None:
                return RemoteDownloadResult(
                    status=DownloadStatus.CACHED,
                    files=[],
                    cached=cached,
                )

        try:
            data = await fetch_links_api_response(
                api_base_url=self.api_base_url,
                url=url,
                mode=mode,
                quality=quality,
                use_cookies=use_cookies,
                timeout_s=links_timeout_s,
                api_token=self.api_token,
            )
        except Exception as exc:
            _log_api_failure("Links API request failed", exc)
            error_result = make_api_error_result(exc, self.api_base_url, links_timeout_s)
            return RemoteDownloadResult(
                status=DownloadStatus.ERROR,
                files=[],
                error=error_result.error,
            )

        return self._pipeline.parse_links_response(data)

    async def download_links(
        self,
        url: str,
        mode: str = "auto",
        quality: str = "max",
        use_cookies: bool = False,
        max_file_size_mb: int | None = None,
        links_timeout_s: float = 60.0,
        _skip_cache: bool = False,
    ) -> DownloadResult:
        """
        Fast-link скачивание: сервер даёт CDN-ссылки, SDK качает сам.

        В отличие от server-side ``download()``, local /links pipeline ограничен
        глобальным concurrency limiter'ом. Если свободный слот не появился
        за ``concurrency_max_ttl_s``, метод вернёт ``status=ERROR`` с
        кодом ``client_busy``.

        Args:
            url: Ссылка на медиа.
            mode: ``"auto"`` | ``"audio"`` | ``"video"``.
            quality: ``"max"`` | ``"2160"`` | ``"1440"`` | ``"1080"`` |
                    ``"720"`` | ``"480"`` | ``"360"``.
            use_cookies: Серверные cookies.
            max_file_size_mb:
                Лимит в MB. Применяется SDK локально через ffmpeg.
                На сервер не передаётся.
            links_timeout_s: Таймаут запроса к ``/downloader/links``.

        Returns:
            :class:`DownloadResult`.
        """
        remote = await self.get_remote_links(
            url=url,
            mode=mode,
            quality=quality,
            use_cookies=use_cookies,
            links_timeout_s=links_timeout_s,
            _skip_cache=_skip_cache,
        )

        if not remote.ok:
            return DownloadResult(
                status=remote.status,
                files=[],
                error=remote.error,
                cached=remote.cached,
            )

        if remote.is_cached:
            return DownloadResult(
                status=DownloadStatus.CACHED,
                files=[],
                cached=remote.cached,
            )

        max_file_size_bytes = (
            max_file_size_mb * 1024 * 1024 if max_file_size_mb else None
        )

        return await self._process_remote_result_with_limit(
            url=url,
            remote=remote,
            max_file_size_bytes=max_file_size_bytes,
        )

    async def _process_remote_result_with_limit(
        self,
        *,
        url: str,
        remote: RemoteDownloadResult,
        max_file_size_bytes: int | None,
    ) -> DownloadResult:
        """
        Запускает локальную fast-link обработку под глобальным concurrency limiter.

        Если свободный слот не появился за ``concurrency_max_ttl_s``,
        возвращает ``client_busy``. Любая неожиданная локальная ошибка
        превращается в ``local_processing_failed``.
        """
        try:
            async with self._local_processing_limiter.slot(label="download_links"):
                return await self._pipeline.process_remote_result(
                    remote,
                    max_file_size_bytes,
                )

        except ConcurrencyLimitTimeoutError as exc:
            logger.warning(
                "Local fast-link slot timeout | url=%s max_concurrency=%d timeout=%.1fs",
                url,
                self.max_concurrency,
                self.concurrency_max_ttl_s,
            )
            return DownloadResult(
                status=DownloadStatus.ERROR,
                files=[],
                error=ClientError(
                    code=ClientErrorCode.CLIENT_BUSY.value,
                    detail=str(exc),
                ),
            )

        except Exception as exc:
            logger.exception(
                "Local fast-link processing failed unexpectedly | url=%s",
                url,
            )
            return DownloadResult(
                status=DownloadStatus.ERROR,
                files=[],
                error=ClientError(
                    code=ClientErrorCode.LOCAL_PROCESSING_FAILED.value,
                    detail=f"Local fast-link processing failed: {type(exc).__name__}: {exc}",
                ),
            )

    # ─── Internal helpers ────────────────────────────────────

    async def _try_cache_lookup(
        self,
        *,
        url: str,
        mode: str,
        quality: str,
    ) -> CachedResult | None:
        """
        Попытка получить результат из Telegram cache.

        При любых ошибках (кроме 401/429) молча возвращает None.
        """
        try:
            return await fetch_cache_lookup(
                api_base_url=self.api_base_url,
                url=url,
                mode=mode,
                quality=quality,
                timeout_s=self.cache_lookup_timeout_s,
                api_token=self.api_token,
            )
        except Exception as e:
            logger.warning(
                "Cache lookup failed, continuing without cache | url=%s error=%s",
                url, e,
            )
            return None

    async def _poll_until_ready(
        self,
        task_id: str,
        start_time: float,
    ) -> dict | None:
        """
        Polling loop: опрашивает result endpoint до готовности или таймаута.

        Returns:
            Parsed DownloaderResponse dict при успехе, None при таймауте.
        """
        while True:
            elapsed = time.monotonic() - start_time
            if elapsed >= self.task_max_poll_time_s:
                logger.warning(
                    "Task poll timeout | task_id=%s elapsed=%.1fs",
                    task_id, elapsed,
                )
                return None

            try:
                data = await fetch_task_result(
                    api_base_url=self.api_base_url,
                    task_id=task_id,
                    timeout_s=self._pipeline.download_timeout_s,
                    api_token=self.api_token,
                )
            except httpx.HTTPStatusError as exc:
                if exc.response.status_code == 404:
                    logger.error(
                        "Task disappeared during polling | task_id=%s", task_id,
                    )
                    return None
                logger.warning(
                    "Task poll HTTP error | task_id=%s status=%s",
                    task_id, exc.response.status_code,
                )
                await asyncio.sleep(self.task_poll_interval_s)
                continue
            except Exception as exc:
                logger.warning(
                    "Task poll failed | task_id=%s error=%s", task_id, exc,
                )
                await asyncio.sleep(self.task_poll_interval_s)
                continue

            if data is not None:
                logger.info(
                    "Task result ready | task_id=%s elapsed=%.1fs", task_id, elapsed,
                )
                return data

            await asyncio.sleep(self.task_poll_interval_s)


# ─── Module-level helpers ─────────────────────────────────────

def _task_error_result(exc: Exception, code: ClientErrorCode) -> DownloadResult:
    """Формирует DownloadResult из ошибки task API."""
    detail = f"{type(exc).__name__}: {exc}"
    status_code = None

    if isinstance(exc, httpx.HTTPStatusError):
        status_code = exc.response.status_code
        if status_code == 401:
            code = ClientErrorCode.API_UNAUTHORIZED
        elif status_code == 429:
            code = ClientErrorCode.API_RATE_LIMITED

    return DownloadResult(
        status=DownloadStatus.ERROR,
        files=[],
        error=ClientError(
            code=code.value,
            detail=detail,
            status=status_code,
        ),
    )