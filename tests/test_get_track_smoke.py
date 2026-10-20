"""
Smoke-тесты AnySaveClient.get_track() — без сети, всё замокано.

Запуск:
    venv/bin/python tests/test_get_track_smoke.py
    venv/bin/python -m pytest tests/test_get_track_smoke.py -q

Покрытие:
- happy path: /search/music → /search/youtube → download_via_tasks (mode="audio"),
- прокидывание _skip_cache (Telegram cache bypass),
- выбор YouTube-кандидата по ближайшей длительности,
- пустой поиск / нет кандидатов / index вне диапазона → no_results без исключений,
- готовый track= (шаг /search/music пропускается), yt_query override/fallback,
- прокидывание mode/quality/use_cookies/max_file_size_mb в smart_download,
- проброс SearchError из поиска и ошибок скачивания из DownloadResult,
- локальная валидация (query/track/index) до любых HTTP-вызовов.
"""

from __future__ import annotations

import asyncio
import contextlib
import sys
import traceback
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from anysave_sdk import AnySaveClient, SearchError  # noqa: E402
from anysave_sdk.models import (  # noqa: E402
    ClientError,
    DownloadedFile,
    DownloadResult,
    DownloadStatus,
    MusicSearchResponse,
    MusicSearchResult,
    YoutubeSearchHit,
    YoutubeSearchResponse,
)

API = "https://api.test"


def make_client() -> AnySaveClient:
    return AnySaveClient(api_base_url=API, api_token="test-token")


def make_track(**overrides) -> MusicSearchResult:
    data = dict(
        id="3135556",
        title="Blinding Lights",
        artist="The Weeknd",
        duration_seconds=200.4,
        yt_query="The Weeknd - Blinding Lights official audio",
        source="itunes",
    )
    data.update(overrides)
    return MusicSearchResult(**data)


def make_music_page(*tracks) -> MusicSearchResponse:
    return MusicSearchResponse(
        results=list(tracks), total_results=len(tracks), query="q"
    )


def make_hit(video_id: str, duration: float | None) -> YoutubeSearchHit:
    return YoutubeSearchHit(
        url=f"https://www.youtube.com/watch?v={video_id}",
        title=video_id,
        duration_seconds=duration,
    )


def make_yt_page(*hits) -> YoutubeSearchResponse:
    return YoutubeSearchResponse(results=list(hits), query="q")


def dl_ok() -> DownloadResult:
    return DownloadResult(
        status=DownloadStatus.SUCCESS,
        files=[DownloadedFile(type="audio", path="/tmp/x.mp3")],
    )


def dl_error(code: str, detail: str) -> DownloadResult:
    return DownloadResult(
        status=DownloadStatus.ERROR,
        files=[],
        error=ClientError(code=code, detail=detail),
    )


@contextlib.contextmanager
def patched(music=None, yt=None, download=None):
    """Моки fetch_music_search / fetch_youtube_search / download_via_tasks."""
    music_mock = mock.AsyncMock(return_value=music)
    yt_mock = mock.AsyncMock(return_value=yt)
    dl_mock = mock.AsyncMock(return_value=download)
    with (
        mock.patch("anysave_sdk.downloader.fetch_music_search", new=music_mock),
        mock.patch("anysave_sdk.downloader.fetch_youtube_search", new=yt_mock),
        mock.patch.object(AnySaveClient, "download_via_tasks", new=dl_mock),
    ):
        yield music_mock, yt_mock, dl_mock


def run(coro):
    return asyncio.run(coro)


def test_happy_path_picks_closest_duration():
    track = make_track()
    page = make_music_page(track)
    yt = make_yt_page(
        make_hit("FAR", 300.0),
        make_hit("CLOSE", 201.0),
        make_hit("NONE", None),
    )
    with patched(music=page, yt=yt, download=dl_ok()) as (music, yt_mock, dl):
        got = run(make_client().get_track("The Weeknd Blinding Lights"))

    # Полный успех: трек + ближайший по длительности хит + файлы
    assert got.ok
    assert got.track is track
    assert got.hit.video_id == "CLOSE"
    assert got.files[0].path == "/tmp/x.mp3"
    assert got.error_msg is None
    assert got.query == "The Weeknd Blinding Lights"

    # Музыкальный поиск с исходным запросом
    assert music.await_args.kwargs["query"] == "The Weeknd Blinding Lights"
    # YouTube-поиск по yt_query из трека
    assert yt_mock.await_args.kwargs["query"] == track.yt_query
    # Скачивание выбранного хита с дефолтными kwargs
    args, kwargs = dl.await_args
    assert args[0].endswith("watch?v=CLOSE")
    assert kwargs == {
        "mode": "audio",
        "quality": "max",
        "use_cookies": False,
        "max_file_size_mb": None,
        "_skip_cache": False,
    }


def test_download_kwargs_passthrough():
    track = make_track()
    yt = make_yt_page(make_hit("AAAA", 201.0))
    with patched(music=make_music_page(track), yt=yt, download=dl_ok()) as (_, _, dl):
        got = run(
            make_client().get_track(
                "q",
                mode="video",
                quality="720",
                use_cookies=True,
                max_file_size_mb=50,
                _skip_cache=True,
            )
        )

    assert got.ok
    _, kwargs = dl.await_args
    assert kwargs == {
        "mode": "video",
        "quality": "720",
        "use_cookies": True,
        "max_file_size_mb": 50,
        "_skip_cache": True,
    }


def test_empty_music_search_no_results():
    with patched(music=make_music_page(), download=dl_ok()) as (_, yt_mock, dl):
        got = run(make_client().get_track("totally missing"))

    assert not got.ok
    assert got.track is None and got.hit is None
    assert got.download.status == DownloadStatus.ERROR
    assert got.download.error.code == "no_results"
    assert "totally missing" in (got.download.error.detail or "")
    assert got.error_msg  # человекочитаемое сообщение
    # Дальше ничего не делаем
    yt_mock.assert_not_awaited()
    dl.assert_not_awaited()


def test_index_out_of_range_no_results():
    track = make_track()
    with patched(music=make_music_page(track), download=dl_ok()) as (_, yt_mock, dl):
        got = run(make_client().get_track("q", index=5))

    assert not got.ok and got.track is None
    assert got.download.error.code == "no_results"
    assert "index=5" in (got.download.error.detail or "")
    yt_mock.assert_not_awaited()
    dl.assert_not_awaited()


def test_no_youtube_hits_no_results():
    track = make_track()
    with patched(music=make_music_page(track), yt=make_yt_page(), download=dl_ok()) as (
        _,
        _,
        dl,
    ):
        got = run(make_client().get_track("q"))

    assert not got.ok
    assert got.track is track  # метаданные есть
    assert got.hit is None  # кандидата нет
    assert got.download.error.code == "no_results"
    dl.assert_not_awaited()


def test_ready_track_skips_music_search():
    track = make_track()
    yt = make_yt_page(make_hit("BBBB", 201.0))
    with patched(yt=yt, download=dl_ok()) as (music, yt_mock, _):
        got = run(make_client().get_track(track=track))

    assert got.ok
    # Шаг /search/music пропускается
    music.assert_not_awaited()
    # Пошёл yt_query готового трека
    assert yt_mock.await_args.kwargs["query"] == track.yt_query
    # query заполняется как "{artist} - {title}"
    assert got.query == "The Weeknd - Blinding Lights"


def test_yt_query_override():
    track = make_track()
    yt = make_yt_page(make_hit("BBBB", 201.0))
    with patched(music=make_music_page(track), yt=yt, download=dl_ok()) as (
        _,
        yt_mock,
        _,
    ):
        got = run(make_client().get_track("q", yt_query="custom override"))

    assert got.ok
    assert yt_mock.await_args.kwargs["query"] == "custom override"


def test_yt_query_fallback_to_artist_title():
    track = make_track(yt_query="")
    yt = make_yt_page(make_hit("BBBB", 201.0))
    with patched(music=make_music_page(track), yt=yt, download=dl_ok()) as (
        _,
        yt_mock,
        _,
    ):
        got = run(make_client().get_track("q"))

    assert got.ok
    assert yt_mock.await_args.kwargs["query"] == "The Weeknd - Blinding Lights"


def test_track_without_duration_takes_first_hit():
    track = make_track(duration_seconds=None)
    yt = make_yt_page(make_hit("FIRST", 999.0), make_hit("SECOND", 10.0))
    with patched(music=make_music_page(track), yt=yt, download=dl_ok()):
        got = run(make_client().get_track("q"))

    assert got.ok and got.hit.video_id == "FIRST"


def test_index_selects_track():
    t1 = make_track(id="1", title="One")
    t2 = make_track(id="2", title="Two")
    yt = make_yt_page(make_hit("AAAA", 201.0))
    with patched(music=make_music_page(t1, t2), yt=yt, download=dl_ok()):
        got = run(make_client().get_track("q", index=1))

    assert got.ok and got.track is t2
    # display-query — исходный запрос, даже когда брали готовый index
    assert got.query == "q"

def test_search_error_propagates():
    err = SearchError(code="rate_limit_exceeded", status=429, retry_after=1.0)
    with mock.patch(
        "anysave_sdk.downloader.fetch_music_search",
        new=mock.AsyncMock(side_effect=err),
    ):
        try:
            run(make_client().get_track("q"))
        except SearchError as e:
            assert e.code == "rate_limit_exceeded"
            assert e.retry_after == 1.0
        else:
            raise AssertionError("SearchError must propagate from get_track")


def test_youtube_search_error_propagates():
    track = make_track()
    err = SearchError(code="youtube_search_failed", status=502)
    with mock.patch(
        "anysave_sdk.downloader.fetch_music_search",
        new=mock.AsyncMock(return_value=make_music_page(track)),
    ), mock.patch(
        "anysave_sdk.downloader.fetch_youtube_search",
        new=mock.AsyncMock(side_effect=err),
    ):
        try:
            run(make_client().get_track("q"))
        except SearchError as e:
            assert e.code == "youtube_search_failed"
        else:
            raise AssertionError("SearchError must propagate from get_track")


def test_download_error_propagated():
    track = make_track()
    yt = make_yt_page(make_hit("AAAA", 201.0))
    failed = dl_error("client_busy", "no slot for /links pipeline")
    with patched(music=make_music_page(track), yt=yt, download=failed):
        got = run(make_client().get_track("q"))

    assert not got.ok
    # Метаданные и хит сохраняются даже при ошибке скачивания
    assert got.track is track and got.hit is not None
    assert got.download.status == DownloadStatus.ERROR
    assert got.download.error.code == "client_busy"
    assert got.error_msg == "no slot for /links pipeline"
    assert got.files == []


def test_validation_before_any_http_calls():
    with patched() as (music, yt_mock, dl):
        bad_kwargs = [
            {},  # нет ни query, ни track
            {"query": "q", "track": make_track()},  # оба сразу
            {"query": "q", "index": -1},
            {"query": "q", "index": 25},
            {"query": "q", "index": True},  # bool != int
            {"query": "q", "index": 1.5},
        ]
        for kwargs in bad_kwargs:
            try:
                run(make_client().get_track(**kwargs))
            except ValueError:
                pass
            else:
                raise AssertionError(f"ValueError expected for {kwargs!r}")

        music.assert_not_awaited()
        yt_mock.assert_not_awaited()
        dl.assert_not_awaited()


def _run_all() -> int:
    tests = [
        (name, fn)
        for name, fn in sorted(globals().items())
        if name.startswith("test_") and callable(fn)
    ]
    failed = 0
    for name, fn in tests:
        try:
            fn()
        except Exception as exc:  # noqa: BLE001
            failed += 1
            traceback.print_exc()
            print(f"FAIL {name}: {exc!r}")
        else:
            print(f"ok   {name}")
    print(f"\n{len(tests) - failed} passed, {failed} failed")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(_run_all())
