# anysave_sdk/download_pipeline.py

"""
Pipeline скачивания файлов: transport, merge, finalize.

Отвечает за физическое скачивание файлов после того,
как orchestrator (AnySaveClient) получил CDN-ссылки или
API-ответ с URL. Не содержит бизнес-логики выбора стратегии.
"""

from __future__ import annotations

import asyncio
import logging
import time
from pathlib import Path
from typing import TYPE_CHECKING, Awaitable, Callable

import aiofiles
import aiohttp
import httpx

from anysave_sdk.api import extract_files, extract_remote_files, make_api_error_result
from anysave_sdk.error_codes import ClientErrorCode
from anysave_sdk.headers import build_download_headers
from anysave_sdk.models import (
    ApiFile,
    ClientError,
    DownloadedFile,
    DownloadedFileExtra,
    DownloadResult,
    DownloadStatus,
    RemoteDownloadResult,
    RemoteFile,
)
from anysave_sdk.postprocessing import (
    download_hls_stream,
    enrich_media_metadata,
    fix_container,
    merge_video_audio,
    truncate_file,
)
from anysave_sdk.transport import (
    download_with_aiohttp,
    download_with_httpx,
    resume_incomplete_download,
)
from anysave_sdk.utils import fmt_size, map_type, normalize_extension, safe_unlink, unique_path

if TYPE_CHECKING:
    pass

logger = logging.getLogger("api_client")


class DownloadPipeline:
    """
    Физическое скачивание файлов: transport, HLS, merge, finalize.

    Создаётся и удерживается AnySaveClient.
    Не принимает решений о стратегии — только исполняет.
    """

    def __init__(
        self,
        *,
        download_dir: Path,
        chunk_size: int,
        max_concurrency: int,
        download_timeout_s: float,
        min_speed_bytes_per_sec: int,
        download_retry_attempts: int,
        thumbnail_placeholder: str | None,
    ) -> None:
        self.download_dir = download_dir
        self.chunk_size = chunk_size
        self.max_concurrency = max_concurrency
        self.download_timeout_s = download_timeout_s
        self.min_speed_bytes_per_sec = min_speed_bytes_per_sec
        self.download_retry_attempts = download_retry_attempts
        self.thumbnail_placeholder = thumbnail_placeholder

    # ─── API response → DownloadResult ───────────────────────

    async def process_api_response(
        self,
        data: dict,
        max_file_size_mb: int | None,
        total_start: float,
    ) -> DownloadResult:
        """
        Обрабатывает API response: извлекает файлы и скачивает их.

        Переиспользуется sync flow (download) и task flow (download_via_tasks).
        """
        max_file_size_bytes = (
            max_file_size_mb * 1024 * 1024 if max_file_size_mb else None
        )

        extracted = extract_files(data)
        if extracted is None:
            return self._error_from_data(data)

        files, audio, is_truncated = extracted
        all_files = list(files)
        if audio is not None:
            all_files.append(audio)

        dl_start = time.monotonic()
        sem = asyncio.Semaphore(self.max_concurrency)
        connector = aiohttp.TCPConnector(limit=self.max_concurrency)

        async with aiohttp.ClientSession(connector=connector) as session:

            async def _guarded(f: ApiFile) -> DownloadedFile | None:
                async with sem:
                    return await self._download_one(
                        session, f, is_truncated, max_file_size_bytes,
                    )

            downloaded = await asyncio.gather(*[_guarded(f) for f in all_files])

        dl_elapsed = time.monotonic() - dl_start
        total_elapsed = time.monotonic() - total_start
        ok_files = [f for f in downloaded if f is not None]

        logger.info(
            "Download complete: %d/%d files | download=%.2fs total=%.2fs",
            len(ok_files), len(all_files), dl_elapsed, total_elapsed,
        )

        if len(ok_files) != len(all_files):
            return DownloadResult(
                status=DownloadStatus.PARTIAL if ok_files else DownloadStatus.ERROR,
                files=ok_files,
                error=ClientError(
                    code=ClientErrorCode.SOME_DOWNLOADS_FAILED.value,
                    detail=f"Expected {len(all_files)}, got {len(ok_files)}",
                ),
            )

        return DownloadResult(status=DownloadStatus.SUCCESS, files=ok_files)

    def parse_links_response(self, data: dict) -> RemoteDownloadResult:
        """
        Парсит API-ответ /downloader/links в RemoteDownloadResult.

        tunnel  → files=[RemoteFile], audio=None
        picker  → files=[...], audio=RemoteFile (если есть)
        error   → status=ERROR
        """
        extracted = extract_remote_files(data)

        if extracted is None:
            return self._remote_error_from_data(data)

        api_files, api_audio, _is_truncated = extracted

        remote_files = [
            RemoteFile(
                type=f.type,
                url=f.url,
                filename=f.filename,
                file_size_bytes=f.file_size_bytes,
                extra=f.extra,
            )
            for f in api_files
        ]

        remote_audio: RemoteFile | None = None
        if api_audio is not None:
            remote_audio = RemoteFile(
                type=api_audio.type,
                url=api_audio.url,
                filename=api_audio.filename,
                file_size_bytes=api_audio.file_size_bytes,
                extra=api_audio.extra,
            )

        return RemoteDownloadResult(
            status=DownloadStatus.SUCCESS,
            files=remote_files,
            audio=remote_audio,
        )

    async def process_remote_result(
        self,
        remote: RemoteDownloadResult,
        max_file_size_bytes: int | None,
    ) -> DownloadResult:
        """
        Скачивает файлы из RemoteDownloadResult.

        Сценарии:
        - split video + audio (один video/animation + отдельный audio) → merge
        - photo carousel + audio → скачать фото и аудио раздельно
        - HLS manifest → ffmpeg download
        - Direct URL → aiohttp / httpx
        """
        self.download_dir.mkdir(parents=True, exist_ok=True)

        # Сценарий: split video + audio → merge только для настоящего видео
        if (
            remote.audio is not None
            and len(remote.files) == 1
            and remote.files[0].is_video
        ):
            return await self._download_and_merge(
                remote.files[0],
                remote.audio,
                max_file_size_bytes,
            )

        files_to_download = list(remote.files)
        if remote.audio is not None:
            files_to_download.append(remote.audio)

        sem = asyncio.Semaphore(self.max_concurrency)
        connector = aiohttp.TCPConnector(limit=self.max_concurrency)

        async with aiohttp.ClientSession(connector=connector) as session:

            async def _guarded(rf: RemoteFile) -> DownloadedFile | None:
                async with sem:
                    return await self._download_remote_file(
                        session,
                        rf,
                        max_file_size_bytes,
                    )

            downloaded = await asyncio.gather(*[_guarded(f) for f in files_to_download])

        ok_files = [f for f in downloaded if f is not None]

        if not ok_files:
            return DownloadResult(
                status=DownloadStatus.ERROR,
                files=[],
                error=ClientError(
                    code=ClientErrorCode.SOME_DOWNLOADS_FAILED.value,
                    detail=f"All {len(files_to_download)} downloads failed",
                ),
            )

        if len(ok_files) != len(files_to_download):
            return DownloadResult(
                status=DownloadStatus.PARTIAL,
                files=ok_files,
                error=ClientError(
                    code=ClientErrorCode.SOME_DOWNLOADS_FAILED.value,
                    detail=f"Expected {len(files_to_download)}, got {len(ok_files)}",
                ),
            )

        return DownloadResult(status=DownloadStatus.SUCCESS, files=ok_files)

    # ─── Remote file download ─────────────────────────────────

    async def _download_remote_file(
        self,
        session: aiohttp.ClientSession,
        remote_file: RemoteFile,
        max_file_size_bytes: int | None,
    ) -> DownloadedFile | None:
        """Скачивает один remote file: direct URL или HLS manifest."""
        dest = unique_path(
            (self.download_dir / normalize_extension(remote_file.filename, remote_file.type)).resolve()
        )
        try:
            if remote_file.is_hls_manifest:
                return await self._download_hls_file(
                    remote_file, dest, max_file_size_bytes,
                )
            return await self._download_direct_file(
                session, remote_file, dest, max_file_size_bytes,
            )
        except Exception as e:
            logger.warning(
                "Remote file download failed | filename=%s error=%s: %s",
                remote_file.filename, type(e).__name__, e,
            )
            safe_unlink(dest)
            return None

    async def _download_direct_file(
        self,
        session: aiohttp.ClientSession,
        remote_file: RemoteFile,
        dest: Path,
        max_file_size_bytes: int | None,
    ) -> DownloadedFile | None:
        """Скачивает прямой URL через _download_one pipeline."""
        api_file = _remote_to_api_file(remote_file)
        return await self._download_one(
            session, api_file, is_truncated=False,
            max_file_size_bytes=max_file_size_bytes,
        )

    async def _download_hls_file(
        self,
        remote_file: RemoteFile,
        dest: Path,
        max_file_size_bytes: int | None,
    ) -> DownloadedFile | None:
        """Скачивает HLS/DASH manifest через ffmpeg."""
        api_file = _remote_to_api_file(remote_file)
        headers = build_download_headers(api_file)

        logger.info(
            "Downloading HLS: %s → %s",
            remote_file.url[:120], dest.name,
        )

        ok = await download_hls_stream(
            manifest_url=remote_file.url,
            output_path=dest,
            timeout_seconds=self.download_timeout_s,
            headers=headers,
        )

        if not ok:
            safe_unlink(dest)
            return None

        file_size = dest.stat().st_size
        was_truncated = False

        if max_file_size_bytes and file_size > max_file_size_bytes:
            dest, was_truncated = await truncate_file(dest, max_file_size_bytes)
            file_size = dest.stat().st_size if dest.exists() else 0

        final_type = map_type(remote_file.type)
        media_metadata = await enrich_media_metadata(
            filepath=dest,
            file_type=final_type,
            current=remote_file.extra.metadata if remote_file.extra else None,
            thumbnail_placeholder=self.thumbnail_placeholder,
        )

        logger.info(
            "HLS complete: %s (%s)%s",
            dest.name, fmt_size(file_size),
            " [TRUNCATED]" if was_truncated else "",
        )

        return DownloadedFile(
            type=final_type,
            path=str(dest),
            file_size_bytes=file_size,
            is_truncated=was_truncated,
            extra=DownloadedFileExtra(metadata=media_metadata) if media_metadata else None,
        )

    # ─── Split video + audio merge ────────────────────────────

    async def _download_and_merge(
        self,
        video_remote: RemoteFile,
        audio_remote: RemoteFile,
        max_file_size_bytes: int | None,
    ) -> DownloadResult:
        """Скачивает video и audio раздельно, мёржит через ffmpeg."""
        video_dest = unique_path(
            (self.download_dir / normalize_extension(f"video_{video_remote.filename}", video_remote.type)).resolve()
        )
        audio_dest = unique_path(
            (self.download_dir / normalize_extension(f"audio_{audio_remote.filename}", audio_remote.type)).resolve()
        )

        logger.info(
            "Downloading split video+audio | video=%s audio=%s",
            video_remote.url[:80], audio_remote.url[:80],
        )

        try:
            video_ok, audio_ok = await asyncio.gather(
                self._download_single_stream(video_remote, video_dest),
                self._download_single_stream(audio_remote, audio_dest),
            )

            if not video_ok:
                logger.warning("Video download failed, cannot merge")
                safe_unlink(video_dest)
                safe_unlink(audio_dest)
                return DownloadResult(
                    status=DownloadStatus.ERROR,
                    files=[],
                    error=ClientError(
                        code=ClientErrorCode.SOME_DOWNLOADS_FAILED.value,
                        detail="Video stream download failed",
                    ),
                )

            if not audio_ok:
                logger.warning("Audio download failed, returning video-only")
                safe_unlink(audio_dest)
                return await self._finalize_single_file(
                    video_dest, video_remote, max_file_size_bytes, is_partial=True,
                )

            merged_dest = unique_path(
                (self.download_dir / normalize_extension(video_remote.filename, video_remote.type)).resolve()
            )
            merge_ok = await merge_video_audio(
                video_path=video_dest,
                audio_path=audio_dest,
                output_path=merged_dest,
            )
            safe_unlink(video_dest)
            safe_unlink(audio_dest)

            if not merge_ok:
                logger.warning("Merge failed")
                safe_unlink(merged_dest)
                return DownloadResult(
                    status=DownloadStatus.ERROR,
                    files=[],
                    error=ClientError(
                        code=ClientErrorCode.SOME_DOWNLOADS_FAILED.value,
                        detail="ffmpeg merge failed",
                    ),
                )

            return await self._finalize_single_file(
                merged_dest, video_remote, max_file_size_bytes, is_partial=False,
            )

        except Exception as e:
            logger.error("Merge pipeline error: %s: %s", type(e).__name__, e)
            safe_unlink(video_dest)
            safe_unlink(audio_dest)
            return DownloadResult(
                status=DownloadStatus.ERROR,
                files=[],
                error=ClientError(
                    code=ClientErrorCode.SOME_DOWNLOADS_FAILED.value,
                    detail=f"Merge pipeline error: {e}",
                ),
            )

    async def _download_single_stream(
        self,
        remote_file: RemoteFile,
        dest: Path,
    ) -> bool:
        """
        Скачивает один stream (video или audio): direct или HLS.

        Returns:
            True при успехе, False при ошибке.
        """
        api_file = _remote_to_api_file(remote_file)
        headers = build_download_headers(api_file)

        if remote_file.is_hls_manifest:
            try:
                ok = await download_hls_stream(
                    manifest_url=remote_file.url,
                    output_path=dest,
                    timeout_seconds=self.download_timeout_s,
                    headers=headers,
                )
                if not ok:
                    safe_unlink(dest)
                return ok
            except Exception as e:
                logger.warning(
                    "HLS stream download failed | url=%s error=%s",
                    remote_file.url[:80], e,
                )
                safe_unlink(dest)
                return False

        # Direct URL: aiohttp → httpx fallback
        if await self._stream_via_aiohttp(remote_file.url, dest, headers):
            return True
        return await self._stream_via_httpx(remote_file.url, dest, headers)

    async def _stream_via_aiohttp(
        self,
        url: str,
        dest: Path,
        headers: dict[str, str],
    ) -> bool:
        """Скачивает поток через aiohttp. Returns True при успехе."""
        try:
            timeout = aiohttp.ClientTimeout(total=self.download_timeout_s)
            async with aiohttp.ClientSession() as session:
                async with session.get(
                    url, allow_redirects=True, timeout=timeout, headers=headers,
                ) as resp:
                    if resp.status < 200 or resp.status >= 300:
                        return False

                    bytes_written = 0
                    async with aiofiles.open(dest, "wb") as f:
                        async for chunk in resp.content.iter_chunked(self.chunk_size):
                            await f.write(chunk)
                            bytes_written += len(chunk)

            if not dest.exists() or dest.stat().st_size < 100:
                safe_unlink(dest)
                return False

            logger.debug(
                "aiohttp stream ok: %s (%s)",
                dest.name, fmt_size(dest.stat().st_size),
            )
            return True

        except Exception as e:
            logger.debug(
                "aiohttp stream failed | url=%s error=%s: %s",
                url[:80], type(e).__name__, e,
            )
            safe_unlink(dest)
            return False

    async def _stream_via_httpx(
        self,
        url: str,
        dest: Path,
        headers: dict[str, str],
    ) -> bool:
        """Скачивает поток через httpx (fallback). Returns True при успехе."""
        try:
            timeout = httpx.Timeout(self.download_timeout_s)
            async with httpx.AsyncClient(
                timeout=timeout, follow_redirects=True,
            ) as client:
                async with client.stream("GET", url, headers=headers) as resp:
                    if resp.status_code < 200 or resp.status_code >= 300:
                        return False

                    async with aiofiles.open(dest, "wb") as f:
                        async for chunk in resp.aiter_bytes(self.chunk_size):
                            await f.write(chunk)

            if not dest.exists() or dest.stat().st_size < 100:
                safe_unlink(dest)
                return False

            logger.info(
                "httpx stream ok (fallback): %s (%s)",
                dest.name, fmt_size(dest.stat().st_size),
            )
            return True

        except Exception as e:
            logger.warning(
                "httpx stream failed | url=%s error=%s: %s",
                url[:80], type(e).__name__, e,
            )
            safe_unlink(dest)
            return False

    async def _finalize_single_file(
        self,
        dest: Path,
        remote_file: RemoteFile,
        max_file_size_bytes: int | None,
        is_partial: bool,
    ) -> DownloadResult:
        """Финализирует один файл: truncate, metadata, DownloadResult."""
        file_size = dest.stat().st_size
        was_truncated = False

        if max_file_size_bytes and file_size > max_file_size_bytes:
            dest, was_truncated = await truncate_file(dest, max_file_size_bytes)
            file_size = dest.stat().st_size if dest.exists() else 0

        final_type = map_type(remote_file.type)
        media_metadata = await enrich_media_metadata(
            filepath=dest,
            file_type=final_type,
            current=remote_file.extra.metadata if remote_file.extra else None,
            thumbnail_placeholder=self.thumbnail_placeholder,
        )

        downloaded_file = DownloadedFile(
            type=final_type,
            path=str(dest),
            file_size_bytes=file_size,
            is_truncated=was_truncated,
            extra=DownloadedFileExtra(metadata=media_metadata) if media_metadata else None,
        )

        return DownloadResult(
            status=DownloadStatus.PARTIAL if is_partial else DownloadStatus.SUCCESS,
            files=[downloaded_file],
            error=ClientError(
                code=ClientErrorCode.SOME_DOWNLOADS_FAILED.value,
                detail="Audio download failed, video-only returned",
            ) if is_partial else None,
        )

    # ─── Legacy API response download (sync / tasks flow) ────

    async def _download_one(
        self,
        session: aiohttp.ClientSession,
        file: ApiFile,
        is_truncated: bool = False,
        max_file_size_bytes: int | None = None,
    ) -> DownloadedFile | None:
        """Скачивает один файл из API response (aiohttp → httpx fallback)."""
        self.download_dir.mkdir(parents=True, exist_ok=True)

        dest = unique_path(
            (self.download_dir / normalize_extension(file.filename, file.type)).resolve()
        )
        headers = build_download_headers(file)

        # Attempt 1: aiohttp
        result = await self._attempt_download(
            transport_name="aiohttp",
            download_fn=lambda: download_with_aiohttp(
                session=session,
                url=file.url,
                dest=dest,
                max_size_bytes=max_file_size_bytes,
                headers=headers,
                timeout_s=self.download_timeout_s,
                chunk_size=self.chunk_size,
                min_speed_bytes_per_sec=self.min_speed_bytes_per_sec,
            ),
            file=file,
            dest=dest,
            headers=headers,
            is_truncated=is_truncated,
            max_file_size_bytes=max_file_size_bytes,
        )
        if result is not None:
            return result

        # Attempt 2: httpx fallback
        timeout = httpx.Timeout(self.download_timeout_s)
        async with httpx.AsyncClient(timeout=timeout, follow_redirects=True) as client:
            return await self._attempt_download(
                transport_name="httpx",
                download_fn=lambda: download_with_httpx(
                    client=client,
                    url=file.url,
                    dest=dest,
                    max_size_bytes=max_file_size_bytes,
                    headers=headers,
                    chunk_size=self.chunk_size,
                ),
                file=file,
                dest=dest,
                headers=headers,
                is_truncated=is_truncated,
                max_file_size_bytes=max_file_size_bytes,
            )

    async def _attempt_download(
        self,
        transport_name: str,
        download_fn: Callable[[], Awaitable[tuple[int, bool]]],
        file: ApiFile,
        dest: Path,
        headers: dict[str, str],
        is_truncated: bool,
        max_file_size_bytes: int | None,
    ) -> DownloadedFile | None:
        """Одна попытка скачивания. Returns DownloadedFile или None."""
        from anysave_sdk.exceptions import IncompleteDownloadError, SlowDownloadError

        dl_start = time.monotonic()
        try:
            bytes_downloaded, was_cut = await download_fn()
            dl_elapsed = time.monotonic() - dl_start
            speed = bytes_downloaded / dl_elapsed if dl_elapsed > 0 else 0

            logger.info(
                "Downloaded (%s) %s: %s in %.2fs (%s/s)%s",
                transport_name, file.filename,
                fmt_size(bytes_downloaded), dl_elapsed,
                fmt_size(int(speed)),
                " [CUT]" if was_cut else "",
            )

            return await self._finalize(
                dest, file, bytes_downloaded, was_cut,
                is_truncated, max_file_size_bytes,
            )

        except IncompleteDownloadError as e:
            logger.warning(
                "%s incomplete | file=%s got=%s expected=%s error=%s: %s",
                transport_name, file.filename,
                fmt_size(e.bytes_downloaded), fmt_size(e.expected_bytes),
                type(e.original).__name__, e.original,
            )
            resumed = await resume_incomplete_download(
                file=file, dest=dest, headers=headers,
                bytes_downloaded=e.bytes_downloaded,
                expected_bytes=e.expected_bytes,
                is_truncated=is_truncated,
                max_file_size_bytes=max_file_size_bytes,
                retry_attempts=self.download_retry_attempts,
                timeout_s=self.download_timeout_s,
                chunk_size=self.chunk_size,
                finalize_fn=self._finalize,
            )
            if resumed is not None:
                return resumed
            safe_unlink(dest)
            return None

        except SlowDownloadError as e:
            logger.warning(
                "Download too slow (%s) | file=%s: %s in %.1fs",
                transport_name, file.filename,
                fmt_size(e.bytes_downloaded), e.elapsed,
            )
            safe_unlink(dest)
            return None

        except Exception as e:
            logger.warning(
                "%s download failed | file=%s error=%s: %s",
                transport_name, file.filename, type(e).__name__, e,
            )
            safe_unlink(dest)
            return None

    async def _finalize(
        self,
        dest: Path,
        file: ApiFile,
        bytes_downloaded: int,
        was_cut: bool,
        is_truncated: bool,
        max_file_size_bytes: int | None,
    ) -> DownloadedFile:
        """Финализация после успешного скачивания: fix container, truncate, metadata."""
        was_truncated_final = is_truncated or was_cut

        if was_cut:
            dest, fixed = await fix_container(dest)
            if fixed:
                bytes_downloaded = dest.stat().st_size
                logger.info("Container fixed: %s", fmt_size(bytes_downloaded))
            else:
                logger.info("Container fix failed, trying ffmpeg truncation")
                dest, was_truncated_final = await truncate_file(dest, max_file_size_bytes)
                bytes_downloaded = dest.stat().st_size if dest.exists() else 0

        elif max_file_size_bytes and bytes_downloaded > max_file_size_bytes:
            dest, was_truncated_final = await truncate_file(dest, max_file_size_bytes)
            bytes_downloaded = dest.stat().st_size if dest.exists() else bytes_downloaded

        final_type = map_type(file.type)
        media_metadata = await enrich_media_metadata(
            filepath=dest,
            file_type=final_type,
            current=file.extra.metadata if file.extra else None,
            thumbnail_placeholder=self.thumbnail_placeholder,
        )

        return DownloadedFile(
            type=final_type,
            path=str(dest),
            file_size_bytes=bytes_downloaded,
            is_truncated=was_truncated_final,
            extra=DownloadedFileExtra(metadata=media_metadata) if media_metadata else None,
        )

    # ─── Error helpers ────────────────────────────────────────

    @staticmethod
    def _error_from_data(data: dict) -> DownloadResult:
        """Формирует DownloadResult(ERROR) из сырого API-ответа."""
        error_data = data.get("error") if isinstance(data, dict) else None
        if error_data and isinstance(error_data, dict):
            return DownloadResult(
                status=DownloadStatus.ERROR,
                files=[],
                error=ClientError(
                    code=error_data.get("code", ClientErrorCode.UNKNOWN_RESPONSE.value),
                    detail=error_data.get("detail"),
                ),
            )
        return DownloadResult(
            status=DownloadStatus.ERROR,
            files=[],
            error=ClientError(code=ClientErrorCode.UNKNOWN_RESPONSE.value),
        )

    @staticmethod
    def _remote_error_from_data(data: dict) -> RemoteDownloadResult:
        """Формирует RemoteDownloadResult(ERROR) из сырого API-ответа."""
        error_data = data.get("error") if isinstance(data, dict) else None
        if error_data and isinstance(error_data, dict):
            return RemoteDownloadResult(
                status=DownloadStatus.ERROR,
                files=[],
                error=ClientError(
                    code=error_data.get("code", ClientErrorCode.UNKNOWN_RESPONSE.value),
                    detail=error_data.get("detail"),
                ),
            )
        return RemoteDownloadResult(
            status=DownloadStatus.ERROR,
            files=[],
            error=ClientError(code=ClientErrorCode.UNKNOWN_RESPONSE.value),
        )


# ─── Module-level helper (не метод класса) ───────────────────

def _remote_to_api_file(remote_file: RemoteFile) -> ApiFile:
    """
    Конвертирует RemoteFile → ApiFile.

    Позволяет переиспользовать существующий pipeline (_download_one,
    build_download_headers, transport layer) для remote files.
    """
    from anysave_sdk.models import ApiFile
    return ApiFile(
        type=remote_file.type,
        url=remote_file.url,
        filename=remote_file.filename,
        file_size_bytes=remote_file.file_size_bytes,
        extra=remote_file.extra,
    )