"""
Telegram-бот на aiogram 3 — task pipeline.

download_via_tasks(): создаёт задачу на сервере, ждёт завершения, скачивает результат.
Подходит для YouTube 4K, длинных VOD и других тяжёлых форматов.

Когда использовать task pipeline вместо smart_download:
  - Видео скачивается > 2 минут (YouTube 4K, длинные стримы)
  - Нужен явный контроль над таймаутом ожидания
  - Инфраструктура требует неблокирующей очереди

Установка:
    pip install "media-downloader-sdk[aiogram3]"

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

from anysave_sdk import AnySaveClient, ClientErrorCode, DownloadStatus

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# ─── Конфигурация ────────────────────────────────────────────

BOT_TOKEN: str = os.environ["BOT_TOKEN"]
DOWNLOADER_URL: str = os.environ["DOWNLOADER_URL"]
API_TOKEN: str | None = os.environ.get("API_TOKEN")
DOWNLOAD_DIR: str = os.environ.get("DOWNLOAD_DIR", "./downloads")

router = Router()

# task_max_poll_time_s=600 — ждём до 10 минут
# task_poll_interval_s=2.0 — опрашиваем каждые 2 секунды
anysave = AnySaveClient(
    api_base_url=DOWNLOADER_URL,
    api_token=API_TOKEN,
    download_dir=DOWNLOAD_DIR,
    task_poll_interval_s=2.0,
    task_max_poll_time_s=600.0,
    task_create_timeout_s=30.0,
    max_file_size_mb=500,
)

# ─── Handlers ────────────────────────────────────────────────


@router.message(CommandStart())
async def cmd_start(message: Message) -> None:
    await message.answer(
        "👋 Привет! Скачиваю тяжёлые видео через очередь задач.\n\n"
        "Подходит для YouTube 4K и длинных VOD.\n"
        "Может занять несколько минут — я сообщу когда готово.\n\n"
        "Отправь URL:"
    )


@router.message(F.text.regexp(r"https?://\S+"))
async def handle_url(message: Message) -> None:
    url = (message.text or "").strip()
    status_msg = await message.answer("⏳ Создаю задачу на скачивание...")

    result = await anysave.download_via_tasks(url)

    # ─── Таймаут ─────────────────────────────────────────────
    if result.error and result.error.code == ClientErrorCode.TASK_TIMEOUT:
        await status_msg.edit_text(
            "⏰ Превышено время ожидания (10 минут).\n"
            "Сервер всё ещё скачивает — попробуйте через несколько минут."
        )
        return

    # ─── Другие ошибки ───────────────────────────────────────
    if not result.ok:
        await status_msg.edit_text(
            f"❌ Не удалось скачать.\n"
            f"Причина: {result.error_msg or 'неизвестная ошибка'}"
        )
        return

    await status_msg.edit_text("✅ Готово! Отправляю...")

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
                await message.answer_audio(
                    input_file,
                    duration=int(meta.duration) if meta and meta.duration else None,
                )
            elif f.type in ("photo", "image"):
                await message.answer_photo(input_file)
            else:
                await message.answer_document(input_file)

        await status_msg.delete()

        if result.status == DownloadStatus.PARTIAL:
            await message.answer(f"⚠️ Частичный результат: {result.error_msg}")

        if any(f.is_truncated for f in result.files):
            await message.answer("⚠️ Файл обрезан — превышен лимит размера.")

    except Exception as e:
        logger.exception("Failed to send media: %s", e)
        await message.answer("❌ Не удалось отправить файл в Telegram.")
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