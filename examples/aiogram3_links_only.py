"""
Telegram-бот на aiogram 3 — только CDN-ссылки (без скачивания).

get_remote_links() возвращает прямые CDN URL.
Файлы отправляются через URLInputFile — не скачиваются на сервер бота.

Преимущества:
  - Не требует disk space
  - Нет задержки на скачивание ботом

Ограничения:
  - Только прямые HTTP-ссылки (не HLS/DASH)
  - Некоторые CDN блокируют Telegram-серверы

Установка:
    pip install "media-downloader-sdk[aiogram3]"

Переменные окружения:
    BOT_TOKEN       — Telegram Bot Token
    DOWNLOADER_URL  — URL сервера
    API_TOKEN       — Bearer токен (опционально)
"""

import asyncio
import logging
import os

from aiogram import Bot, Dispatcher, F, Router
from aiogram.filters import CommandStart
from aiogram.types import Message, URLInputFile

from anysave_sdk import AnySaveClient

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# ─── Конфигурация ────────────────────────────────────────────

BOT_TOKEN: str = os.environ["BOT_TOKEN"]
DOWNLOADER_URL: str = os.environ["DOWNLOADER_URL"]
API_TOKEN: str | None = os.environ.get("API_TOKEN")

router = Router()

# download_dir не нужен — файлы не скачиваются локально
anysave = AnySaveClient(
    api_base_url=DOWNLOADER_URL,
    api_token=API_TOKEN,
)

# ─── Handlers ────────────────────────────────────────────────


@router.message(CommandStart())
async def cmd_start(message: Message) -> None:
    await message.answer(
        "👋 Привет! Отправляю медиа по прямым CDN-ссылкам.\n\n"
        "⚡ Быстро — файлы не скачиваются на сервер бота.\n"
        "⚠️ Не работает для HLS-потоков (Twitch VOD и др.).\n\n"
        "Отправь URL:"
    )


@router.message(F.text.regexp(r"https?://\S+"))
async def handle_url(message: Message) -> None:
    url = (message.text or "").strip()
    status_msg = await message.answer("⏳ Получаю ссылки...")

    remote = await anysave.get_remote_links(url)

    if not remote.ok:
        await status_msg.edit_text(
            f"❌ Не удалось получить ссылки.\n"
            f"Причина: {remote.error_msg or 'неизвестная ошибка'}\n\n"
            "Попробуйте другой URL или скачайте напрямую."
        )
        return

    await status_msg.delete()

    # Собираем все файлы: основные + split audio (YouTube)
    all_files = list(remote.files)
    if remote.audio:
        all_files.append(remote.audio)

    sent = 0
    skipped_hls = 0

    for f in all_files:
        if f.is_hls_manifest:
            # HLS нельзя передать через URLInputFile — Telegram его не поймёт
            skipped_hls += 1
            logger.info("Skipping HLS: %s", f.url[:80])
            continue

        try:
            url_file = URLInputFile(url=f.url, filename=f.filename)

            if f.type == "video":
                await message.answer_video(url_file)
            elif f.type == "animation":
                await message.answer_animation(url_file)
            elif f.type == "audio":
                await message.answer_audio(url_file)
            elif f.type in ("photo", "image"):
                await message.answer_photo(url_file)
            else:
                await message.answer_document(url_file)

            sent += 1

        except Exception as e:
            logger.warning("Failed to send via URLInputFile | file=%s error=%s", f.filename, e)

    if skipped_hls > 0 and sent == 0:
        await message.answer(
            "⚠️ Этот контент использует HLS-поток — прямая ссылка недоступна.\n"
            "Используйте бота с полным скачиванием."
        )
    elif skipped_hls > 0:
        await message.answer(
            f"⚠️ {skipped_hls} файл(ов) пропущено — HLS не поддерживается в этом режиме."
        )


# ─── Entry point ─────────────────────────────────────────────


async def main() -> None:
    bot = Bot(token=BOT_TOKEN)
    dp = Dispatcher()
    dp.include_router(router)
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())