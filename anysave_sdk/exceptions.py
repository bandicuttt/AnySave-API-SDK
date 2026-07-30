# anysave_sdk/exceptions.py

"""Внутренние исключения клиента скачивания."""


class SlowDownloadError(Exception):
    """Скорость скачивания упала ниже допустимого минимума."""

    def __init__(self, bytes_downloaded: int, elapsed: float, speed: float):
        self.bytes_downloaded = bytes_downloaded
        self.elapsed = elapsed
        self.speed = speed
        super().__init__(
            f"Slow download: {bytes_downloaded} bytes in {elapsed:.1f}s ({speed:.0f} B/s)"
        )


class IncompleteDownloadError(Exception):
    """CDN оборвал body раньше, чем обещал в Content-Length."""

    def __init__(
        self,
        bytes_downloaded: int,
        expected_bytes: int | None,
        original: Exception,
    ):
        self.bytes_downloaded = bytes_downloaded
        self.expected_bytes = expected_bytes
        self.original = original
        super().__init__(
            f"Incomplete download: got={bytes_downloaded} expected={expected_bytes} "
            f"| {type(original).__name__}: {original}"
        )