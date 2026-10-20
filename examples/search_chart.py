"""
Глобальный чарт Deezer: GET /search/chart.

Установка:
    pip install anysave-api-sdk

Переменные окружения:
    DOWNLOADER_URL  — URL сервера (например https://api.anysave.click)
    API_TOKEN       — Bearer токен авторизации
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

    print("Глобальный чарт Deezer:\n")

    try:
        page = await anysave.search_chart(limit=10)

        all_tracks = list(page.results)
        # Пагинация по чарту — через непрозрачный cursor (как в search_music)
        while page.has_more and len(all_tracks) < 50:
            page = await anysave.search_chart(
                limit=10, cursor=page.next_cursor
            )
            all_tracks.extend(page.results)

        if not all_tracks:
            print("Чарт пуст")
            return

        print(f"Получено {len(all_tracks)} трек(ов)\n")

        for i, track in enumerate(all_tracks[:25], start=1):
            print(
                f"{i:2}. {track.artist} — {track.title} "
                f"[{fmt_duration(track.duration_seconds)}] {track.source_url or ''}"
            )

        # Мост в скачивание: yt_query → search_youtube → smart_download
        top = all_tracks[0]
        print(f"\nТоп-1 yt_query: {top.yt_query!r}")

    except SearchError as e:
        if e.code == "chart_unavailable":
            print("Deezer недоступен (502 chart_unavailable) — попробуйте позже")
        elif e.code == "rate_limit_exceeded":
            print(f"Rate limit, повтор через {e.retry_after or '?'}s")
        else:
            print(f"Ошибка [{e.code}]: {e.detail}")
        raise


if __name__ == "__main__":
    asyncio.run(main())
