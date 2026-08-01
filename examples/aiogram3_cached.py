"""
Telegram-бот на aiogram 3 — с Telegram cache.

prefer_telegram_cache=True: перед скачиванием SDK проверяет кэш.
При повторном запросе того же URL файл пересылается через file_id —
без повторной загрузки в Telegram. Экономит трафик и время.

Установка:
    python -m pip install -e ".[aiogram3]"

Переменные окружения:
    BOT_TOKEN       — Telegram Bot Token
    DOWNLOADER_URL  — URL сервера
    API_TOKEN       — Bearer токен (опционально)
    DOWNLOAD_DIR    — директория для файлов (default: ./downloads)
"""

import asyncio
import logging
import os
from pathlib import Path

from aiogram import Bot, Dispatcher, F, Router
from aiogram.filters import CommandStart
from aiogram.types import FSInputFile, Message

from anysave_sdk import AnySaveClient

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# ─── Конфигурация ────────────────────────────────────────────

BOT_TOKEN: str = os.environ["BOT_TOKEN"]
DOWNLOADER_URL: str = os.environ["DOWNLOADER_URL"]
API_TOKEN: str | None = os.environ.get("API_TOKEN")
DOWNLOAD_DIR: str = os.environ.get("DOWNLOAD_DIR", "./downloads")

router = Router()

# prefer_telegram_cache=True — ключевая настройка.
# cache_lookup_timeout_s=5.0 — быстрый таймаут, кэш не тормозит основной flow.
anysave = AnySaveClient(
    api_base_url=DOWNLOADER_URL,
    api_token=API_TOKEN,
    download_dir=DOWNLOAD_DIR,
    prefer_telegram_cache=True,
    cache_lookup_timeout_s=5.0,
    max_concurrency=4,
    concurrency_max_ttl_s=60.0,
)

# ─── Handlers ────────────────────────────────────────────────


@router.message(CommandStart())
async def cmd_start(message: Message) -> None:
    await message.answer(
        "👋 Привет! Я кэширую медиа в Telegram.\n\n"
        "При повторных запросах одного URL — отвечаю мгновенно ⚡\n"
        "Просто отправь URL:"
    )


@router.message(F.text.regexp(r"https?://\S+"))
async def handle_url(message: Message) -> None:
    url = (message.text or "").strip()
    status_msg = await message.answer("⏳ Проверяю кэш...")

    result = await anysave.smart_download(url, max_file_size_mb=50)

    # ─── Cache hit: пересылаем через file_id ─────────────────
    if result.is_cached:
        await status_msg.delete()
        cache = result.cached
        logger.info("Cache hit | url=%s files=%d", url, len(cache.files))

        sent = False
        for f in cache.files:
            try:
                if f.type == "video":
                    await message.answer_video(f.file_id)
                elif f.type == "audio":
                    await message.answer_audio(f.file_id)
                elif f.type == "animation":
                    await message.answer_animation(f.file_id)
                elif f.type in ("photo", "image"):
                    await message.answer_photo(f.file_id)
                else:
                    await message.answer_document(f.file_id)
                sent = True
            except Exception as e:
                logger.warning("Failed to send cached file_id=%s: %s", f.file_id, e)

        if sent:
            await message.answer("⚡ Из кэша!")
        return

    # ─── Cache miss: скачиваем ───────────────────────────────
    if not result.ok:
        await status_msg.edit_text(
            f"❌ Не удалось скачать.\n"
            f"Причина: {result.error_msg or 'неизвестная ошибка'}"
        )
        return

    await status_msg.edit_text("📤 Отправляю...")

    try:
        for f in result.files:
            input_file = FSInputFile(f.path)
            meta = f.extra.metadata if f.extra else None

            if f.type == "video":
                thumb = None
                if meta and meta.thumbnail and Path(meta.thumbnail).exists():
                    thumb = FSInputFile(meta.thumbnail)
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
                await message.answer_audio(input_file)
            elif f.type in ("photo", "image"):
                await message.answer_photo(input_file)
            else:
                await message.answer_document(input_file)

        await status_msg.delete()

        if any(f.is_truncated for f in result.files):
            await message.answer("⚠️ Файл обрезан — превышен лимит размера.")

    except Exception as e:
        logger.exception("Failed to send media: %s", e)
        await message.answer("❌ Не удалось отправить файл.")
    finally:
        for f in result.files:
            Path(f.path).unlink(missing_ok=True)


# ─── Entry point ─────────────────────────────────────────────


async def main() -> None:
    bot = Bot(token=BOT_TOKEN)
    dp = Dispatcher()
    dp.include_router(router)
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())