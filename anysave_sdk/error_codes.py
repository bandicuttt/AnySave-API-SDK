# anysave_sdk/error_codes.py

"""
Коды ошибок клиентского SDK.

Не зависит от серверных error_codes — у клиента своя семантика:
API-коммуникация, скачивание файлов, конфигурация.
"""

from enum import Enum


class ClientErrorCode(str, Enum):
    """
    Коды ошибок клиентского SDK.

    Не зависит от серверных error_codes — у клиента своя семантика.
    Все коды являются строками (``str``), совместимы с прямым сравнением::

        if result.error.code == "api_timeout":
            ...
        # или через enum:
        if result.error.code == ClientErrorCode.API_TIMEOUT:
            ...

    Группы кодов:

    **Конфигурация** — ошибки настройки клиента:

        NO_API_URL — ``api_base_url`` не задан при создании клиента.

    **API-коммуникация** — ошибки HTTP-запросов к серверу:

        API_CONNECT_TIMEOUT — сервер не ответил на подключение за отведённое время.
        API_READ_TIMEOUT    — сервер принял запрос, но не вернул ответ вовремя.
                              При ``download()`` означает что сервер ещё качает.
        API_CONNECT_ERROR   — не удалось подключиться (DNS, firewall, сервер упал).
        API_HTTP_ERROR      — сервер вернул 4xx/5xx (кроме 401/429).
        API_TIMEOUT         — общий таймаут (обёртка для нестандартных случаев).
        API_REQUEST_FAILED  — запрос упал по непредвиденной причине.
        API_UNAUTHORIZED    — токен неверный или не передан (HTTP 401).
        API_RATE_LIMITED    — превышен лимит запросов (HTTP 429).
        SERVER_BUSY         — сервер перегружен, нет свободных слотов.

    **Скачивание файлов** — ошибки локального скачивания:

        UNKNOWN_RESPONSE        — сервер вернул неизвестный формат ответа.
        SOME_DOWNLOADS_FAILED   — часть файлов из carousel не скачалась.

    **Кэш** — ошибки Telegram-кэша:

        CACHE_LOOKUP_FAILED — запрос к кэшу упал (не критично, flow продолжается).

    **Task pipeline** — ошибки фонового скачивания:

        TASK_CREATE_FAILED  — не удалось создать задачу (POST /downloader/tasks).
        TASK_POLL_FAILED    — ошибка опроса статуса задачи.
        TASK_RESULT_FAILED  — ошибка получения результата задачи.
        TASK_TIMEOUT        — задача не завершилась за ``task_max_poll_time_s``.
        TASK_DOWNLOAD_ERROR — задача завершилась, но скачивание файла упало.
    """

    # ─── Конфигурация ────────────────────────────────────────
    NO_API_URL = "no_api_url"

    # ─── API communication ───────────────────────────────────
    API_CONNECT_TIMEOUT = "api_connect_timeout"
    API_READ_TIMEOUT = "api_read_timeout"
    API_CONNECT_ERROR = "api_connect_error"
    API_HTTP_ERROR = "api_http_error"
    API_TIMEOUT = "api_timeout"
    API_REQUEST_FAILED = "api_request_failed"
    API_UNAUTHORIZED = "api_unauthorized"
    API_RATE_LIMITED = "api_rate_limited"
    SERVER_BUSY = "server_busy"

    # ─── Download ────────────────────────────────────────────
    UNKNOWN_RESPONSE = "unknown_response"
    SOME_DOWNLOADS_FAILED = "some_downloads_failed"
    
    # ─── Cache lookup ────────────────────────────────────────
    CACHE_LOOKUP_FAILED = "cache_lookup_failed"

    # ─── Task pipeline ───────────────────────────────────────
    TASK_CREATE_FAILED = "task_create_failed"
    TASK_POLL_FAILED = "task_poll_failed"
    TASK_RESULT_FAILED = "task_result_failed"
    TASK_TIMEOUT = "task_timeout"
    TASK_DOWNLOAD_ERROR = "task_download_error"