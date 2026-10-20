"""
Поиск видео YouTube: POST /search/youtube (yt-dlp flat-playlist).

Установка:
    pip install anysave-api-sdk

Переменные окружения:
    DOWNLOADER_URL  — URL сервера (например https://api.anysave.click)
    API_TOKEN       — Bearer токен авторизации (план lite/pro для search.youtube)

Эндпоинт универсальный — поддерживает любой текстовый запрос:
видеоуроки, влоги, музыку. Для музыки удобно переиспользовать
yt_query из search_music().
"""

import asyncio
import os

from anysave_sdk import AnySaveClient, SearchError, fmt_duration


async def main() -> None:
    anysave = AnySaveClient(
        api_base_url=os.environ["DOWNLOADER_URL"],
        api_token=os.environ.get("API_TOKEN"),
        download_dir="./downloads",
    )

    query = "how to tie a tie tutorial"
    print(f"Поиск видео: {query}\n")

    try:
        page = await anysave.search_youtube(query, limit=5)
    except SearchError as e:
        if e.code in {"access_forbidden", "subscription_required"}:
            print("План не поддерживает /search/youtube (нужен lite/pro)")
        elif e.code == "rate_limit_exceeded":
            print(f"Rate limit 15/min, повтор через {e.retry_after or '?'}s")
        else:
            print(f"Ошибка [{e.code}]: {e.detail}")
        raise

    if page.is_empty:
        print("Ничего не найдено — попробуйте другой запрос")
        return

    # В v1 пагинации нет: next_cursor всегда None
    print(f"Результатов: {len(page.results)} (has_more={page.has_more})\n")

    for i, hit in enumerate(page.results, start=1):
        print(f"{i}. {hit.title}")
        print(f"   канал:  {hit.channel or '?'}")
        print(
            f"   длит:   {fmt_duration(hit.duration_seconds)}  "
            f"просмотры: {hit.view_count or '?'}  загружен: {hit.upload_date or '?'}"
        )
        print(f"   id:     {hit.video_id}  url: {hit.url}")

    # Готово к скачиванию:
    best = page.results[0]
    print(f"\nБерём первый: {best.title} ({fmt_duration(best.duration_seconds)})")
    # result = await anysave.smart_download(best.url, quality="720")


if __name__ == "__main__":
    asyncio.run(main())
