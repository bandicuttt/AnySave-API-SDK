"""
Telegram-бот на aiogram 2 — базовый пример (legacy).

Для новых проектов рекомендуется aiogram3_basic.py.

Установка:
    python -m pip install -e ".[aiogram2]"

Системные зависимости:
    apt install ffmpeg

Переменные окружения:
    BOT_TOKEN       — Telegram Bot Token
    DOWNLOADER_URL  — URL сервера
    API_TOKEN       — Bearer токен (опционально)
    DOWNLOAD_DIR    — директория для файлов (default: ./downloads)
"""

import logging
import os
from pathlib import Path

from aiogram import Bot, Dispatcher, executor, types
from aiogram.types import InputFile, MediaGroup

from anysave_sdk import AnySaveClient

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# ─── Конфигурация ────────────────────────────────────────────

BOT_TOKEN: str = os.environ["BOT_TOKEN"]
DOWNLOADER_URL: str = os.environ["DOWNLOADER_URL"]
API_TOKEN: str | None = os.environ.get("API_TOKEN")
DOWNLOAD_DIR: str = os.environ.get("DOWNLOAD_DIR", "./downloads")

bot = Bot(token=BOT_TOKEN)
dp = Dispatcher(bot)

anysave = AnySaveClient(
    api_base_url=DOWNLOADER_URL,
    api_token=API_TOKEN,
    download_dir=DOWNLOAD_DIR,
    max_concurrency=4,
    concurrency_max_ttl_s=60.0,
)

# ─── Handlers ────────────────────────────────────────────────


@dp.message_handler(commands=["start"])
async def cmd_start(message: types.Message) -> None:
    await message.answer(
        "👋 Привет! Отправь ссылку или используй:\n"
        "/download <url> — скачать по ссылке\n\n"
        "Поддерживаю YouTube, TikTok, Instagram, Twitter/X и 50+ платформ."
    )


@dp.message_handler(commands=["download"])
async def cmd_download(message: types.Message) -> None:
    args = message.get_args()
    if not args:
        await message.answer("❌ Укажи URL: /download https://youtu.be/...")
        return
    await _handle_url(message, args.strip())


@dp.message_handler(lambda m: m.text and m.text.startswith(("http://", "https://")))
async def handle_url_message(message: types.Message) -> None:
    await _handle_url(message, message.text.strip())


async def _handle_url(message: types.Message, url: str) -> None:
    status_msg = await message.answer("⏳ Скачиваю...")

    result = await anysave.smart_download(url, max_file_size_mb=50)

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


async def _send_single(message: types.Message, f) -> None:
    input_file = InputFile(f.path)
    meta = f.extra.metadata if f.extra else None

    if f.type == "video":
        thumb = InputFile(meta.thumbnail) if (
            meta and meta.thumbnail and Path(meta.thumbnail).exists()
        ) else None
        await message.answer_video(
            input_file,
            duration=int(meta.duration) if meta and meta.duration else None,
            width=meta.width if meta else None,
            height=meta.height if meta else None,
            thumb=thumb,
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


async def _send_group(message: types.Message, files: list) -> None:
    for i in range(0, len(files), 10):
        batch = files[i : i + 10]
        media = MediaGroup()
        for f in batch:
            input_file = InputFile(f.path)
            if f.type in ("video", "animation"):
                media.attach_video(input_file)
            elif f.type in ("photo", "image"):
                media.attach_photo(input_file)
            else:
                media.attach({"type": "document", "media": input_file})
        await message.answer_media_group(media)


# ─── Entry point ─────────────────────────────────────────────

if __name__ == "__main__":
    executor.start_polling(dp, skip_updates=True)