"""
Поиск музыки: POST /search/music (Deezer → iTunes fallback).

Установка:
    pip install anysave-api-sdk

Переменные окружения:
    DOWNLOADER_URL  — URL сервера (например https://api.anysave.click)
    API_TOKEN       — Bearer токен авторизации

Сценарий: ищем треки → листаем страницы через next_cursor →
передаём yt_query в search_youtube().
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

    query = "The Weeknd Blinding Lights"
    print(f"Поиск: {query}\n")

    try:
        # 1-я страница
        page = await anysave.search_music(query, limit=10)
        print(f"Найдено всего: {page.total_results}")

        all_tracks = list(page.results)

        # next_cursor — непрозрачная строка, парсить нельзя, только передавать
        while page.has_more and len(all_tracks) < 25:
            page = await anysave.search_music(query, limit=10, cursor=page.next_cursor)
            all_tracks.extend(page.results)

        print(f"Загружено страниц: получено {len(all_tracks)} трек(ов)\n")

        for i, track in enumerate(all_tracks[:10], start=1):
            source = "Deezer" if track.is_deezer else "iTunes"
            print(
                f"{i:2}. {track.artist} — {track.title} "
                f"[{fmt_duration(track.duration_seconds)}] ({source})"
            )
            if track.album:
                print(f"     альбом: {track.album}, релиз: {track.release_date or '?'}")

        # 2) Готовый запрос для YouTube-поиска (или свой: f"{artist} {title}")
        first = all_tracks[0]
        print(f"\nyt_query для search_youtube(): {first.yt_query!r}")
        yt_page = await anysave.search_youtube(first.yt_query, limit=3)
        for hit in yt_page.results:
            print(f"  → {hit.title} [{fmt_duration(hit.duration_seconds)}] {hit.url}")

    except SearchError as e:
        if e.code == "rate_limit_exceeded":
            print(f"Rate limit, повтор через {e.retry_after or '?'}s")
        elif e.code == "insufficient_usage_tokens":
            print("Закончились usage-токены")
        else:
            print(f"Ошибка [{e.code}]: {e.detail}")
        raise


if __name__ == "__main__":
    asyncio.run(main())
