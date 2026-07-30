"""
Telegram-бот на aiogram 3 — базовый пример.

Использует smart_download(): автоматически выбирает fast-link или task pipeline.
Поддерживает видео, аудио, фото, карусели (до 10 файлов в группе).

Установка:
    pip install "media-downloader-sdk[aiogram3]"

Системные зависимости:
    apt install ffmpeg

Переменные окружения:
    BOT_TOKEN       — Telegram Bot Token (@BotFather)
    DOWNLOADER_URL  — URL сервера
    API_TOKEN       — Bearer токен (опционально)
    DOWNLOAD_DIR    — директория для файлов (default: ./downloads)
"""

import asyncio
import logging
import os
from pathlib import Path

from aiogram import Bot, Dispatcher, F, Router
from aiogram.filters import Command, CommandStart
from aiogram.types import (
    FSInputFile,
    InputMediaAnimation,
    InputMediaAudio,
    InputMediaDocument,
    InputMediaPhoto,
    InputMediaVideo,
    Message,
)

from anysave_sdk import AnySaveClient

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# ─── Конфигурация ────────────────────────────────────────────

BOT_TOKEN: str = os.environ["BOT_TOKEN"]
DOWNLOADER_URL: str = os.environ["DOWNLOADER_URL"]
API_TOKEN: str | None = os.environ.get("API_TOKEN")
DOWNLOAD_DIR: str = os.environ.get("DOWNLOAD_DIR", "./downloads")

router = Router()

anysave = AnySaveClient(
    api_base_url=DOWNLOADER_URL,
    api_token=API_TOKEN,
    download_dir=DOWNLOAD_DIR,
    max_concurrency=4,
    max_file_size_mb=50,        # лимит Telegram Bot API
)

# ─── Handlers ────────────────────────────────────────────────


@router.message(CommandStart())
async def cmd_start(message: Message) -> None:
    await message.answer(
        "👋 Привет! Отправь ссылку на медиа или используй:\n"
        "/download <url> — скачать по ссылке\n\n"
        "Поддерживаю YouTube, TikTok, Instagram, Twitter/X и 50+ платформ."
    )


@router.message(Command("download"))
async def cmd_download(message: Message) -> None:
    parts = (message.text or "").split(maxsplit=1)
    if len(parts) < 2 or not parts[1].strip():
        await message.answer("❌ Укажи URL: /download https://youtu.be/...")
        return
    await _handle_url(message, parts[1].strip())


@router.message(F.text.regexp(r"https?://\S+"))
async def handle_url_message(message: Message) -> None:
    await _handle_url(message, (message.text or "").strip())


async def _handle_url(message: Message, url: str) -> None:
    status_msg = await message.answer("⏳ Скачиваю...")

    result = await anysave.smart_download(url)

    if not result.ok:
        await status_msg.edit_text(
            f"❌ Не удалось скачать.\n"
            f"Причина: {result.error_msg or 'неизвестная ошибка'}"
        )
        return

    await status_msg.delete()

    try:
        if len(result.files) == 1:
            await _send_single(message, result.files[0])
        else:
            await _send_group(message, result.files)

        if any(f.is_truncated for f in result.files):
            await message.answer("⚠️ Файл обрезан — превышен лимит размера.")

    except Exception as e:
        logger.exception("Failed to send media: %s", e)
        await message.answer("❌ Не удалось отправить файл в Telegram.")
    finally:
        for f in result.files:
            Path(f.path).unlink(missing_ok=True)


async def _send_single(message: Message, f) -> None:
    input_file = FSInputFile(f.path)
    meta = f.extra.metadata if f.extra else None

    if f.type == "video":
        thumb = _thumb_input(meta)
        await message.answer_video(
            input_file,
            duration=int(meta.duration) if meta and meta.duration else None,
            width=meta.width if meta else None,
            height=meta.height if meta else None,
            thumbnail=thumb,
        )
    elif f.type == "animation":
        await message.answer_animation(input_file)
    elif f.type == "audio":
        await message.answer_audio(
            input_file,
            duration=int(meta.duration) if meta and meta.duration else None,
        )
    elif f.type in ("photo", "image"):
        await message.answer_photo(input_file)
    else:
        await message.answer_document(input_file)


async def _send_group(message: Message, files: list) -> None:
    # Telegram: максимум 10 файлов в группе
    for i in range(0, len(files), 10):
        batch = files[i : i + 10]
        media = [_to_input_media(f) for f in batch]
        if media:
            await message.answer_media_group(media)


def _to_input_media(f):
    input_file = FSInputFile(f.path)
    if f.type == "video":
        return InputMediaVideo(media=input_file)
    if f.type == "animation":
        return InputMediaAnimation(media=input_file)
    if f.type in ("photo", "image"):
        return InputMediaPhoto(media=input_file)
    if f.type == "audio":
        return InputMediaAudio(media=input_file)
    return InputMediaDocument(media=input_file)


def _thumb_input(meta) -> FSInputFile | None:
    if meta and meta.thumbnail and Path(meta.thumbnail).exists():
        return FSInputFile(meta.thumbnail)
    return None


# ─── Entry point ─────────────────────────────────────────────


async def main() -> None:
    bot = Bot(token=BOT_TOKEN)
    dp = Dispatcher()
    dp.include_router(router)
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())