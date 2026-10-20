"""
Трек целиком одним вызовом: get_track() = инфо + YouTube + скачивание.

Установка:
    pip install anysave-api-sdk

Переменные окружения:
    DOWNLOADER_URL  — URL сервера (например https://api.anysave.click)
    API_TOKEN       — Bearer токен авторизации

Сценарий: get_track() под капотом делает /search/music → /search/youtube
(лучший кандидат по длительности) → download_via_tasks(mode="audio")
и возвращает единый TrackResult: track + hit + download.
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
    print(f"Запрос: {query}\n")

    try:
        # 1) Полный цикл: метаданные → YouTube → файл (mode="audio" по умолчанию)
        got = await anysave.get_track(query)

        if not got.ok:
            # Пустой поиск — не исключение, а код no_results в download
            print(f"Не получилось: {got.error_msg}")
            return

        track, hit = got.track, got.hit
        source = "Deezer" if track.is_deezer else "iTunes"
        print(f"Трек: {track.artist} — {track.title} [{fmt_duration(track.duration_seconds)}]")
        print(f"Источник: {source}, альбом: {track.album or '?'}")
        print(f"YouTube: {hit.title} [{fmt_duration(hit.duration_seconds)}]")
        print(f"URL: {hit.url}")

        for f in got.files:
            print(f"Файл: {f.path} ({f.file_size_bytes or '?'} bytes, cached={got.is_cached})")

        # 2) Готовый трек из чарта — шаг /search/music пропускается
        chart = await anysave.search_chart(country="0", limit=5)
        got2 = await anysave.get_track(track=chart.results[0], mode="audio")
        print(f"\nИз чарта: ok={got2.ok}, error={got2.error_msg}")
        if got2.ok:
            print(f"  {got2.track.artist} — {got2.track.title} → {got2.files[0].path}")

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