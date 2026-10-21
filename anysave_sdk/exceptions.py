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


class ConcurrencyLimitTimeoutError(Exception):
    """Не удалось дождаться свободного слота локальной обработки."""

    def __init__(
        self,
        *,
        label: str,
        max_concurrency: int,
        timeout_s: float,
    ):
        self.label = label
        self.max_concurrency = max_concurrency
        self.timeout_s = timeout_s
        super().__init__(
            f"No free local processing slot for '{label}' within "
            f"{timeout_s:.1f}s (max_concurrency={max_concurrency})"
        )


class SearchError(Exception):
    """
    Ошибка Search API (``/search/*``).

    Бросается методами :meth:`AnySaveClient.search_music`,
    :meth:`AnySaveClient.search_youtube`, :meth:`AnySaveClient.search_chart`
    при не-2xx ответе сервера или сетевой ошибке.

    Attributes:
        code:        Машиночитаемый код. Берётся из серверного envelope
                     (``error.code``) либо это клиентский fallback для
                     нестандартных ответов (``http_500`` и т.п.).
                     Серверные коды: ``unauthorized``, ``subscription_required``,
                     ``access_forbidden``, ``insufficient_usage_tokens``,
                     ``validation_error``, ``rate_limit_exceeded``,
                     ``music_provider_failed``, ``youtube_search_failed``,
                     ``chart_unavailable``.
        detail:      Человекочитаемое описание (может быть ``None``).
        status:      HTTP-статус ответа (``None`` при сетевой ошибке).
        extra:       Доп. диагностические данные из envelope
                     (field-level ошибки при 422).
        retry_after: Секунды до сброса лимита — заголовок ``Retry-After``
                     при 429 (может быть ``None``, если заголовка нет).

    Example::

        from anysave_sdk import SearchError

        try:
            page = await anysave.search_music("The Weeknd")
        except SearchError as e:
            if e.code == "rate_limit_exceeded":
                print(f"Подождите {e.retry_after}s")
            elif e.code in {"access_forbidden", "subscription_required"}:
                print("Тариф не поддерживает этот эндпоинт")
            elif e.code == "insufficient_usage_tokens":
                print("Закончились usage-токены")
            else:
                raise
    """

    def __init__(
        self,
        *,
        code: str,
        detail: str | None = None,
        status: int | None = None,
        extra: dict | None = None,
        retry_after: float | None = None,
    ):
        self.code = code
        self.detail = detail
        self.status = status
        self.extra = extra
        self.retry_after = retry_after

        parts = [f"Search API error [{code}]"]
        if status is not None:
            parts.append(f"(HTTP {status})")
        if detail:
            parts.append(f"— {detail}")
        super().__init__(" ".join(parts))