# anysave_sdk/models.py

"""Pydantic-модели API-ответов и результатов скачивания."""

from enum import Enum
from urllib.parse import parse_qs, urlparse

from pydantic import BaseModel, field_validator


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


class ExtraProcessingHint(BaseModel):
    """
    Подсказка сервера о том, как обрабатывать файл (контракт extra.processing).

    Attributes:
        version: Версия контракта (сейчас ``1``). Незнакомые kind/version
                 клиент обязан игнорировать — обратная совместимость.
        kind:    Тип обработки. ``"hls_manifest"`` — URL это HLS-манифест,
                 заголовки обязательны и для вариант-плейлистов, и для сегментов.
        headers: Заголовки, которые нужно пробрасывать при скачивании
                 манифеста, плейлистов и сегментов (User-Agent, Referer...).
        base_url: Оригинальный URL плейлиста на CDN (например okcdn).
                  Если задан — относительные URI внутри плейлиста
                  (вариант-плейлисты, сегменты) резолвятся против него,
                  а не против URL, по которому манифест был получен
                  (это может быть relay-URL). Клиент скачивает манифест
                  сам, переписывает URI и качает сегменты напрямую с CDN.
        note:    Человекочитаемое пояснение от сервера.

    Example::

        {
            "extra": {
                "processing": {
                    "version": 1,
                    "kind": "hls_manifest",
                    "headers": {"User-Agent": "...", "Referer": "https://ok.ru/"},
                    "note": "HLS-манифест okcdn.ru"
                }
            }
        }
    """
    version: int = 1
    kind: str
    headers: dict[str, str] = {}
    base_url: str | None = None
    note: str | None = None

    @property
    def is_hls_manifest(self) -> bool:
        """Известный ли это hint, который клиент умеет обрабатывать."""
        return self.kind == "hls_manifest"


class ApiFileExtra(BaseModel):
    metadata: FileMediaMetadata | None = None
    processing: ExtraProcessingHint | None = None


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
        """
        Является ли URL HLS/DASH манифестом.

        ``True`` если URL оканчивается на ``.m3u8``/``.mpd`` ИЛИ сервер
        прислал ``extra.processing.kind == "hls_manifest"`` (парсер okru
        может отдать манифест с URL без расширения).
        """
        path = self.url.split("?", 1)[0].lower()
        if path.endswith(".m3u8") or path.endswith(".mpd"):
            return True
        processing = self.extra.processing if self.extra else None
        return processing is not None and processing.is_hls_manifest

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


# ─── Search API (/search/*) ──────────────────────────────────


class MusicSearchResult(BaseModel):
    """
    Трек из ``/search/music`` или ``/search/chart`` (универсальное представление).

    Источник данных определяется полем ``source``: ``"deezer"`` или ``"itunes"``.

    Attributes:
        id:               ID трека в исходной системе (Deezer / iTunes).
        title:            Название трека.
        artist:           Исполнитель.
        album:            Название альбома (может быть ``None``).
        duration_seconds: Длительность в СЕКУНДАХ (может быть дробной).
        cover_url_small:  Обложка ~50-60px.
        cover_url_medium: Обложка ~120-250px.
        cover_url_big:    Обложка ~500-1000px.
        preview_url:      30-секундный превью-URL (только Deezer,
                          для iTunes обычно ``None``).
        release_date:     Дата релиза в формате ISO 8601 (``YYYY-MM-DD``).
        isrc:             International Standard Recording Code
                          (в текущей версии API всегда ``None``).
        is_explicit:      Содержит ли трек explicit-контент —
                          фильтрация на стороне SDK/UI при необходимости.
        source:           ``"deezer"`` | ``"itunes"``.
        source_url:       Ссылка на трек в источнике.
        yt_query:         Готовый запрос для ``/search/youtube``
                          (обычно ``"{artist} - {title} official audio"``).
                          Можно передавать как есть или переопределить.

    Properties:
        is_deezer: ``True`` если трек пришёл из Deezer.
        is_itunes: ``True`` если трек пришёл из iTunes.

    Example::

        track = (await anysave.search_music("The Weeknd")).results[0]
        print(track.artist, track.title, track.duration_seconds)
        hits = await anysave.search_youtube(track.yt_query)
    """

    id: str
    title: str
    artist: str
    album: str | None = None
    duration_seconds: float | None = None
    cover_url_small: str | None = None
    cover_url_medium: str | None = None
    cover_url_big: str | None = None
    preview_url: str | None = None
    release_date: str | None = None
    isrc: str | None = None
    is_explicit: bool = False
    source: str = ""
    source_url: str | None = None
    yt_query: str = ""

    @field_validator("id", mode="before")
    @classmethod
    def _coerce_id_to_str(cls, v: object) -> str:
        # iTunes trackId может приходить как int — нормализуем в str.
        return str(v)

    @property
    def is_deezer(self) -> bool:
        return self.source == "deezer"

    @property
    def is_itunes(self) -> bool:
        return self.source == "itunes"


class MusicSearchResponse(BaseModel):
    """
    Ответ ``POST /search/music`` (и база для ``GET /search/chart``).

    Пустой ``results`` — это успешный ответ (200), а не ошибка.

    Attributes:
        results:       Список :class:`MusicSearchResult`.
        total_results: Всего результатов на сервере (больше, чем в одной странице).
        next_cursor:   Непрозрачный курсор следующей страницы.
                       ``None`` — страниц больше нет. Курсор нельзя парсить
                       и конструировать — только передавать как есть.
        query:         Эхо исходного запроса.

    Properties:
        has_more:  ``True`` если есть следующая страница.
        is_empty:  ``True`` если результатов нет.

    Example::

        page = await anysave.search_music("rock", limit=25)
        all_tracks = list(page.results)
        while page.next_cursor:
            page = await anysave.search_music("rock", limit=25, cursor=page.next_cursor)
            all_tracks.extend(page.results)
    """

    results: list[MusicSearchResult] = []
    total_results: int = 0
    next_cursor: str | None = None
    query: str = ""

    @property
    def has_more(self) -> bool:
        return self.next_cursor is not None

    @property
    def is_empty(self) -> bool:
        return len(self.results) == 0


class MusicChartResponse(MusicSearchResponse):
    """
    Ответ ``GET /search/chart`` — глобальный чарт Deezer.

    Структура совпадает с :class:`MusicSearchResponse`.
    ``source`` у всех треков всегда ``"deezer"`` (fallback на iTunes
    для чартов отсутствует — у iTunes нет публичного chart API).
    """


class YoutubeSearchHit(BaseModel):
    """
    Видео из ``/search/youtube``.

    Отдельная структура, НЕ содержит полей :class:`MusicSearchResult`.
    ``upload_date`` — в формате YouTube ``YYYYMMDD`` (без разделителей).

    Attributes:
        url:              Полный URL видео
                          (всегда ``https://www.youtube.com/watch?v=<ID>``).
        title:            Название видео.
        channel:          Название канала (может быть ``None``).
        duration_seconds: Длительность в СЕКУНДАХ.
        view_count:       Число просмотров.
        thumbnail_url:    Лучший доступный thumbnail.
        upload_date:      Дата загрузки в формате ``YYYYMMDD``.

    Properties:
        video_id: YouTube ID видео (парсится из ``url``).

    Example::

        hits = await anysave.search_youtube("how to tie a tie tutorial", limit=10)
        print(hits.results[0].url, hits.results[0].video_id)
    """

    url: str
    title: str
    channel: str | None = None
    duration_seconds: float | None = None
    view_count: int | None = None
    thumbnail_url: str | None = None
    upload_date: str | None = None

    @property
    def video_id(self) -> str | None:
        """YouTube ID видео, извлечённый из URL."""
        query = parse_qs(urlparse(self.url).query)
        values = query.get("v")
        return values[0] if values else None


class YoutubeSearchResponse(BaseModel):
    """
    Ответ ``POST /search/youtube``.

    В v1 пагинация НЕ поддерживается — ``next_cursor`` всегда ``None``.
    Пустой ``results`` — успешный ответ (200), а не ошибка.

    Attributes:
        results:     Список :class:`YoutubeSearchHit`.
        next_cursor: Всегда ``None`` в v1.
        query:       Эхо исходного запроса.

    Properties:
        has_more:  ``True`` если есть следующая страница (в v1 всегда ``False``).
        is_empty:  ``True`` если результатов нет.
    """

    results: list[YoutubeSearchHit] = []
    next_cursor: str | None = None
    query: str = ""

    @property
    def has_more(self) -> bool:
        return self.next_cursor is not None

    @property
    def is_empty(self) -> bool:
        return len(self.results) == 0


class TrackResult(BaseModel):
    """
    Единый результат :meth:`AnySaveClient.get_track` — метаданные + скачивание.

    Attributes:
        query:    Исходный запрос (для готового ``track`` — ``"{artist} - {title}"``).
        track:    Найденный трек (:class:`MusicSearchResult`) или ``None``,
                  если музыкальный поиск ничего не вернул.
        hit:      Выбранное YouTube-видео (:class:`YoutubeSearchHit`)
                  или ``None``, если кандидатов не нашлось.
        download: Итог скачивания (:class:`DownloadResult`). Заполнен всегда —
                  при пустом поиске это ``status=ERROR`` с кодом ``no_results``.

    Properties:
        ok:        ``True`` если трек найден и файлы скачаны.
        files:     Shortcut к ``download.files``.
        is_cached: ``True`` если результат из Telegram-кэша.
        error_msg: Человекочитаемое сообщение об ошибке или ``None``.

    Example::

        got = await anysave.get_track("The Weeknd Blinding Lights")
        if got.ok:
            print(got.track.artist, got.track.title)
            print(got.hit.url, got.files[0].path)
        else:
            print(got.error_msg)  # напр. код no_results
    """

    query: str = ""
    track: MusicSearchResult | None = None
    hit: YoutubeSearchHit | None = None
    download: DownloadResult

    @property
    def ok(self) -> bool:
        return self.track is not None and self.download.ok

    @property
    def files(self) -> list[DownloadedFile]:
        return self.download.files

    @property
    def is_cached(self) -> bool:
        return self.download.is_cached

    @property
    def error_msg(self) -> str | None:
        message = self.download.error_msg
        if message is not None:
            return message
        if self.track is None:
            return f"No music results for {self.query!r}"
        return None
