"""
Тесты контракта extra.processing (hints от сервера, напр. парсер okru).

Запуск:
    venv/bin/python tests/test_extra_processing.py
    venv/bin/python -m pytest tests/test_extra_processing.py -q

Покрытие:
- парсинг extra.processing из API-ответа (tunnel / picker / links),
- RemoteFile.is_hls_manifest по kind=hls_manifest (URL без .m3u8),
- build_download_headers: мердж серверных заголовков поверх builtin,
- незнакомый kind → заголовки не применяются,
- версия != 1 → заголовки применяются с warning (best-effort),
- _download_hls_file пробрасывает серверные заголовки в download_hls_stream,
- ответ без extra / без processing работает как раньше.
"""

from __future__ import annotations

import asyncio
import sys
import traceback
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from anysave_sdk.download_pipeline import DownloadPipeline  # noqa: E402
from anysave_sdk.headers import build_download_headers  # noqa: E402
from anysave_sdk.models import (  # noqa: E402
    ApiFile,
    ApiFileExtra,
    ExtraProcessingHint,
    RemoteFile,
)

OKRU_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) Test/1.0",
    "Referer": "https://ok.ru/",
    "Accept": "*/*",
}


def make_okru_api_file(url: str = "https://vdok.mycdn.me/getVideo?sig=x") -> ApiFile:
    return ApiFile(
        type="video",
        url=url,
        filename="okru_15911462046351.m3u8",
        extra=ApiFileExtra(
            processing=ExtraProcessingHint(
                version=1,
                kind="hls_manifest",
                headers=dict(OKRU_HEADERS),
                note="Заголовки обязательны для плейлистов и сегментов",
            )
        ),
    )


def make_pipeline(tmp: Path) -> DownloadPipeline:
    (tmp / "dl").mkdir(parents=True, exist_ok=True)
    return DownloadPipeline(
        download_dir=tmp / "dl",
        chunk_size=64,
        max_concurrency=2,
        download_timeout_s=10.0,
        min_speed_bytes_per_sec=0,
        download_retry_attempts=1,
        thumbnail_placeholder=None,
    )


def test_parse_tunnel_with_processing():
    """tunnel-ответ с extra.processing парсится, hint доезжает до ApiFile."""
    from anysave_sdk.api import extract_files

    data = {
        "status": "tunnel",
        "source_url": "https://ok.ru/video/15911462046351",
        "service": "okru",
        "is_truncated": False,
        "file": {
            "type": "video",
            "url": "https://vdok.mycdn.me/getVideo?sig=x",
            "filename": "okru_15911462046351.m3u8",
            "extra": {
                "metadata": {"duration": 100.5},
                "processing": {
                    "version": 1,
                    "kind": "hls_manifest",
                    "headers": OKRU_HEADERS,
                    "note": "HLS-манифест okcdn.ru",
                },
            },
        },
    }
    extracted = extract_files(data)
    assert extracted is not None
    files, audio, _ = extracted
    assert audio is None and len(files) == 1
    f = files[0]
    assert f.extra is not None
    p = f.extra.processing
    assert p is not None
    assert p.version == 1
    assert p.kind == "hls_manifest"
    assert p.headers == OKRU_HEADERS
    assert p.is_hls_manifest


def test_parse_picker_with_processing():
    """picker-ответ: processing доезжает до files."""
    from anysave_sdk.api import extract_files

    hint = {"version": 1, "kind": "hls_manifest", "headers": OKRU_HEADERS}
    data = {
        "status": "picker",
        "source_url": "https://ok.ru/video/1",
        "service": "okru",
        "is_truncated": False,
        "files": [{
            "type": "video",
            "url": "https://vdok.mycdn.me/getVideo?sig=x",
            "filename": "okru_x.m3u8",
            "extra": {"processing": hint},
        }],
        "audio": None,
    }
    extracted = extract_files(data)
    assert extracted is not None
    files, _, _ = extracted
    p = files[0].extra.processing if files[0].extra else None
    assert p is not None and p.kind == "hls_manifest"
    assert p.headers == OKRU_HEADERS


def test_links_response_extra_passes_through_pipeline():
    """parse_links_response сохраняет extra.processing в RemoteFile."""
    import tempfile

    with tempfile.TemporaryDirectory() as tmp:
        pipeline = make_pipeline(Path(tmp))
        result = pipeline.parse_links_response(_tunnel_data())

    assert result.ok
    rf = result.files[0]
    p = rf.extra.processing if rf.extra else None
    assert p is not None and p.headers == OKRU_HEADERS


def _tunnel_data() -> dict:
    return {
        "status": "tunnel",
        "source_url": "https://ok.ru/video/15911462046351",
        "service": "okru",
        "is_truncated": False,
        "file": {
            "type": "video",
            "url": "https://vdok.mycdn.me/getVideo?sig=x",
            "filename": "okru_x.m3u8",
            "extra": {
                "processing": {
                    "version": 1,
                    "kind": "hls_manifest",
                    "headers": OKRU_HEADERS,
                },
            },
        },
    }



def test_hls_download_receives_server_headers():
    """_download_hls_file пробрасывает серверные заголовки в download_hls_stream."""
    import tempfile

    tmp_path = Path(tempfile.mkdtemp())
    pipeline = make_pipeline(tmp_path)
    rf = RemoteFile(
        type="video",
        url="https://vdok.mycdn.me/getVideo?sig=x",
        filename="okru_x.m3u8",
        extra=ApiFileExtra(
            processing=ExtraProcessingHint(
                version=1, kind="hls_manifest", headers=dict(OKRU_HEADERS)
            )
        ),
    )

    captured: dict = {}

    async def fake_hls(manifest_url, output_path, timeout_seconds=900.0, headers=None):
        captured["url"] = manifest_url
        captured["headers"] = headers
        output_path.write_bytes(b"\x00" * 2048)
        return True

    async def fake_enrich(**kwargs):
        return None

    with (
        mock.patch("anysave_sdk.download_pipeline.download_hls_stream", new=fake_hls),
        mock.patch("anysave_sdk.download_pipeline.enrich_media_metadata", new=fake_enrich),
    ):
        got = asyncio.run(pipeline._download_hls_file(rf, tmp_path / "out.mp4", None))

    assert got is not None
    assert captured["url"] == rf.url
    assert captured["headers"] is not None
    assert captured["headers"]["User-Agent"] == OKRU_HEADERS["User-Agent"]
    assert captured["headers"]["Referer"] == "https://ok.ru/"


def test_direct_download_uses_processing_headers_for_hls_route():
    """Файл с kind=hls_manifest уходит по HLS-ветке даже без .m3u8 в URL."""
    import tempfile

    tmp_path = Path(tempfile.mkdtemp())
    pipeline = make_pipeline(tmp_path)
    rf = RemoteFile(
        type="video",
        url="https://vdok.mycdn.me/getVideo?sig=x",
        filename="okru_x.m3u8",
        extra=ApiFileExtra(
            processing=ExtraProcessingHint(
                version=1, kind="hls_manifest", headers=dict(OKRU_HEADERS)
            )
        ),
    )

    called = {"hls": False}

    async def fake_hls(manifest_url, output_path, timeout_seconds=900.0, headers=None):
        called["hls"] = True
        output_path.write_bytes(b"\x00" * 2048)
        return True

    async def fake_direct(session, file, dest, max_size):
        raise AssertionError("direct download must not be used for kind=hls_manifest")

    async def fake_enrich(**kwargs):
        return None

    with (
        mock.patch("anysave_sdk.download_pipeline.download_hls_stream", new=fake_hls),
        mock.patch("anysave_sdk.download_pipeline.enrich_media_metadata", new=fake_enrich),
        mock.patch.object(pipeline, "_download_direct_file", new=fake_direct),
    ):
        got = asyncio.run(pipeline._download_remote_file(None, rf, None))  # type: ignore[arg-type]

    assert got is not None
    assert called["hls"] is True



def test_is_hls_manifest_by_kind_without_url_extension():
    """kind=hls_manifest делает файл HLS даже если URL без .m3u8."""
    rf = RemoteFile(
        type="video",
        url="https://vdok.mycdn.me/getVideo?sig=x",
        filename="okru_x.m3u8",
        extra=ApiFileExtra(
            processing=ExtraProcessingHint(kind="hls_manifest", headers=OKRU_HEADERS)
        ),
    )
    assert rf.is_hls_manifest

    plain = RemoteFile(
        type="video",
        url="https://vdok.mycdn.me/getVideo?sig=x",
        filename="okru_x.m3u8",
    )
    assert not plain.is_hls_manifest


def test_build_headers_applies_server_headers():
    """Серверные заголовки из extra.processing попадают в build_download_headers."""
    headers = build_download_headers(make_okru_api_file())
    assert headers["User-Agent"] == OKRU_HEADERS["User-Agent"]
    assert headers["Referer"] == "https://ok.ru/"
    assert headers["Accept"] == "*/*"


def test_build_headers_server_overrides_builtin():
    """Серверные заголовки приоритетнее builtin (TikTok-правило перебивается)."""
    f = ApiFile(
        type="video",
        url="https://tiktokcdn.com/video/x.mp4",
        filename="x.m3u8",
        extra=ApiFileExtra(
            processing=ExtraProcessingHint(
                kind="hls_manifest",
                headers={"User-Agent": "OkRuClient/1.0", "Referer": "https://ok.ru/"},
            )
        ),
    )
    headers = build_download_headers(f)
    assert headers["User-Agent"] == "OkRuClient/1.0"
    assert headers["Referer"] == "https://ok.ru/"


def test_build_headers_unknown_kind_ignored():
    """Незнакомый kind — hint игнорируется полностью (обратная совместимость)."""
    f = ApiFile(
        type="video",
        url="https://cdn.example.com/x.mp4",
        filename="x.mp4",
        extra=ApiFileExtra(
            processing=ExtraProcessingHint(
                version=1,
                kind="requires_merge",
                headers={"User-Agent": "MUST-NOT-APPLY"},
            )
        ),
    )
    assert "User-Agent" not in build_download_headers(f)


def test_build_headers_unknown_version_best_effort():
    """version != 1 при знакомом kind — заголовки применяются (best-effort)."""
    f = ApiFile(
        type="video",
        url="https://cdn.example.com/x.m3u8",
        filename="x.m3u8",
        extra=ApiFileExtra(
            processing=ExtraProcessingHint(
                version=2, kind="hls_manifest", headers={"Referer": "https://ok.ru/"}
            )
        ),
    )
    assert build_download_headers(f).get("Referer") == "https://ok.ru/"


def test_build_headers_no_extra_no_processing():
    """Без extra / без processing — заголовки пустые, как раньше."""
    assert build_download_headers(ApiFile(type="video", url="u", filename="f")) == {}
    f = make_okru_api_file()
    assert f.extra is not None
    f.extra.processing = None
    assert build_download_headers(f) == {}


# ─── extra.processing.base_url (okru: relay манифест, сегменты на CDN) ────

BASE_URL = "https://vd679.okcdn.ru/expires/20261023/some-sign/video.m3u8?sig=abc"

RAW_MANIFEST = (
    "#EXTM3U\n"
    "#EXT-X-STREAM-INF:BANDWIDTH=2000000,RESOLUTION=1280x720\n"
    "/expires/20261023/some-sign/HIGH/video.m3u8?sig=h\n"
    "#EXT-X-STREAM-INF:BANDWIDTH=800000,RESOLUTION=640x360\n"
    "/expires/20261023/some-sign/LOW/video.m3u8?sig=l\n"
    "#EXT-X-KEY:METHOD=AES-128,URI=\"/key.bin\",IV=0x1\n"
    "#EXT-X-KEY:METHOD=NONE\n"
    "#EXT-X-MAP:URI=\"init.mp4\"\n"
    "#EXTINF:6.006,\n"
    "HIGH00000.ts\n"
    "#EXTINF:6.006,\n"
    "https://other-cdn.example.com/abs.ts\n"
)


def test_parse_base_url():
    """base_url парсится из extra.processing."""
    f = ApiFile(
        type="video",
        url="https://relay.example.com/manifest.m3u8",
        filename="okru_x.m3u8",
        extra=ApiFileExtra(
            processing=ExtraProcessingHint(
                version=1,
                kind="hls_manifest",
                headers=dict(OKRU_HEADERS),
                base_url=BASE_URL,
            )
        ),
    )
    assert f.extra is not None
    p = f.extra.processing
    assert p is not None and p.base_url == BASE_URL


def test_rewrite_playlist_resolves_relative_uris():
    """Относительные URI (варианты, сегменты, атрибуты) резолвятся против base_url."""
    from anysave_sdk.postprocessing import rewrite_hls_playlist

    out = rewrite_hls_playlist(RAW_MANIFEST, BASE_URL)

    # вариант-плейлисты — против base_url, НЕ против relay-URL
    # ("/expires/..." — абсолютный путь от корня okcdn-хоста)
    assert "https://vd679.okcdn.ru/expires/20261023/some-sign/HIGH/video.m3u8?sig=h" in out.splitlines()
    assert "https://vd679.okcdn.ru/expires/20261023/some-sign/LOW/video.m3u8?sig=l" in out.splitlines()
    # сегмент относительный — в той же директории, что и base_url
    assert "https://vd679.okcdn.ru/expires/20261023/some-sign/HIGH00000.ts" in out.splitlines()
    # абсолютный сегмент не тронут
    assert "https://other-cdn.example.com/abs.ts" in out
    # EXT-X-KEY и EXT-X-MAP URI перезаписаны (URI="/key.bin" — от корня хоста)
    assert 'URI="https://vd679.okcdn.ru/key.bin"' in out
    assert f'URI="{BASE_URL.rsplit("/", 1)[0]}/init.mp4"' in out
    # директивы не пострадали
    assert out.splitlines()[0] == "#EXTM3U"
    assert "#EXTINF:6.006," in out
    assert "#EXT-X-KEY:METHOD=NONE" in out


def test_rewrite_playlist_data_uri_untouched():
    """data: URI (напр. ключ в base64) не резолвится."""
    from anysave_sdk.postprocessing import rewrite_hls_playlist

    manifest = '#EXT-X-KEY:METHOD=AES-128,URI="data:text/plain;base64,AAAA",IV=0x1\n'
    out = rewrite_hls_playlist(manifest, BASE_URL)
    assert 'URI="data:text/plain;base64,AAAA"' in out


def _make_rf_with_base_url(url: str = "https://relay.example.com/manifest.m3u8") -> RemoteFile:
    return RemoteFile(
        type="video",
        url=url,
        filename="okru_x.m3u8",
        extra=ApiFileExtra(
            processing=ExtraProcessingHint(
                version=1,
                kind="hls_manifest",
                headers=dict(OKRU_HEADERS),
                base_url=BASE_URL,
            )
        ),
    )


def test_hls_download_uses_cdn_segments_with_base_url():
    """С base_url: сегменты качает SDK (download_hls_via_cdn), ffmpeg не нужен."""
    import tempfile

    tmp_path = Path(tempfile.mkdtemp())
    pipeline = make_pipeline(tmp_path)
    rf = _make_rf_with_base_url()

    captured: dict = {}

    async def fake_cdn(manifest_url, base_url, headers, output_path,
                       timeout_seconds=900.0, max_concurrent=4):
        captured["manifest_url"] = manifest_url
        captured["base_url"] = base_url
        captured["headers"] = headers
        captured["output_path"] = Path(output_path)
        output_path.write_bytes(b"\x00" * 2048)
        return True

    async def fake_hls(*args, **kwargs):
        raise AssertionError("direct ffmpeg path must not be used when via_cdn succeeds")

    async def fake_enrich(**kwargs):
        return None

    with (
        mock.patch("anysave_sdk.download_pipeline.download_hls_via_cdn", new=fake_cdn),
        mock.patch("anysave_sdk.download_pipeline.download_hls_stream", new=fake_hls),
        mock.patch("anysave_sdk.download_pipeline.enrich_media_metadata", new=fake_enrich),
    ):
        got = asyncio.run(pipeline._download_hls_file(rf, tmp_path / "out.mp4", None))

    assert got is not None
    assert captured["manifest_url"] == rf.url
    assert captured["base_url"] == BASE_URL
    assert captured["headers"]["User-Agent"] == OKRU_HEADERS["User-Agent"]
    assert captured["headers"]["Referer"] == "https://ok.ru/"
    assert captured["output_path"] == tmp_path / "out.mp4"


def test_hls_download_base_url_fallback_when_fetch_fails():
    """Если манифест не скачался — fallback на прямой ffmpeg по relay-URL."""
    import tempfile

    tmp_path = Path(tempfile.mkdtemp())
    pipeline = make_pipeline(tmp_path)
    rf = _make_rf_with_base_url()

    captured: dict = {}

    async def fake_fetch(url, headers=None, timeout_seconds=60.0):
        return None

    async def fake_cdn(manifest_url, base_url, headers, output_path,
                       timeout_seconds=900.0, max_concurrent=4):
        return False  # манифест не скачался

    async def fake_hls(manifest_url, output_path, timeout_seconds=900.0, headers=None):
        captured["ffmpeg_input"] = manifest_url
        output_path.write_bytes(b"\x00" * 2048)
        return True

    async def fake_enrich(**kwargs):
        return None

    with (
        mock.patch("anysave_sdk.download_pipeline.download_hls_via_cdn", new=fake_cdn),
        mock.patch("anysave_sdk.download_pipeline.download_hls_stream", new=fake_hls),
        mock.patch("anysave_sdk.download_pipeline.enrich_media_metadata", new=fake_enrich),
    ):
        got = asyncio.run(pipeline._download_hls_file(rf, tmp_path / "out.mp4", None))

    assert got is not None
    assert captured["ffmpeg_input"] == rf.url  # старый путь


def test_hls_download_without_base_url_unchanged():
    """Без base_url — старое поведение: ffmpeg качает по relay-URL напрямую."""
    import tempfile

    tmp_path = Path(tempfile.mkdtemp())
    pipeline = make_pipeline(tmp_path)
    rf = RemoteFile(
        type="video",
        url="https://relay.example.com/manifest.m3u8",
        filename="okru_x.m3u8",
        extra=ApiFileExtra(
            processing=ExtraProcessingHint(
                version=1, kind="hls_manifest", headers=dict(OKRU_HEADERS)
            )
        ),
    )

    captured: dict = {}

    async def fake_hls(manifest_url, output_path, timeout_seconds=900.0, headers=None):
        captured["ffmpeg_input"] = manifest_url
        output_path.write_bytes(b"\x00" * 2048)
        return True

    async def fake_enrich(**kwargs):
        return None

    with (
        mock.patch("anysave_sdk.download_pipeline.download_hls_stream", new=fake_hls),
        mock.patch("anysave_sdk.download_pipeline.enrich_media_metadata", new=fake_enrich),
    ):
        got = asyncio.run(pipeline._download_hls_file(rf, tmp_path / "out.mp4", None))

    assert got is not None
    assert captured["ffmpeg_input"] == rf.url


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
