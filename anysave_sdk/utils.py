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


def unique_path(path: Path) -> Path:
    """Гарантирует уникальность пути, добавляя UUID-суффикс при коллизии."""
    if not path.exists():
        return path
    return path.with_name(f"{path.stem}_{uuid.uuid4().hex}{path.suffix}")


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