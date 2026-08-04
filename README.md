# AnySave API SDK

<div align="center">

**Async Python SDK для скачивания медиа из YouTube, TikTok, Instagram, Twitter/X и 50+ платформ**

[![Python](https://img.shields.io/badge/python-3.10+-blue.svg)](https://www.python.org/downloads/)
[![License](https://img.shields.io/badge/license-MIT-blue.svg)](LICENSE)
[![Typed](https://img.shields.io/badge/typing-py.typed-green.svg)](py.typed)
[![Website](https://img.shields.io/badge/website-anysave.click-0087eb.svg)](https://anysave.click)
[![API Docs](https://img.shields.io/badge/docs-api-blue.svg)](https://anysave.click/docs)

[Website](https://anysave.click) • [API Docs](https://anysave.click/docs) • [Playground](https://anysave.click/docs/playground) • [Get API key](https://anysave.click)

[Установка](#установка) • [Быстрый старт](#быстрый-старт) • [Методы](#методы) • [Примеры](#примеры) • [Конфигурация](#конфигурация) • [Обработка ошибок](#обработка-ошибок)

</div>

---

## Зачем

- **Один метод** — `smart_download()` выбирает оптимальную стратегию автоматически
- **Auto-fallback** — fast-link → server-side download, без вашего участия
- **Защита процесса** — глобальный limiter ограничивает число одновременно выполняемых local `/links` задач
- **Telegram-ready** — cache lookup, truncate под 50 MB, thumbnails
- **HLS / merge / resume** — SDK берёт на себя ffmpeg, split video+audio, дозагрузку
- **Typed** — полная поддержка mypy / pyright

```python
result = await anysave.smart_download("https://youtu.be/dQw4w9WgXcQ")
# → DownloadResult(status=SUCCESS, files=[DownloadedFile(type='video', path='./downloads/video.mp4')])
```

---

## Установка

```bash
python -m pip install -e .
```

С поддержкой Telegram-ботов:

```bash
python -m pip install -e ".[aiogram3]"  # aiogram 3 (рекомендуется)
python -m pip install -e ".[aiogram2]"  # aiogram 2 (legacy)
```

**Системные зависимости** (для HLS, merge, truncate, thumbnails):

```bash
# Ubuntu / Debian
apt install ffmpeg

# macOS
brew install ffmpeg
```

> Без ffmpeg SDK работает только с прямыми HTTP-ссылками.

---

## Быстрый старт

> Получить бесплатный API токен — [anysave.click](https://anysave.click) (1000 кредитов на старте, без карты).

```python
import asyncio
from anysave_sdk import AnySaveClient

async def main():
    anysave = AnySaveClient(
        api_base_url="https://api.anysave.click",
        api_token="YOUR_TOKEN",
        download_dir="./downloads",
    )

    result = await anysave.smart_download("https://youtu.be/dQw4w9WgXcQ")

    if result.ok:
        for f in result.files:
            print(f"{f.type}: {f.path}")
    elif result.is_cached:
        for f in result.cached.files:
            print(f"Telegram file_id: {f.file_id}")
    else:
        print("Ошибка:", result.error_msg)

asyncio.run(main())
```
> Полная документация API-эндпоинтов и стоимость в кредитах — [anysave.click/docs](https://anysave.click/docs).
> Протестировать любой эндпоинт в браузере — [Playground](https://anysave.click/docs/playground).

---

## Методы

| Метод | Когда использовать | API endpoint |
|---|---|---|
| `smart_download()` | **Рекомендуется.** Auto-fallback: fast-link → task pipeline | `/links` + `/tasks` |
| `download_links()` | Только fast-link. SDK скачивает и обрабатывает CDN-файлы локально | `POST /downloader/links` |
| `download_via_tasks()` | Явный task pipeline для долгих / тяжёлых задач | `POST /downloader/tasks` + polling |
| `download()` | Sync endpoint. Сервер скачивает и обрабатывает файл | `POST /downloader/` |
| `get_remote_links()` | CDN-ссылки без скачивания | `POST /downloader/links` |

### `smart_download()`

```python
result = await anysave.smart_download(
    url="https://www.youtube.com/watch?v=dQw4w9WgXcQ",
    mode="auto",            # "auto" | "audio" | "video"
    quality="1080",         # "max" | "2160" | "1440" | "1080" | "720" | "480" | "360"
    use_cookies=False,      # серверные cookies для приватного контента
    max_file_size_mb=50,    # обрезать если > 50 MB (лимит Telegram Bot API)
)

if result.ok:
    f = result.files[0]
    print(f.path)           # ./downloads/video.mp4
    print(f.type)           # "video" | "audio" | "photo" | "animation"
    print(f.file_size_bytes)
    print(f.is_truncated)   # True если файл был обрезан
    if f.extra and f.extra.metadata:
        m = f.extra.metadata
        print(m.duration, m.width, m.height, m.thumbnail)

elif result.is_cached:
    for f in result.cached.files:
        print(f.file_id)    # для bot.send_video(file_id)

else:
    print(result.error_msg)
```

### `get_remote_links()`

```python
remote = await anysave.get_remote_links("https://youtu.be/dQw4w9WgXcQ")

if remote.ok:
    for f in remote.files:
        print(f.url)              # CDN-ссылка
        print(f.is_hls_manifest)  # True → нужен ffmpeg
    if remote.audio:
        print(remote.audio.url)   # split аудиодорожка (YouTube)
```

### `download_via_tasks()`

```python
result = await anysave.download_via_tasks(
    url="https://www.youtube.com/watch?v=dQw4w9WgXcQ",
    quality="2160",
    max_file_size_mb=500,
)
# SDK создаёт задачу → опрашивает статус → возвращает результат
```

---

## Примеры

Готовые примеры в [`examples/`](examples/):

| Файл | Описание |
|---|---|
| [`quick_start.py`](examples/quick_start.py) | Минимальный скрипт — 20 строк, без Telegram |
| [`aiogram3_basic.py`](examples/aiogram3_basic.py) | Бот на aiogram 3 с `smart_download()` |
| [`aiogram3_cached.py`](examples/aiogram3_cached.py) | Бот с Telegram cache (`file_id` повторно) |
| [`aiogram3_links_only.py`](examples/aiogram3_links_only.py) | Бот с CDN-ссылками (без disk space) |
| [`aiogram3_tasks.py`](examples/aiogram3_tasks.py) | Бот с task pipeline (YouTube 4K) |
| [`aiogram2_basic.py`](examples/aiogram2_basic.py) | Legacy: бот на aiogram 2 |

---

## Поддерживаемые платформы

| Платформа | Видео | Аудио | Фото | Карусель |
|---|---|---|---|---|
| YouTube | ✅ | ✅ | — | — |
| TikTok | ✅ | ✅ | ✅ | ✅ |
| Instagram | ✅ | — | ✅ | ✅ |
| Twitter / X | ✅ | — | ✅ | ✅ |
| VK | ✅ | ✅ | ✅ | — |
| Facebook | ✅ | — | — | — |
| Reddit | ✅ | — | ✅ | ✅ |
| Twitch | ✅ | — | — | — |
| SoundCloud | — | ✅ | — | — |
| Pinterest | ✅ | — | ✅ | ✅ |
| Likee | ✅ | — | — | — |
| Rutube | ✅ | — | — | — |
| OK.ru | ✅ | — | — | — |
| Snapchat | ✅ | — | ✅ | — |

---

## Конфигурация

```python
anysave = AnySaveClient(
    api_base_url="https://api.anysave.click",  # обязательный
    api_token="YOUR_TOKEN",

    download_dir="./downloads",
    chunk_size=512 * 1024,
    max_concurrency=2,              # рекомендуется для bot / VPS окружения
    concurrency_max_ttl_s=60.0,     # сколько ждать slot local /links pipeline

    api_timeout_s=900.0,
    download_timeout_s=900.0,
    cache_lookup_timeout_s=10.0,

    min_speed_bytes_per_sec=10 * 1024,
    download_retry_attempts=3,

    prefer_telegram_cache=True,

    task_poll_interval_s=1.0,
    task_max_poll_time_s=900.0,
    task_create_timeout_s=30.0,

    thumbnail_placeholder="assets/thumb.jpg",
)
```

<details>
<summary><strong>Все параметры</strong></summary>

| Параметр | Тип | Default | Описание |
|---|---|---|---|
| `api_base_url` | `str` | — | URL AnySave API сервера (https://api.anysave.click) |
| `api_token` | `str \| None` | `None` | Bearer токен авторизации |
| `download_dir` | `str \| Path` | `cwd()` | Директория для файлов |
| `chunk_size` | `int` | `524288` | Размер чанка скачивания (байт) |
| `max_concurrency` | `int` | `4` | Макс. число одновременно выполняемых local `/links` задач; также используется как лимит параллельных скачиваний внутри local pipeline |
| `concurrency_max_ttl_s` | `float` | `60.0` | Сколько ждать свободный slot local `/links` pipeline перед ошибкой `client_busy` |
| `api_timeout_s` | `float` | `900.0` | Таймаут sync endpoint |
| `download_timeout_s` | `float` | `900.0` | Таймаут скачивания файла |
| `cache_lookup_timeout_s` | `float` | `10.0` | Таймаут Telegram cache |
| `min_speed_bytes_per_sec` | `int` | `10240` | Минимальная скорость (байт/с) |
| `download_retry_attempts` | `int` | `3` | Попыток resume при обрыве |
| `prefer_telegram_cache` | `bool` | `False` | Проверять Telegram cache |
| `task_poll_interval_s` | `float` | `1.0` | Интервал опроса задачи |
| `task_max_poll_time_s` | `float` | `900.0` | Максимум ожидания задачи |
| `task_create_timeout_s` | `float` | `30.0` | Таймаут создания задачи |
| `thumbnail_placeholder` | `str \| None` | `None` | Путь к заглушке thumbnail |

</details>

> Лимитер применяется только к local обработке `download_links()` / `smart_download()` на fast-link пути.  
> Server-side методы `download()` и `download_via_tasks()` не ограничиваются этим limiter'ом:
> тяжёлая работа уже выполнена сервером, а клиент обычно делает только быстрый metadata fallback.
> Это сознательно сохраняет UX и не создаёт лишнюю очередь для коротких операций.

---

## Обработка ошибок

```python
from anysave_sdk import AnySaveClient, ClientErrorCode

result = await anysave.smart_download(url)

if not result.ok:
    err = result.error

    if err.code == ClientErrorCode.API_UNAUTHORIZED:
        print("Неверный API токен")
    elif err.code == ClientErrorCode.API_RATE_LIMITED:
        print(f"Rate limit: {err.detail}")
    elif err.code == ClientErrorCode.TASK_TIMEOUT:
        print("Сервер не успел за отведённое время")
    elif err.code == ClientErrorCode.CLIENT_BUSY:
        print("Локальный fast-link pipeline занят")
    elif err.code == ClientErrorCode.LOCAL_PROCESSING_FAILED:
        print("Локальная обработка fast-link завершилась ошибкой")
    elif err.code == ClientErrorCode.SOME_DOWNLOADS_FAILED:
        if result.files:
            print(f"Частично: {len(result.files)} файл(ов)")
    else:
        print(f"[{err.code}] {err.detail}")
```

<details>
<summary><strong>Все коды ошибок</strong></summary>

| Код | Когда |
|---|---|
| `no_api_url` | `api_base_url` не задан |
| `api_connect_timeout` | Сервер не ответил на подключение |
| `api_read_timeout` | Сервер не вернул ответ за `api_timeout_s` |
| `api_connect_error` | DNS / firewall / сервер недоступен |
| `api_http_error` | Сервер вернул 4xx/5xx (кроме 401/429) |
| `api_unauthorized` | HTTP 401 — неверный токен |
| `api_rate_limited` | HTTP 429 — превышен лимит запросов |
| `server_busy` | Сервер перегружен |
| `client_busy` | Local `/links` pipeline не получил slot за `concurrency_max_ttl_s` |
| `local_processing_failed` | Локальная обработка fast-link завершилась ошибкой |
| `unknown_response` | Неизвестный формат ответа |
| `some_downloads_failed` | Часть файлов из карусели не скачалась |
| `cache_lookup_failed` | Ошибка Telegram cache (flow продолжается) |
| `task_create_failed` | Не удалось создать задачу |
| `task_poll_failed` | Ошибка опроса статуса задачи |
| `task_result_failed` | Ошибка получения результата |
| `task_timeout` | Задача не завершилась за `task_max_poll_time_s` |
| `task_download_error` | Задача завершена, скачивание упало |

</details>

> `client_busy` и `local_processing_failed` чаще всего видны при прямом вызове `download_links()`.  
> В `smart_download()` эти состояния обычно приводят к автоматическому fallback в `download_via_tasks()`.

---

## Модели данных

<details>
<summary><strong>DownloadResult</strong></summary>

```python
result.status        # DownloadStatus: SUCCESS | PARTIAL | CACHED | ERROR
result.ok            # True если есть результат
result.is_cached     # True если из Telegram cache
result.error_msg     # str | None
result.files         # list[DownloadedFile]
result.cached        # CachedResult | None
result.error         # ClientError | None
```

</details>

<details>
<summary><strong>DownloadedFile</strong></summary>

```python
f.type               # "video" | "audio" | "photo" | "animation"
f.path               # абсолютный путь к файлу
f.file_size_bytes    # int | None
f.is_truncated       # True если обрезан
f.is_video           # True для video и animation
f.is_audio           # True для audio
f.is_visual          # True для video, animation, photo
f.extra.metadata     # FileMediaMetadata: duration, width, height, thumbnail
```

</details>

<details>
<summary><strong>CachedResult</strong></summary>

```python
cache = result.cached
cache.service                     # "youtube" | "tiktok" | ...
cache.files[0].file_id            # Telegram file_id
cache.files[0].type               # "video" | "audio" | "photo"
cache.files[0].file_size_bytes
cache.files[0].duration
```

</details>

<details>
<summary><strong>RemoteDownloadResult</strong></summary>

```python
remote.ok                         # True при SUCCESS или CACHED
remote.files                      # list[RemoteFile]
remote.audio                      # RemoteFile | None (split audio)
remote.files[0].url               # CDN URL
remote.files[0].is_hls_manifest   # True если .m3u8 или .mpd
remote.files[0].filename
```

</details>

---

## Требования

- **Python 3.10+**
- **ffmpeg** в PATH — для HLS, merge video+audio, truncate, thumbnails
- Доступ к AnySave
---

## Ссылки

- [AnySave API — официальный сайт](https://anysave.click)
- [Документация API](https://anysave.click/docs)
- [Interactive Playground](https://anysave.click/docs/playground)
- [Стоимость эндпоинтов в кредитах](https://anysave.click/docs)
- [Telegram support](https://t.me/AnySaveAPI)

## Лицензия

MIT