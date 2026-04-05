# Telegram GPT Bot

Телеграм-бот на [aiogram 3](https://docs.aiogram.dev/) c поддержкой GPT-5.4-nano через OpenAI Responses API. Бот хранит контекст в Redis, показывает `typing`, имеет системный промпт и простую защиту от спама.

## Возможности
- 💬 Диалоговый контекст на пользователя (системный промпт + история в Redis)
- 🔄 Обычный ответ (typing во время генерации)
- 🧠 GPT-5.4-nano через `responses` API (без `completions`)
- ♻️ Память переживает рестарты за счёт Redis
- 🚫 Rate limiting, предотвращающий спам

## Запуск
1. Установите зависимости:
	   ```bash
	   python3 -m venv .venv && source .venv/bin/activate
	   python3 -m pip install -r requirements.txt
	   ```
2. Поднимите Redis (локально или в облаке). Для Docker:
   ```bash
   docker run -d --name tg-gpt-redis -p 6379:6379 redis:7-alpine
   ```
3. Создайте файл `.env` или экспортируйте переменные окружения:
   ```env
   TELEGRAM_BOT_TOKEN=xxx
   OPENAI_API_KEY=sk-...
   REDIS_URL=redis://localhost:6379/0
   SYSTEM_PROMPT="You are a friendly assistant..."
   RATE_LIMIT=5
   RATE_WINDOW_SECONDS=30
   HISTORY_MAX_MESSAGES=20
   REDIS_HISTORY_TTL=86400
   ```
4. Запустите бота:
	   ```bash
	   python3 main.py
	   ```

## Docker (Debian 13)
1. Поднимите Redis и бота через `docker compose`:
   ```bash
   docker compose up -d --build
   ```
2. Логи:
   ```bash
   docker compose logs -f bot
   ```

Важно: в `docker-compose.yml` внутри контейнера используется `REDIS_URL=redis://redis:6379/0`, поэтому в вашем `.env` можно держать `REDIS_URL` как угодно для локального запуска.

## Команды
- `/start` — приветствие и сброс истории
- `/reset` — очистка контекста диалога

## Структура
- `main.py` — точка входа, настройка aiogram
- `app/config.py` — загрузка настроек
- `app/memory.py` — работа с историей диалога в Redis
- `app/openai_client.py` — потоковое взаимодействие с Responses API
- `app/rate_limiter.py` — простая защита от спама
