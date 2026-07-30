# Примеры AnySave API SDK

## Требования

- Python 3.10+
- `ffmpeg` в PATH (`apt install ffmpeg` / `brew install ffmpeg`)
- Доступ к AnySave-совместимому API серверу
- Токен Telegram бота ([@BotFather](https://t.me/BotFather)) — для bot-примеров

## Установка

```bash
# Базовый SDK
pip install anysave-api-sdk

# SDK + aiogram 3 (для bot-примеров)
pip install "anysave-api-sdk[aiogram3]"

# SDK + aiogram 2 (legacy)
pip install "anysave-api-sdk[aiogram2]"
```

## Переменные окружения

| Переменная | Описание | Где нужна |
|---|---|---|
| `DOWNLOADER_URL` | URL API сервера | Все примеры |
| `API_TOKEN` | Bearer токен авторизации | Все примеры |
| `BOT_TOKEN` | Telegram Bot Token от @BotFather | Bot-примеры |
| `DOWNLOAD_DIR` | Директория для файлов (default: `./downloads`) | Bot-примеры |

```bash
export DOWNLOADER_URL="https://api.example.com"
export API_TOKEN="your-api-token"
export BOT_TOKEN="your-bot-token"
```

## Примеры

### `quick_start.py` — минимальный пример

Скачивает одно видео через `smart_download()`. Без Telegram, без зависимостей кроме SDK.
Лучшая точка входа для понимания SDK.

```bash
python examples/quick_start.py
```

---

### `aiogram3_basic.py` — базовый бот (рекомендуется)

Бот на aiogram 3. Команда `/download <url>` или просто URL.
`smart_download()` автоматически выбирает fast-link или task pipeline.
Поддерживает видео, аудио, фото, карусели.

```bash
python examples/aiogram3_basic.py
```

---

### `aiogram3_cached.py` — бот с Telegram cache

`prefer_telegram_cache=True`: при повторном запросе URL — мгновенный ответ через `file_id`.

```bash
python examples/aiogram3_cached.py
```

---

### `aiogram3_links_only.py` — бот с CDN-ссылками

`get_remote_links()` — файлы не скачиваются, передаются через `URLInputFile`.
Не требует disk space. Не работает для HLS-потоков.

```bash
python examples/aiogram3_links_only.py
```

---

### `aiogram3_tasks.py` — бот с task pipeline

`download_via_tasks()` для YouTube 4K и длинных VOD.
Создаёт задачу → ждёт до 10 минут → отправляет результат.

```bash
python examples/aiogram3_tasks.py
```

---

### `aiogram2_basic.py` — бот на aiogram 2 (legacy)

То же что `aiogram3_basic.py`, но на aiogram 2.

```bash
python examples/aiogram2_basic.py
```

## Какой метод выбрать

```
Максимальная простота          → smart_download()       aiogram3_basic.py
Повторные запросы одного URL   → smart_download() + кэш aiogram3_cached.py
Только ссылки, без скачивания  → get_remote_links()     aiogram3_links_only.py
YouTube 4K / тяжёлые VOD      → download_via_tasks()    aiogram3_tasks.py
Проект на aiogram 2            → smart_download()       aiogram2_basic.py
```