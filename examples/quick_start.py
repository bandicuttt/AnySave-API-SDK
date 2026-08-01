"""
Минимальный пример использования AnySave API SDK.

Установка:
    pip install -e .

Системные зависимости (для HLS, merge, thumbnails):
    apt install ffmpeg   # Ubuntu/Debian
    brew install ffmpeg  # macOS

Переменные окружения:
    DOWNLOADER_URL  — URL сервера (например https://api.example.com)
    API_TOKEN       — Bearer токен авторизации
"""

import asyncio
import os

from anysave_sdk import AnySaveClient


async def main() -> None:
    anysave = AnySaveClient(
        api_base_url=os.environ["DOWNLOADER_URL"],
        api_token=os.environ.get("API_TOKEN"),
        download_dir="./downloads",
    )

    url = "https://www.youtube.com/watch?v=dQw4w9WgXcQ"
    print(f"Скачиваю: {url}")

    result = await anysave.smart_download(url, quality="720")

    if result.is_cached:
        print(f"Из кэша: {len(result.cached.files)} файл(ов)")
        for f in result.cached.files:
            print(f"  [{f.type}] file_id={f.file_id}")
        return

    if result.ok:
        print(f"Готово: {len(result.files)} файл(ов)")
        for f in result.files:
            size_mb = (f.file_size_bytes or 0) / 1024 / 1024
            truncated = " [обрезан]" if f.is_truncated else ""
            print(f"  [{f.type}] {f.path} ({size_mb:.1f} MB){truncated}")
            if f.extra and f.extra.metadata:
                m = f.extra.metadata
                print(f"    duration={m.duration}s  {m.width}x{m.height}")
    else:
        print(f"Ошибка [{result.error.code}]: {result.error_msg}")


if __name__ == "__main__":
    asyncio.run(main())