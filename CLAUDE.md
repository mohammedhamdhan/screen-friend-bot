# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Commands

```bash
# Run FastAPI dev server
uvicorn app.main:app --reload

# Run Celery worker with beat scheduler
celery -A app.workers.celery_app worker --beat --loglevel=info

# Run all tests
pytest

# Run a single test file
pytest tests/test_requests.py

# Run a single test function
pytest tests/test_requests.py::test_function_name -v

# Database migrations
alembic revision --autogenerate -m "description"
alembic upgrade head

# Docker (runs api, worker, db, redis)
docker-compose up --build
```

## Architecture

**ScreenGate** is a Telegram bot for social screen time accountability. Three processes run together:

1. **FastAPI app** (`app/main.py`) — REST API + Telegram webhook receiver
2. **Celery worker** (`app/workers/`) — background tasks + beat scheduler for daily/weekly jobs
3. **PostgreSQL + Redis** — data store + Celery broker

### Request flow

Telegram → `POST /api/v1/webhook/telegram` → PTB `Application.process_update()` → bot handlers (`bot/handlers/`) → call FastAPI endpoints or Telegram API directly

The bot `Application` is created in `bot/main.py` and initialized as a FastAPI background task during startup (stored on `app.state.application`). This solves the chicken-and-egg problem: FastAPI must be serving before the webhook can be set.

### Key architectural decisions

- **bot_service.py uses raw httpx, not PTB** — calls Telegram Bot API directly via `https://api.telegram.org/bot{TOKEN}` to avoid circular imports with the PTB Application instance
- **OCR uses GPT-4o vision** (`app/services/ocr_service.py`) — screenshots are base64-encoded and sent to OpenAI, returns structured JSON of app names + minutes
- **Celery beat runs every minute** — tasks themselves check if it's the right UTC hour/minute for each group's configured check-in time
- **Webhook always returns 200** — prevents Telegram retry backoff even on errors
- **Bot init has 5 retries** — resilience against startup race conditions

### Layer responsibilities

| Layer | Location | Role |
|-------|----------|------|
| Routers | `app/routers/` | REST endpoints, DB queries via `AsyncSession` |
| Schemas | `app/schemas/` | Pydantic models with `from_attributes=True` for ORM mode |
| Models | `app/models/` | SQLAlchemy async ORM (11 tables) |
| Bot handlers | `bot/handlers/` | Telegram command/message handlers |
| Services | `app/services/` | External API calls (Telegram, OpenAI) |
| Workers | `app/workers/tasks.py` | Async tasks wrapped with `asyncio.run()` |

### Database

- Async SQLAlchemy 2.0 with asyncpg
- Query pattern: `result = await db.execute(select(Model).where(...))` then `result.scalar_one_or_none()`
- Tests use in-memory SQLite (conftest.py patches Enum→VARCHAR for compatibility)
- Dependency injection: `db: AsyncSession = Depends(get_db)`

## Configuration

All config via environment variables, loaded by Pydantic Settings in `app/config.py`. Key vars: `DATABASE_URL`, `REDIS_URL`, `TELEGRAM_BOT_TOKEN`, `WEBHOOK_URL`, `OPENAI_API_KEY`, `R2_*` (Cloudflare R2 for image storage). Tunable behavior defaults (check-in times, timeouts, tolerance) are documented in `app/config.py`.

## Testing

Tests mock Celery tasks and bot_service calls. Fixtures in `tests/conftest.py` provide `db_session` and `client`, plus helpers (`create_user`, `create_group`, `add_member`). Uses `pytest-asyncio` with `asyncio_mode = auto`.
