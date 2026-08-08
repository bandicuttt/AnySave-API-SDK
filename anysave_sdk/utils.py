# anysave_sdk/utils.py

"""Общие утилиты клиента скачивания."""

import uuid
import re

from contextlib import suppress
from pathlib import Path


_UNSAFE_CHARS = re.compile(r'[<>:"/\\|?*\x00-\x1f]')
_MAX_FILENAME_LENGTH = 200


def safe_filename(filename: str) -> str:
    """Безопасное имя файла из произвольной строки."""
    name = (filename or "").strip()

    # Убираем путь
    name = name.replace("\\", "/").split("/")[-1]

    # Убираем небезопасные символы
    name = _UNSAFE_CHARS.sub("_", name)

    # Убираем leading dots (скрытые файлы, ..)
    name = name.lstrip(".")

    # Ограничиваем длину (сохраняя расширение)
    if len(name) > _MAX_FILENAME_LENGTH:
        stem, _, ext = name.rpartition(".")
        if ext and len(ext) <= 10:
            max_stem = _MAX_FILENAME_LENGTH - len(ext) - 1
            name = f"{stem[:max_stem]}.{ext}"
        else:
            name = name[:_MAX_FILENAME_LENGTH]

    return name or str(uuid.uuid4())


def normalize_extension(filename: str, file_type: str) -> str:
    """
    Нормализует расширение файла в зависимости от логического типа.
    
    Если файл уже имеет подходящее расширение — оно сохраняется.
    Если нет — заменяется на дефолтное для данного типа.
    """
    safe_name = safe_filename(filename)
    mapped_type = map_type(file_type)

    stem, _, ext = safe_name.rpartition(".")
    ext_lower = ext.lower()

    if mapped_type == "photo":
        if ext_lower not in {"jpg", "jpeg", "png", "webp"}:
            return f"{stem or safe_name}.jpg"
    elif mapped_type == "animation":
        if ext_lower not in {"mp4", "webm", "gif"}:
            return f"{stem or safe_name}.mp4"
    elif mapped_type == "audio":
        if ext_lower not in {"mp3", "m4a", "ogg", "wav"}:
            return f"{stem or safe_name}.mp3"
    elif mapped_type == "video":
        if ext_lower not in {"mp4", "webm", "mkv", "mov"}:
            return f"{stem or safe_name}.mp4"

    return safe_name


def unique_path(path: Path) -> Path:
    """Возвращает уникальный путь с UUID-суффиксом, избегая гонок по имени файла."""
    stem = path.stem or "file"
    return path.with_name(f"{stem}_{uuid.uuid4().hex}{path.suffix}")


def map_type(t: str) -> str:
    """Маппинг API-типа файла в клиентский тип."""
    value = (t or "").lower()

    if value in {"image", "picture", "photo"}:
        return "photo"

    if value == "audio":
        return "audio"

    if value in {"gif", "animation"}:
        return "animation"

    return "video"


def fmt_size(size: int | None) -> str:
    """Человекочитаемый размер файла."""
    if size is None or size < 0:
        return "unknown"
    if size < 1024:
        return f"{size} B"
    if size < 1024 * 1024:
        return f"{size / 1024:.1f} KB"
    return f"{size / (1024 * 1024):.2f} MB"


def safe_unlink(path: Path) -> None:
    """Безопасное удаление файла без исключений."""
    with suppress(Exception):
        if path and path.exists():
            path.unlink()