# tg_gpt — Telegram GPT бот

Небольшой асинхронный Telegram-бот на Python с использованием `aiogram` и OpenAI Responses API.
Проект предоставляет HTTP/бот-роутеры для обработки чатов и админских команд, хранит диалоговую историю в Redis,
поддерживает суммаризацию истории, обработку изображений и аудио (через Responses / Audio Transcriptions),
а также простую защиту от спама (rate limiting) и управление доступом.

**Ключевые характеристики**

- Асинхронный код на `aiogram` (polling)
- Взаимодействие с OpenAI через `openai.AsyncOpenAI` (Responses API)
- Хранение истории и TTL в Redis
- Суммаризация истории перед отправкой (reduce-to-summary)
- Поддержка изображений и транскрибирования аудио
- Админ- и чат-маршруты, allowlist/blacklist и безопасные дефолты

## Возможности

- Диалоговый контекст на пользователя с ограничением длины и триггерной суммаризацией
- Генерация ответов через Responses API (`gpt-5.4-nano` по умолчанию)
- Короткие сводки (summary) для долгих диалогов
- Вставка изображений в контекст запроса и генерация ответа с учётом изображения
- Транскрибирование аудио сообщений
- Rate limiter для защиты от спама
- Админские команды и контролируемый доступ по `ALLOWED_USER_IDS` / `ADMIN_USER_IDS`

## Переменные окружения

Проект читает настройки из окружения (и `.env`) через `app/config.py`. Важные переменные:

- `TELEGRAM_BOT_TOKEN` — токен бота (обязательный)
- `OPENAI_API_KEY` — ключ OpenAI (обязательный)
- `REDIS_URL` — URL Redis (по умолчанию `redis://localhost:6379/0`)
- `REDIS_HISTORY_TTL` — TTL для истории в секундах
- `HISTORY_MAX_MESSAGES` — макс. сообщений, передаваемых модели
- `HISTORY_STORE_MESSAGES` — сообщений храним до суммаризации
- `SUMMARY_TRIGGER_MESSAGES` — порог для запуска суммаризации (если не задан, вычисляется автоматически)
- `SUMMARY_MAX_CHARS` — макс. символов в summary
- `RATE_LIMIT` — максимум запросов за окно
- `RATE_WINDOW_SECONDS` — окно в секундах для rate limiting
- `SYSTEM_PROMPT` — системный промпт, отправляемый модели
- `ALLOWED_USER_IDS` — пусто (secure default) / `*` / `123,456`
- `ADMIN_USER_IDS` — список админов (по умолчанию повторяет `ALLOWED_USER_IDS` в allowlist-режиме)

## Быстрый запуск (локально)

1. Создайте виртуальное окружение и установите зависимости:

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt
```

2. Поднимите Redis (локально или через Docker):

```bash
docker run -d --name tg-gpt-redis -p 6379:6379 redis:7-alpine
```

3. Создайте `.env` (или экспортируйте переменные), например:

```env
TELEGRAM_BOT_TOKEN=your_telegram_token
OPENAI_API_KEY=sk-...
REDIS_URL=redis://localhost:6379/0
SYSTEM_PROMPT=You are a helpful Telegram assistant.
ALLOWED_USER_IDS=
RATE_LIMIT=5
RATE_WINDOW_SECONDS=30
HISTORY_MAX_MESSAGES=20
REDIS_HISTORY_TTL=86400
```

4. Запустите бота:

```bash
python3 main.py
```

## Docker

Проект содержит `Dockerfile` и `docker-compose.yml`. Для запуска через Docker Compose:

```bash
docker compose up -d --build
```

Логи сервиса:

```bash
docker compose logs -f
```

## Структура проекта (ключевые файлы)

- `main.py` — точка входа, создаёт `Dispatcher` и подключает маршруты
- `app/config.py` — чтение и валидация настроек из окружения
- `app/access_control.py` — проверка allowlist/admin и seed дефолтов
- `app/memory.py` — работа с историей диалога в Redis и суммаризация
- `app/openai_client.py` — обёртка над `openai.AsyncOpenAI` (Responses API, audio)
- `app/rate_limiter.py` — защита от спама на базе Redis
- `app/routers/chat.py` — обработка сообщений пользователей
- `app/routers/admin.py` — админские команды/эндпоинты
- `app/routers/utils.py` — вспомогательные хелперы для роутов

## Команды бота

- `/start` — приветствие и (опционально) сброс состояния
- `/reset` — очистка контекста диалога

## Примечания

- По умолчанию проект использует безопасные настройки: пустой `ALLOWED_USER_IDS` делает бота недоступным для всех, чтобы избежать случайного публичного запуска.
- Модели и параметры (например `gpt-5.4-nano`, температура и т.п.) настраиваются в коде и могут быть проксированы через переменные окружения/настройки при необходимости.

Если хотите, могу добавить раздел с примером `.env` для продакшн-деплоя и пример `docker-compose.yml` секции.
