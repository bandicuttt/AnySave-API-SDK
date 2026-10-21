"""
AnySave API SDK — async Python client for downloading media.

Quick start::

    from anysave_sdk import AnySaveClient

    anysave = AnySaveClient(
        api_base_url="https://api.example.com",
        api_token="YOUR_TOKEN",
        download_dir="./downloads",
    )

    result = await anysave.smart_download("https://youtu.be/dQw4w9WgXcQ")
    if result.ok:
        for f in result.files:
            print(f.type, f.path)

Full configuration::

    anysave = AnySaveClient(
        api_base_url="https://api.example.com",
        api_token="YOUR_TOKEN",
        download_dir="./downloads",
        max_concurrency=2,
        api_timeout_s=600.0,
        download_timeout_s=300.0,
        min_speed_bytes_per_sec=50 * 1024,
        download_retry_attempts=5,
        thumbnail_placeholder="assets/thumb.jpg",
        prefer_telegram_cache=True,
        cache_lookup_timeout_s=5.0,
        task_poll_interval_s=2.0,
        task_max_poll_time_s=600.0,
        task_create_timeout_s=15.0,
        search_timeout_s=60.0,
        search_max_retries=2,
    )
"""

from anysave_sdk.download_pipeline import DownloadPipeline
from anysave_sdk.downloader import AnySaveClient
from anysave_sdk.error_codes import ClientErrorCode
from anysave_sdk.exceptions import SearchError
from anysave_sdk.models import (
    CachedDestinationInfo,
    CachedFileInfo,
    CachedMessageInfo,
    CachedRequestParams,
    CachedResult,
    ClientError,
    DownloadedFile,
    DownloadResult,
    DownloadStatus,
    MusicChartResponse,
    MusicSearchResponse,
    MusicSearchResult,
    RemoteDownloadResult,
    RemoteFile,
    TaskCreateResult,
    TaskStatusResult,
    TrackResult,
    YoutubeSearchHit,
    YoutubeSearchResponse,
)
from anysave_sdk.utils import fmt_duration, fmt_size

__all__ = [
    "AnySaveClient",
    "CachedDestinationInfo",
    "CachedFileInfo",
    "CachedMessageInfo",
    "CachedRequestParams",
    "CachedResult",
    "ClientError",
    "ClientErrorCode",
    "DownloadedFile",
    "DownloadResult",
    "DownloadStatus",
    "MusicChartResponse",
    "MusicSearchResponse",
    "MusicSearchResult",
    "RemoteDownloadResult",
    "RemoteFile",
    "SearchError",
    "TaskCreateResult",
    "TaskStatusResult",
    "TrackResult",
    "YoutubeSearchHit",
    "YoutubeSearchResponse",
    "DownloadPipeline",
    "fmt_duration",
    "fmt_size",
]