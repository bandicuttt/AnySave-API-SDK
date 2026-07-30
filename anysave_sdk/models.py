# anysave_sdk/models.py

"""Pydantic-модели API-ответов и результатов скачивания."""

from enum import Enum

from pydantic import BaseModel


class DownloadStatus(str, Enum):
    """Статус результата скачивания."""
    SUCCESS = "success"
    ERROR = "error"
    PARTIAL = "partial"
    CACHED = "cached"


class ClientError(BaseModel):
    """
    Типизированная ошибка клиента.

    Attributes:
        code:   Машиночитаемый код из :class:`ClientErrorCode`.
                Например: ``"api_timeout"``, ``"task_timeout"``,
                ``"some_downloads_failed"``.
        detail: Человекочитаемое описание ошибки (может быть ``None``).
        status: HTTP-статус код, если ошибка связана с HTTP (401, 429, 500...).

    Example::

        if not result.ok:
            err = result.error
            if err.code == "api_unauthorized":
                print("Неверный токен!")
            elif err.code == "task_timeout":
                print(f"Таймаут: {err.detail}")
            else:
                print(f"Ошибка [{err.code}]: {err.detail}")
    """
    code: str
    detail: str | None = None
    status: int | None = None


class FileMediaMetadata(BaseModel):
    duration: float | None = None
    width: int | None = None
    height: int | None = None
    thumbnail: str | None = None


class ApiFileExtra(BaseModel):
    metadata: FileMediaMetadata | None = None


class DownloadedFileExtra(BaseModel):
    metadata: FileMediaMetadata | None = None


class DownloadedFile(BaseModel):
    """
    Один скачанный локальный файл.

    Attributes:
        type:            Тип файла: ``"video"``, ``"audio"``, ``"photo"``,
                         ``"animation"`` (GIF).
        path:            Абсолютный путь к файлу на диске.
        file_size_bytes: Размер файла в байтах (``None`` если не определён).
        is_truncated:    ``True`` если файл был обрезан из-за ``max_file_size_mb``.
        extra:           Медиа-метаданные (длительность, разрешение, thumbnail).

    Properties:
        is_visual:    Видео или фото.
        is_audio:     Аудиофайл.
        is_video:     Видео или анимация.
        is_container: Файл с контейнером (видео, аудио, анимация).

    Example::

        for f in result.files:
            if f.is_video:
                print(f"Видео: {f.path} ({f.file_size_bytes} байт)")
                if f.extra and f.extra.metadata:
                    m = f.extra.metadata
                    print(f"  Длительность: {m.duration}s, {m.width}x{m.height}")
                    print(f"  Thumbnail: {m.thumbnail}")
            elif f.is_audio:
                print(f"Аудио: {f.path}")
    """

    type: str
    path: str
    file_size_bytes: int | None = None
    is_truncated: bool = False
    extra: DownloadedFileExtra | None = None

    @property
    def is_visual(self) -> bool:
        return self.type in {"video", "animation", "photo"}

    @property
    def is_audio(self) -> bool:
        return self.type == "audio"

    @property
    def is_video(self) -> bool:
        return self.type in {"video", "animation"}

    @property
    def is_container(self) -> bool:
        return self.type in {"video", "audio", "animation"}


class CachedFileInfo(BaseModel):
    """Файл из Telegram cache."""
    type: str
    file_id: str
    file_unique_id: str
    message_id: int
    filename: str | None = None
    file_size_bytes: int | None = None
    duration: float | None = None
    width: int | None = None
    height: int | None = None
    thumbnail_file_id: str | None = None


class CachedMessageInfo(BaseModel):
    """Сообщение из Telegram cache."""
    message_id: int
    chat_id: int
    date: int
    media_group_id: str | None = None


class CachedDestinationInfo(BaseModel):
    """Telegram destination из cache."""
    destination_id: int
    chat_id: int
    name: str


class CachedRequestParams(BaseModel):
    """
    Параметры запроса, по которым был создан кэш.

    Соответствует серверному CacheLookupRequestParams:
    кэш идентифицируется только по url + mode + quality.
    """
    mode: str
    requested_quality: str | None = None
    effective_quality: str | None = None


class CachedResult(BaseModel):
    """
    Результат из Telegram-кэша.

    Содержит ``file_id`` файлов, уже загруженных в Telegram.
    Позволяет повторно отправить медиа без повторного скачивания
    и повторной загрузки в Telegram.

    Attributes:
        source_url:      Исходный URL запроса.
        normalized_url:  Нормализованный URL (без UTM и прочего мусора).
        service:         Платформа (``"youtube"``, ``"tiktok"`` и т.д.).
        is_truncated:    Файл был обрезан при исходном скачивании.
        response_status: Тип ответа (``"tunnel"`` или ``"picker"``).
        request_params:  Параметры исходного запроса (mode, quality и т.д.).
        cached_at:       ISO 8601 timestamp создания кэша.
        destination:     Telegram-канал/чат куда были отправлены файлы.
        files:           Список :class:`CachedFileInfo` с ``file_id``.
        messages:        Telegram-сообщения с этими файлами.

    Example::

        result = await anysave.smart_download(url)
        if result.is_cached:
            cache = result.cached
            print(f"Из кэша ({cache.service}): {len(cache.files)} файл(ов)")
            for f in cache.files:
                # Отправить напрямую через Telegram Bot API
                await bot.send_video(chat_id, video=f.file_id)
    """
    source_url: str
    normalized_url: str
    service: str
    is_truncated: bool = False
    response_status: str
    request_params: CachedRequestParams
    cached_at: str
    destination: CachedDestinationInfo
    files: list[CachedFileInfo]
    messages: list[CachedMessageInfo]


class TaskCreateResult(BaseModel):
    """Ответ на POST /downloader/tasks."""
    task_id: str
    status: str
    created_at: str
    status_url: str
    result_url: str


class TaskStatusResult(BaseModel):
    """Ответ на GET /downloader/tasks/{id}."""
    task_id: str
    status: str
    result_status: str | None = None
    created_at: str
    started_at: str | None = None
    finished_at: str | None = None
    expires_at: str
    poll_after_ms: int = 1000


class DownloadResult(BaseModel):
    """
    Итоговый результат скачивания.

    Attributes:
        status: Статус операции (:class:`DownloadStatus`).
        files:  Список скачанных локальных файлов (:class:`DownloadedFile`).
                Пустой при ``status=CACHED`` или ``status=ERROR``.
        error:  Описание ошибки при ``status=ERROR`` или ``status=PARTIAL``.
        cached: Данные из Telegram-кэша при ``status=CACHED``.

    Properties:
        ok:         ``True`` если есть хоть какой-то результат
                    (SUCCESS, PARTIAL или CACHED).
        is_cached:  ``True`` если результат из Telegram-кэша.
        error_msg:  Человекочитаемое сообщение об ошибке или ``None``.

    Example::

        result = await anysave.smart_download("https://youtu.be/...")
        if result.ok:
            for f in result.files:
                print(f.type, f.path, f.file_size_bytes)
        elif result.is_cached:
            print("Из кэша:", result.cached.files[0].file_id)
        else:
            print("Ошибка:", result.error_msg)
    """

    status: DownloadStatus | str
    files: list[DownloadedFile]
    error: ClientError | None = None
    cached: CachedResult | None = None

    @property
    def ok(self) -> bool:
        status_value = (
            self.status.value
            if isinstance(self.status, DownloadStatus)
            else str(self.status)
        )
        return status_value in {"success", "partial", "cached"} and (
            len(self.files) > 0 or self.cached is not None
        )

    @property
    def is_cached(self) -> bool:
        status_value = (
            self.status.value
            if isinstance(self.status, DownloadStatus)
            else str(self.status)
        )
        return status_value == "cached" and self.cached is not None

    @property
    def error_msg(self) -> str | None:
        if self.error is None:
            return None
        return self.error.detail or self.error.code or str(self.error)


class ApiFile(BaseModel):
    """Один файл из API-ответа."""
    type: str
    url: str
    filename: str
    file_size_bytes: int | None = None
    extra: ApiFileExtra | None = None


class ApiTunnelResponse(BaseModel):
    """API-ответ типа tunnel (один файл)."""
    status: str
    source_url: str
    service: str
    is_truncated: bool = False
    file: ApiFile


class ApiPickerResponse(BaseModel):
    """API-ответ типа picker (несколько файлов + опциональное аудио)."""
    status: str
    source_url: str
    service: str
    is_truncated: bool = False
    files: list[ApiFile]
    audio: ApiFile | None = None


class RemoteFile(BaseModel):
    """
    CDN-ссылка на файл из fast-link ответа сервера.

    В отличие от :class:`DownloadedFile` — нет локального ``path``,
    только ``url`` на CDN или HLS-манифест.

    Attributes:
        type:            ``"video"``, ``"audio"``, ``"photo"``, ``"animation"``.
        url:             Прямая CDN-ссылка или HLS/DASH-манифест.
        filename:        Рекомендованное имя файла.
        file_size_bytes: Размер файла (может быть ``None``).
        extra:           Метаданные (duration, width, height, thumbnail URL).

    Properties:
        is_hls_manifest: ``True`` если URL оканчивается на ``.m3u8`` или ``.mpd``.
                         Такой файл нельзя скачать простым GET — нужен ffmpeg.
        is_video:        Видео или анимация.
        is_audio:        Аудиофайл.
        is_image:        Фото.

    Example::

        remote = await anysave.get_remote_links("https://youtu.be/...")
        for f in remote.files:
            if f.is_hls_manifest:
                print(f"HLS: {f.url}")
            else:
                print(f"Direct: {f.url} ({f.filename})")
    """
    type: str
    url: str
    filename: str
    file_size_bytes: int | None = None
    extra: ApiFileExtra | None = None

    @property
    def is_hls_manifest(self) -> bool:
        """Является ли URL HLS/DASH манифестом."""
        path = self.url.split("?", 1)[0].lower()
        return path.endswith(".m3u8") or path.endswith(".mpd")

    @property
    def is_video(self) -> bool:
        return self.type in {"video", "animation"}

    @property
    def is_audio(self) -> bool:
        return self.type == "audio"

    @property
    def is_image(self) -> bool:
        return self.type in {"image", "photo"}


class RemoteDownloadResult(BaseModel):
    """
    Результат fast-link запроса: CDN-ссылки без локального скачивания.

    Attributes:
        status: ``"success"`` | ``"error"`` | ``"cached"``.
        files:  Список :class:`RemoteFile` (видео, фото, аудио).
        audio:  Отдельная аудиодорожка (при split video+audio, например YouTube).
                ``None`` если аудио встроено в видео или не запрашивалось.
        error:  Описание ошибки при ``status=ERROR``.
        cached: Данные Telegram-кэша при ``status=CACHED``.

    Example::

        remote = await anysave.get_remote_links(url, quality="1080")
        if not remote.ok:
            print("Ошибка:", remote.error_msg)
        elif remote.audio:
            print("Split: нужно скачать video + audio и смёржить")
        else:
            print("Единый файл:", remote.files[0].url)
    """
    status: DownloadStatus | str
    files: list[RemoteFile]
    audio: RemoteFile | None = None
    error: ClientError | None = None
    cached: CachedResult | None = None

    @property
    def ok(self) -> bool:
        status_value = (
            self.status.value
            if isinstance(self.status, DownloadStatus)
            else str(self.status)
        )
        return status_value in {"success", "cached"} and (
            len(self.files) > 0 or self.cached is not None
        )

    @property
    def is_cached(self) -> bool:
        status_value = (
            self.status.value
            if isinstance(self.status, DownloadStatus)
            else str(self.status)
        )
        return status_value == "cached" and self.cached is not None

    @property
    def error_msg(self) -> str | None:
        if self.error is None:
            return None
        return self.error.detail or self.error.code or str(self.error)