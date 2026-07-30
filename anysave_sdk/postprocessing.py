# anysave_sdk/postprocessing.py

"""
FFmpeg post-processing: fix container, truncate, probe duration.
"""

import json
import aiohttp
import asyncio
import logging
import subprocess

from contextlib import suppress
from pathlib import Path
from typing import Optional
from anysave_sdk.models import FileMediaMetadata
from anysave_sdk.utils import fmt_size, safe_unlink

logger = logging.getLogger("api_client")


async def fix_container(filepath: Path) -> tuple[Path, bool]:
    """
    Фиксит контейнер обрезанного файла через ffmpeg -c copy.

    Когда мы прерываем HTTP-стрим на середине, MP4 может быть невалидным
    (нет moov atom). ffmpeg -c copy пересобирает контейнер без перекодирования.

    Returns:
        (output_path, success)
    """
    fixed_path = filepath.with_name(f"{filepath.stem}_fixed{filepath.suffix}")

    cmd = [
        "ffmpeg",
        "-i", str(filepath),
        "-c", "copy",
        "-movflags", "+faststart",
        "-y",
        str(fixed_path),
    ]

    try:
        process = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
        )

        try:
            _, stderr = await asyncio.wait_for(
                process.communicate(), timeout=30.0
            )
        except asyncio.TimeoutError:
            with suppress(ProcessLookupError):
                process.kill()
                await process.wait()
            return filepath, False

        if process.returncode != 0:
            safe_unlink(fixed_path)
            return filepath, False

        if not fixed_path.exists() or fixed_path.stat().st_size < 100:
            safe_unlink(fixed_path)
            return filepath, False

        safe_unlink(filepath)
        fixed_path.rename(filepath)
        return filepath, True

    except Exception:
        safe_unlink(fixed_path)
        return filepath, False


async def truncate_file(
    filepath: Path, max_size_bytes: int
) -> tuple[Path, bool]:
    """
    Обрезает медиафайл через ffmpeg если превышает лимит.

    Returns:
        (path, was_truncated)
    """
    file_size = filepath.stat().st_size

    if file_size <= max_size_bytes:
        return filepath, False

    logger.info(
        f"Truncating {filepath.name}: "
        f"{fmt_size(file_size)} → target {fmt_size(max_size_bytes)}"
    )

    duration = await ffprobe_duration(filepath)
    if duration is None or duration <= 0:
        logger.warning(
            f"Cannot determine duration of {filepath.name}, skipping truncation"
        )
        return filepath, False

    ratio = max_size_bytes / file_size
    target_duration = duration * ratio * 0.90

    if target_duration < 1.0:
        logger.warning(
            f"Target duration too short ({target_duration:.1f}s), skipping truncation"
        )
        return filepath, False

    truncated_path = filepath.with_name(f"{filepath.stem}_cut{filepath.suffix}")

    success = await ffmpeg_cut(filepath, truncated_path, target_duration)

    if not success:
        safe_unlink(truncated_path)
        return filepath, False

    truncated_size = truncated_path.stat().st_size

    if truncated_size > max_size_bytes:
        logger.info(
            f"First cut still too large: {fmt_size(truncated_size)}, second pass..."
        )
        second_ratio = max_size_bytes / truncated_size * 0.85
        second_duration = target_duration * second_ratio

        if second_duration < 1.0:
            safe_unlink(truncated_path)
            return filepath, False

        second_path = filepath.with_name(f"{filepath.stem}_cut2{filepath.suffix}")
        success = await ffmpeg_cut(truncated_path, second_path, second_duration)

        safe_unlink(truncated_path)

        if success and second_path.exists():
            final_size = second_path.stat().st_size
            logger.info(f"Truncated (2nd pass): {fmt_size(final_size)}")
            safe_unlink(filepath)
            return second_path, True

        safe_unlink(second_path)
        return filepath, False

    logger.info(f"Truncated: {fmt_size(truncated_size)}")
    safe_unlink(filepath)
    truncated_path.rename(filepath)
    return filepath, True


async def enrich_media_metadata(
    filepath: Path,
    file_type: str,
    current: FileMediaMetadata | None = None,
    thumbnail_placeholder: str | None = None,
) -> FileMediaMetadata | None:
    merged = _copy_media_metadata(current)
    normalized_type = (file_type or "").lower()

    if normalized_type in {"video", "animation", "audio"} and _needs_probe(
        normalized_type, merged
    ):
        probed = await ffprobe_media_metadata(filepath)
        if probed is not None:
            if normalized_type == "audio":
                probed = FileMediaMetadata(duration=probed.duration)
            else:
                probed = FileMediaMetadata(
                    duration=probed.duration,
                    width=probed.width,
                    height=probed.height,
                )
            merged = merge_media_metadata(merged, probed)

    if normalized_type in {"video", "animation", "audio"} and (
        merged is None or not merged.thumbnail
    ):
        thumb_path = await _ensure_local_thumbnail(
            filepath=filepath,
            file_type=normalized_type,
            duration_seconds=merged.duration if merged else None,
        )
        if thumb_path is not None:
            merged = merge_media_metadata(
                merged,
                FileMediaMetadata(thumbnail=str(thumb_path)),
            )

    # Материализация remote thumbnail в локальный файл
    if merged and merged.thumbnail and merged.thumbnail.startswith("http"):
        local_thumb_path = filepath.with_name(f"{filepath.stem}_thumb.jpg")
        ok = await _materialize_remote_thumbnail(merged.thumbnail, local_thumb_path)
        if ok:
            merged = merge_media_metadata(
                FileMediaMetadata(thumbnail=str(local_thumb_path)),
                merged,
            )
        else:
            # Remote thumbnail не скачался — убираем URL, ставим placeholder
            fallback_thumb = thumbnail_placeholder
            merged = merge_media_metadata(
                FileMediaMetadata(
                    duration=merged.duration,
                    width=merged.width,
                    height=merged.height,
                    thumbnail=fallback_thumb,
                ),
                None,
            )

    if normalized_type in {"video", "animation", "audio"} and (
        merged is None or not merged.thumbnail
    ) and thumbnail_placeholder:
        merged = merge_media_metadata(
            merged,
            FileMediaMetadata(thumbnail=thumbnail_placeholder),
        )

    return merged if _has_media_metadata(merged) else None


async def ffprobe_media_metadata(filepath: Path) -> FileMediaMetadata | None:
    cmd = [
        "ffprobe",
        "-v", "error",
        "-show_entries", "format=duration:stream=codec_type,width,height",
        "-of", "json",
        str(filepath),
    ]

    try:
        process = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )

        stdout, stderr = await asyncio.wait_for(
            process.communicate(), timeout=15.0
        )

        if process.returncode != 0:
            return None

        payload = json.loads(stdout.decode("utf-8", errors="replace"))
        format_data = payload.get("format") or {}
        streams = payload.get("streams") or []

        width: int | None = None
        height: int | None = None

        for stream in streams:
            if not isinstance(stream, dict):
                continue
            if stream.get("codec_type") != "video":
                continue

            if width is None:
                width = _coerce_int(stream.get("width"))
            if height is None:
                height = _coerce_int(stream.get("height"))

            if width is not None or height is not None:
                break

        return FileMediaMetadata(
            duration=_coerce_float(format_data.get("duration")),
            width=width,
            height=height,
        )
    except (asyncio.TimeoutError, json.JSONDecodeError, Exception):
        return None


async def ffprobe_duration(filepath: Path) -> Optional[float]:
    """Определяет длительность медиафайла через ffprobe."""
    metadata = await ffprobe_media_metadata(filepath)
    return metadata.duration if metadata is not None else None


def merge_media_metadata(
    primary: FileMediaMetadata | None,
    fallback: FileMediaMetadata | None,
) -> FileMediaMetadata | None:
    if primary is None and fallback is None:
        return None

    result = FileMediaMetadata(
        duration=primary.duration if primary and primary.duration is not None else fallback.duration if fallback else None,
        width=primary.width if primary and primary.width is not None else fallback.width if fallback else None,
        height=primary.height if primary and primary.height is not None else fallback.height if fallback else None,
        thumbnail=primary.thumbnail if primary and primary.thumbnail else fallback.thumbnail if fallback else None,
    )
    return result if _has_media_metadata(result) else None


def _copy_media_metadata(metadata: FileMediaMetadata | None) -> FileMediaMetadata | None:
    if metadata is None:
        return None

    return FileMediaMetadata(
        duration=metadata.duration,
        width=metadata.width,
        height=metadata.height,
        thumbnail=metadata.thumbnail,
    )


def _has_media_metadata(metadata: FileMediaMetadata | None) -> bool:
    if metadata is None:
        return False

    return any(
        value is not None
        for value in (
            metadata.duration,
            metadata.width,
            metadata.height,
            metadata.thumbnail,
        )
    )


def _needs_probe(file_type: str, metadata: FileMediaMetadata | None) -> bool:
    if file_type in {"video", "animation"}:
        return metadata is None or any(
            value is None for value in (metadata.duration, metadata.width, metadata.height)
        )

    if file_type == "audio":
        return metadata is None or metadata.duration is None

    return False


async def _ensure_local_thumbnail(
    filepath: Path,
    file_type: str,
    duration_seconds: float | None,
) -> Path | None:
    thumb_path = filepath.with_name(f"{filepath.stem}_thumb.jpg")

    if thumb_path.exists() and thumb_path.stat().st_size > 0:
        return thumb_path

    if file_type in {"video", "animation"}:
        ok = await _generate_video_thumbnail(
            input_path=filepath,
            output_path=thumb_path,
            seek_seconds=_pick_thumbnail_seek(duration_seconds),
        )
    elif file_type == "audio":
        ok = await _extract_audio_thumbnail(
            input_path=filepath,
            output_path=thumb_path,
        )
    else:
        return None

    if not ok or not thumb_path.exists() or thumb_path.stat().st_size <= 0:
        safe_unlink(thumb_path)
        return None

    return thumb_path


def _pick_thumbnail_seek(duration_seconds: float | None) -> float:
    if duration_seconds is None or duration_seconds <= 0:
        return 1.0
    return min(max(duration_seconds * 0.1, 1.0), 5.0)


async def _generate_video_thumbnail(
    input_path: Path,
    output_path: Path,
    seek_seconds: float,
) -> bool:
    """
    Генерирует thumbnail для видео с учётом лимитов Telegram Bot API:
    - JPEG формат
    - Максимум 320px по большей стороне
    - Целевой размер до 200KB
    """
    cmd = [
        "ffmpeg",
        "-ss", f"{seek_seconds:.2f}",
        "-i", str(input_path),
        "-frames:v", "1",
        "-vf", "scale='min(320,iw)':'min(320,ih)':force_original_aspect_ratio=decrease",
        "-q:v", "5",
        "-y",
        str(output_path),
    ]

    try:
        process = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
        )

        _, stderr = await asyncio.wait_for(
            process.communicate(), timeout=30.0
        )

        if process.returncode != 0:
            safe_unlink(output_path)
            return False

        if not output_path.exists() or output_path.stat().st_size <= 0:
            return False

        # Telegram Bot API отбрасывает thumbnails > 200KB
        if output_path.stat().st_size > 200 * 1024:
            logger.warning(
                "Generated thumbnail too large (%d bytes), removing",
                output_path.stat().st_size,
            )
            safe_unlink(output_path)
            return False

        return True
    except Exception:
        safe_unlink(output_path)
        return False


async def _extract_audio_thumbnail(
    input_path: Path,
    output_path: Path,
) -> bool:
    """
    Извлекает embedded cover art для аудио с ресайзом под лимиты Telegram.
    """
    cmd = [
        "ffmpeg",
        "-i", str(input_path),
        "-map", "0:v:0",
        "-frames:v", "1",
        "-vf", "scale='min(320,iw)':'min(320,ih)':force_original_aspect_ratio=decrease",
        "-q:v", "5",
        "-y",
        str(output_path),
    ]

    try:
        process = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
        )

        _, stderr = await asyncio.wait_for(
            process.communicate(), timeout=20.0
        )

        if process.returncode != 0:
            safe_unlink(output_path)
            return False

        if not output_path.exists() or output_path.stat().st_size <= 0:
            return False

        if output_path.stat().st_size > 200 * 1024:
            logger.warning(
                "Audio thumbnail too large (%d bytes), removing",
                output_path.stat().st_size,
            )
            safe_unlink(output_path)
            return False

        return True
    except Exception:
        safe_unlink(output_path)
        return False

async def _materialize_remote_thumbnail(
    url: str,
    output_path: Path,
    timeout_s: float = 15.0,
) -> bool:
    """
    Скачивает remote thumbnail URL и приводит к лимитам Telegram Bot API.
    """
    try:
        timeout = aiohttp.ClientTimeout(total=timeout_s)
        async with aiohttp.ClientSession(timeout=timeout) as session:
            async with session.get(url, allow_redirects=True) as resp:
                if resp.status != 200:
                    return False

                data = await resp.read()

        if len(data) < 100:
            return False

        output_path.parent.mkdir(parents=True, exist_ok=True)

        # Сохраняем во временный файл, потом ресайзим через ffmpeg
        raw_path = output_path.with_name(f"{output_path.stem}_raw{output_path.suffix}")
        raw_path.write_bytes(data)

        # Ресайз под лимиты Telegram
        cmd = [
            "ffmpeg",
            "-i", str(raw_path),
            "-vf", "scale='min(320,iw)':'min(320,ih)':force_original_aspect_ratio=decrease",
            "-q:v", "5",
            "-y",
            str(output_path),
        ]

        process = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
        )
        await asyncio.wait_for(process.communicate(), timeout=15.0)

        safe_unlink(raw_path)

        if process.returncode != 0 or not output_path.exists():
            safe_unlink(output_path)
            return False

        if output_path.stat().st_size > 200 * 1024:
            logger.warning(
                "Remote thumbnail too large after resize (%d bytes), removing",
                output_path.stat().st_size,
            )
            safe_unlink(output_path)
            return False

        return True

    except Exception:
        safe_unlink(output_path)
        return False

def _coerce_float(value) -> float | None:
    try:
        if value is None or value == "":
            return None
        return float(value)
    except (TypeError, ValueError):
        return None


def _coerce_int(value) -> int | None:
    try:
        if value is None or value == "":
            return None
        return int(value)
    except (TypeError, ValueError):
        return None


async def ffmpeg_cut(
    input_path: Path, output_path: Path, duration_seconds: float
) -> bool:
    """Обрезка через ffmpeg -c copy (без перекодирования)."""
    cmd = [
        "ffmpeg",
        "-i", str(input_path),
        "-t", f"{duration_seconds:.2f}",
        "-c", "copy",
        "-avoid_negative_ts", "make_zero",
        "-y",
        str(output_path),
    ]

    try:
        process = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
        )

        try:
            _, stderr = await asyncio.wait_for(
                process.communicate(), timeout=120.0
            )
        except asyncio.TimeoutError:
            with suppress(ProcessLookupError):
                process.kill()
                await process.wait()
            logger.warning("ffmpeg timed out cutting %s", input_path.name)
            return False

        if process.returncode != 0:
            error_msg = stderr.decode("utf-8", errors="replace").strip()[:200]
            logger.warning(
                "ffmpeg cut failed | file=%s returncode=%d error=%s",
                input_path.name,
                process.returncode,
                error_msg,
            )
            return False

        return output_path.exists() and output_path.stat().st_size > 0

    except Exception as e:
        logger.warning("ffmpeg error: %s", e)
        return False


async def download_hls_stream(
    manifest_url: str,
    output_path: Path,
    timeout_seconds: float = 900.0,
    headers: dict[str, str] | None = None,
) -> bool:
    """
    Скачивает HLS/DASH manifest в локальный файл через ffmpeg.

    Аналог серверного downloader_app.utils.ffmpeg.download_hls_stream,
    но полностью автономный — работает только через subprocess.

    headers:
        Опциональные browser-like headers для нестабильных CDN
        (например TikTok / Instagram / некоторые edge CDN).

    Returns:
        True при успехе, False при ошибке.
    """
    cmd = ["ffmpeg"]

    if headers:
        user_agent = headers.get("User-Agent") or headers.get("user-agent")
        if user_agent:
            cmd.extend(["-user_agent", user_agent])

        extra_header_lines = [
            f"{key}: {value}"
            for key, value in headers.items()
            if key.lower() != "user-agent"
        ]
        if extra_header_lines:
            cmd.extend(["-headers", "\r\n".join(extra_header_lines) + "\r\n"])

    cmd.extend([
        "-i", manifest_url,
        "-c", "copy",
        "-bsf:a", "aac_adtstoasc",
        "-movflags", "+faststart",
        "-y",
        str(output_path),
    ])

    try:
        process = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
        )

        try:
            _, stderr = await asyncio.wait_for(
                process.communicate(), timeout=timeout_seconds,
            )
        except asyncio.TimeoutError:
            with suppress(ProcessLookupError):
                process.kill()
                await process.wait()
            logger.warning("HLS download timed out: %s", manifest_url[:120])
            return False

        if process.returncode != 0:
            error_msg = stderr.decode("utf-8", errors="replace").strip()[:300]
            logger.warning(
                "HLS download failed | url=%s returncode=%d error=%s",
                manifest_url[:120],
                process.returncode,
                error_msg,
            )
            return False

        return output_path.exists() and output_path.stat().st_size > 100

    except Exception as e:
        logger.warning("HLS download error for %s: %s", manifest_url[:120], e)
        return False


async def merge_video_audio(
    video_path: Path,
    audio_path: Path,
    output_path: Path,
    timeout_seconds: float = 300.0,
) -> bool:
    """
    Мёржит video + audio через ffmpeg -c copy.

    Аналог серверного downloader_app.utils.ffmpeg.merge_video_audio,
    но полностью автономный.

    Returns:
        True при успехе, False при ошибке.
    """
    cmd = [
        "ffmpeg",
        "-i", str(video_path),
        "-i", str(audio_path),
        "-c", "copy",
        "-movflags", "+faststart",
        "-y",
        str(output_path),
    ]

    try:
        process = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
        )

        try:
            _, stderr = await asyncio.wait_for(
                process.communicate(), timeout=timeout_seconds,
            )
        except asyncio.TimeoutError:
            with suppress(ProcessLookupError):
                process.kill()
                await process.wait()
            logger.warning("Merge timed out: %s + %s", video_path.name, audio_path.name)
            return False

        if process.returncode != 0:
            error_msg = stderr.decode("utf-8", errors="replace").strip()[:300]
            logger.warning(
                "Merge failed | returncode=%d error=%s",
                process.returncode,
                error_msg,
            )
            return False

        return output_path.exists() and output_path.stat().st_size > 100

    except Exception as e:
        logger.warning("Merge error: %s", e)
        return False