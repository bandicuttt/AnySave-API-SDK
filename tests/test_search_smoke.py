"""
Smoke-тесты Search API SDK (без сети: httpx замокан).

Запуск:
    venv/bin/python tests/test_search_smoke.py

Покрытие:
- разбор envelope + модели (music / chart / youtube),
- пагинация cursor'ом (pass-through),
- локальная валидация (ValueError до HTTP),
- ретраи 429 (Retry-After) и 502 (backoff), исчерпание попыток,
- 401/422 без ретраев, сетевые ошибки,
- прокидывание конфига клиента (timeout / retries).
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path
from unittest import mock

import httpx

# запуск из любого каталога: venv/bin/python tests/test_search_smoke.py
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from anysave_sdk import AnySaveClient, SearchError, fmt_duration  # noqa: E402
from anysave_sdk.models import (  # noqa: E402
    MusicChartResponse,
    MusicSearchResponse,
    YoutubeSearchResponse,
)

BASE = "https://api.test"
TOKEN = "test-token"

MUSIC_OK = {
    "status": "ok",
    "ok": True,
    "data": {
        "results": [
            {
                "id": 3135556,  # int (iTunes) — должен нормализоваться в str
                "title": "Blinding Lights",
                "artist": "The Weeknd",
                "album": "After Hours",
                "duration_seconds": 200.4,
                "cover_url_small": "https://img/s.jpg",
                "cover_url_medium": "https://img/m.jpg",
                "cover_url_big": "https://img/b.jpg",
                "preview_url": None,
                "release_date": "2019-11-29",
                "isrc": None,
                "is_explicit": False,
                "source": "itunes",
                "source_url": "https://music.apple.com/x",
                "yt_query": "The Weeknd - Blinding Lights official audio",
            },
            {
                "id": 777,
                "title": "One Kiss",
                "artist": "Calvin Harris",
                "album": "FM4",
                "duration_seconds": 214,
                "preview_url": "https://preview.mp3",
                "source": "deezer",
                "yt_query": "Calvin Harris - One Kiss official audio",
            },
        ],
        "total_results": 42,
        "next_cursor": "opaque-c2",
        "query": "the weeknd",
    },
}

YOUTUBE_OK = {
    "status": "ok",
    "ok": True,
    "data": {
        "results": [
            {
                "url": "https://www.youtube.com/watch?v=dQw4w9WgXcQ",
                "title": "Rick Astley - Never Gonna Give You Up",
                "channel": "Rick Astley",
                "duration_seconds": 213.0,
                "view_count": 1_000_000,
                "thumbnail_url": "https://i.ytimg.com/vi/dQw4w9WgXcQ/hq.jpg",
                "upload_date": "20091025",
            }
        ],
        "next_cursor": None,
        "query": "rick roll",
    },
}

CHART_OK = {
    "status": "ok",
    "ok": True,
    "data": {
        "results": [
            {
                "id": 3135556,
                "title": "Blinding Lights",
                "artist": "The Weeknd",
                "duration_seconds": 200,
                "source": "deezer",
                "yt_query": "The Weeknd - Blinding Lights official audio",
            }
        ],
        "total_results": 100,
        "next_cursor": None,
        "query": "chart",
    },
}


def err_envelope(code: str, detail: str = "boom", extra: dict | None = None) -> dict:
    error: dict = {"code": code, "detail": detail}
    if extra is not None:
        error["extra"] = extra
    return {"status": "error", "ok": False, "error": error}


def resp(status: int, body: dict | None = None, headers: dict | None = None) -> httpx.Response:
    if body is not None:
        return httpx.Response(status, json=body, headers=headers or {})
    return httpx.Response(status, text="", headers=headers or {})


def make_cm(posts=None, gets=None):
    """httpx.AsyncClient context manager с AsyncMock-последовательностями."""
    cm = mock.MagicMock()
    client_obj = cm.__aenter__.return_value
    if posts is not None:
        client_obj.post = mock.AsyncMock(side_effect=posts)
    if gets is not None:
        client_obj.get = mock.AsyncMock(side_effect=gets)
    return cm


def make_client() -> AnySaveClient:
    return AnySaveClient(api_base_url=BASE, api_token=TOKEN, download_dir="/tmp/anysave-test")


def no_sleep():
    return mock.patch("anysave_sdk.search.asyncio.sleep", new=mock.AsyncMock())


# ─── Success paths ────────────────────────────────────────────


def test_music_search_ok() -> None:
    cm = make_cm(posts=[resp(200, MUSIC_OK)])
    with mock.patch("httpx.AsyncClient", return_value=cm) as ctor, no_sleep():
        page = asyncio.run(make_client().search_music("  the weeknd  ", limit=10))

    assert isinstance(page, MusicSearchResponse)
    assert page.total_results == 42
    assert page.has_more and not page.is_empty
    assert page.next_cursor == "opaque-c2"

    t = page.results[0]
    assert t.id == "3135556"  # int → str
    assert t.is_itunes and not t.is_deezer
    assert t.yt_query.endswith("official audio")
    assert page.results[1].is_deezer

    # запрос ушёл trim + таймаут клиента прокинут
    body = cm.__aenter__.return_value.post.await_args.kwargs["json"]
    assert body == {"query": "the weeknd", "limit": 10}
    assert ctor.call_args.kwargs["timeout"].connect == 60.0  # search_timeout_s


def test_music_search_cursor_passthrough() -> None:
    cm = make_cm(posts=[resp(200, MUSIC_OK)])
    with mock.patch("httpx.AsyncClient", return_value=cm):
        asyncio.run(make_client().search_music("q", cursor="very-secret.cursor=="))

    body = cm.__aenter__.return_value.post.await_args.kwargs["json"]
    # курсор непрозрачен: уходит ровно тем же байт-в-байт
    assert body["cursor"] == "very-secret.cursor=="


def test_youtube_search_ok_and_video_id() -> None:
    cm = make_cm(posts=[resp(200, YOUTUBE_OK)])
    with mock.patch("httpx.AsyncClient", return_value=cm):
        page = asyncio.run(make_client().search_youtube("rick roll", limit=5))

    assert isinstance(page, YoutubeSearchResponse)
    hit = page.results[0]
    assert hit.video_id == "dQw4w9WgXcQ"
    assert hit.upload_date == "20091025"
    assert page.next_cursor is None and not page.has_more
    assert not page.is_empty


def test_chart_get_params() -> None:
    cm = make_cm(gets=[resp(200, CHART_OK)])
    with mock.patch("httpx.AsyncClient", return_value=cm):
        page = asyncio.run(make_client().search_chart(limit=25))

    assert isinstance(page, MusicChartResponse)
    assert page.results[0].is_deezer

    kwargs = cm.__aenter__.return_value.get.await_args.kwargs
    assert kwargs["params"] == {"limit": "25"}
    assert kwargs["headers"]["Authorization"] == f"Bearer {TOKEN}"


def test_empty_results_is_ok() -> None:
    body = {
        "status": "ok",
        "ok": True,
        "data": {"results": [], "total_results": 0, "next_cursor": None, "query": "zzz"},
    }
    cm = make_cm(posts=[resp(200, body)])
    with mock.patch("httpx.AsyncClient", return_value=cm):
        page = asyncio.run(make_client().search_music("zzz"))
    assert page.is_empty and not page.has_more


def test_fmt_duration() -> None:
    assert fmt_duration(95.4) == "1:35"
    assert fmt_duration(3661) == "1:01:01"
    assert fmt_duration(None) == "--:--"
    assert fmt_duration(0) == "0:00"


# ─── Local validation (ValueError до HTTP) ────────────────────


def test_validation_no_http_call() -> None:
    cases = [
        ("search_music", ("",)),
        ("search_music", ("q", 0)),
        ("search_music", ("q", 26)),
        ("search_music", ("q", True)),  # bool ≠ int
        ("search_youtube", ("x" * 301,)),
    ]
    with mock.patch("httpx.AsyncClient") as ctor:
        for method, args in cases:
            try:
                asyncio.run(getattr(make_client(), method)(*args))
            except ValueError:
                pass
            else:
                raise AssertionError(f"{method}{args}: expected ValueError")
        assert not ctor.called, "validation must happen before any HTTP call"


# ─── Error paths / retries ────────────────────────────────────


def test_401_no_retry() -> None:
    cm = make_cm(posts=[resp(401, err_envelope("unauthorized", "bad token"))])
    with mock.patch("httpx.AsyncClient", return_value=cm), no_sleep():
        try:
            asyncio.run(make_client().search_music("q"))
        except SearchError as e:
            assert e.code == "unauthorized"
            assert e.status == 401
            assert e.detail == "bad token"
        else:
            raise AssertionError("expected SearchError")
    assert cm.__aenter__.return_value.post.await_count == 1  # без ретраев


def test_422_with_extra() -> None:
    extra = {"fields": {"limit": "must be <= 25"}}
    cm = make_cm(posts=[resp(422, err_envelope("validation_error", "bad", extra))])
    with mock.patch("httpx.AsyncClient", return_value=cm), no_sleep():
        try:
            asyncio.run(make_client().search_music("q"))
        except SearchError as e:
            assert e.code == "validation_error"
            assert e.extra == extra
        else:
            raise AssertionError("expected SearchError")
    assert cm.__aenter__.return_value.post.await_count == 1


def test_429_retry_after_then_success() -> None:
    cm = make_cm(
        posts=[
            resp(429, err_envelope("rate_limit_exceeded", "slow down"),
                 headers={"Retry-After": "2"}),
            resp(200, MUSIC_OK),
        ]
    )
    sleep = mock.AsyncMock()
    with mock.patch("httpx.AsyncClient", return_value=cm), \
         mock.patch("anysave_sdk.search.asyncio.sleep", new=sleep):
        page = asyncio.run(make_client().search_music("q"))
    assert not page.is_empty
    assert cm.__aenter__.return_value.post.await_count == 2
    assert sleep.await_args.args[0] == 2.0  # honor Retry-After


def test_429_exhausted_sets_retry_after() -> None:
    cm = make_cm(
        posts=[
            resp(429, err_envelope("rate_limit_exceeded"), headers={"Retry-After": "1"}),
            resp(429, err_envelope("rate_limit_exceeded"), headers={"Retry-After": "1"}),
            resp(429, err_envelope("rate_limit_exceeded"), headers={"Retry-After": "1"}),
        ]
    )
    with mock.patch("httpx.AsyncClient", return_value=cm), no_sleep():
        try:
            asyncio.run(make_client().search_music("q"))
        except SearchError as e:
            assert e.code == "rate_limit_exceeded"
            assert e.status == 429
            assert e.retry_after == 1.0
        else:
            raise AssertionError("expected SearchError")
    # max_retries=2 → 3 попытки и всё
    assert cm.__aenter__.return_value.post.await_count == 3


def test_502_retries_then_search_error() -> None:
    cm = make_cm(
        posts=[
            resp(502, err_envelope("music_provider_failed", "deezer down")),
            resp(502, err_envelope("music_provider_failed", "deezer down")),
            resp(502, err_envelope("music_provider_failed", "deezer down")),
        ]
    )
    sleep = mock.AsyncMock()
    with mock.patch("httpx.AsyncClient", return_value=cm), \
         mock.patch("anysave_sdk.search.asyncio.sleep", new=sleep):
        try:
            asyncio.run(make_client().search_music("q"))
        except SearchError as e:
            assert e.code == "music_provider_failed"
            assert e.status == 502
        else:
            raise AssertionError("expected SearchError")
    assert cm.__aenter__.return_value.post.await_count == 3
    # exponential backoff: 1s, 2s
    assert [c.args[0] for c in sleep.await_args_list] == [1.0, 2.0]


def test_500_without_envelope_uses_fallback_code() -> None:
    cm = make_cm(posts=[resp(500, None)])
    with mock.patch("httpx.AsyncClient", return_value=cm), no_sleep():
        try:
            asyncio.run(make_client().search_music("q"))
        except SearchError as e:
            assert e.code == "internal_error"
            assert e.status == 500
        else:
            raise AssertionError("expected SearchError")
    assert cm.__aenter__.return_value.post.await_count == 1  # 500 не ретраится


def test_network_error_maps_to_search_error() -> None:
    cm = make_cm(posts=[httpx.ConnectError("dns down")])
    with mock.patch("httpx.AsyncClient", return_value=cm), no_sleep():
        try:
            asyncio.run(make_client().search_music("q"))
        except SearchError as e:
            assert e.code == "api_connect_error"
            assert e.status is None
        else:
            raise AssertionError("expected SearchError")


def test_bad_success_shape_maps_to_unknown_response() -> None:
    # results сломан типово — pydantic должен упасть → unknown_response
    bad = {"status": "ok", "ok": True, "data": {"results": "not-a-list"}}
    cm = make_cm(posts=[resp(200, bad)])
    with mock.patch("httpx.AsyncClient", return_value=cm), no_sleep():
        try:
            asyncio.run(make_client().search_music("q"))
        except SearchError as e:
            assert e.code == "unknown_response"
        else:
            raise AssertionError("expected SearchError")


# ─── Runner ───────────────────────────────────────────────────


if __name__ == "__main__":
    tests = [
        (name, fn)
        for name, fn in sorted(globals().items())
        if name.startswith("test_") and callable(fn)
    ]
    failed = 0
    for name, fn in tests:
        try:
            fn()
            print(f"  OK   {name}")
        except Exception as exc:  # noqa: BLE001
            failed += 1
            print(f"  FAIL {name}: {type(exc).__name__}: {exc}")
    print(f"\n{len(tests) - failed}/{len(tests)} passed")
    raise SystemExit(1 if failed else 0)



