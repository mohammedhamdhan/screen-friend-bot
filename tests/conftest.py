"""
Test configuration and shared fixtures.

Uses an in-memory SQLite database via aiosqlite for fast, isolated tests.
SQLite does not support PostgreSQL ENUM types, so we swap the RequestStatus
column to a plain VARCHAR before creating the schema.
"""

import os

# ---------------------------------------------------------------------------
# Provide dummy env vars so pydantic-settings can load without a real .env.
# These must be set BEFORE any app module is imported.
# ---------------------------------------------------------------------------
os.environ.setdefault("DATABASE_URL", "sqlite+aiosqlite:///:memory:")
os.environ.setdefault("REDIS_URL", "redis://localhost:6379/0")
os.environ.setdefault("SECRET_KEY", "test-secret-key")
os.environ.setdefault("TELEGRAM_BOT_TOKEN", "0:test-token")
os.environ.setdefault("WEBHOOK_URL", "https://example.com/webhook")
os.environ.setdefault("R2_ACCOUNT_ID", "test-account")
os.environ.setdefault("R2_ACCESS_KEY_ID", "test-key-id")
os.environ.setdefault("R2_SECRET_ACCESS_KEY", "test-secret")
os.environ.setdefault("R2_BUCKET_NAME", "test-bucket")
os.environ.setdefault("R2_PUBLIC_URL", "https://example.com")
os.environ.setdefault("REQUEST_TIMEOUT_MINUTES", "30")
os.environ.setdefault("REQUEST_COOLDOWN_MINUTES", "15")

# Clear the lru_cache so pydantic-settings reloads from the env vars above
# rather than from a cached result that read a real .env file.
try:
    from app.config import get_settings
    get_settings.cache_clear()
except Exception:
    pass

import pytest
import pytest_asyncio
from unittest.mock import AsyncMock, patch, MagicMock
from httpx import AsyncClient, ASGITransport
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker, AsyncSession
from sqlalchemy.ext.compiler import compiles
from sqlalchemy import Enum

# ---------------------------------------------------------------------------
# SQLite ENUM compatibility shim
#
# SQLAlchemy's Enum type emits a CREATE TYPE statement for PostgreSQL which
# SQLite does not understand.  The @compiles decorator below renders any Enum
# column as VARCHAR on SQLite so schema creation succeeds.
# ---------------------------------------------------------------------------


@compiles(Enum, "sqlite")
def _compile_enum_sqlite(element, compiler, **kw):
    """Render Enum as VARCHAR for SQLite DDL."""
    return "VARCHAR(50)"


# Now it is safe to import app modules
from app.database import Base, get_db  # noqa: E402
from app.main import app  # noqa: E402


# ---------------------------------------------------------------------------
# SQLite-compatible engine
# ---------------------------------------------------------------------------

TEST_DATABASE_URL = "sqlite+aiosqlite:///:memory:"

test_engine = create_async_engine(TEST_DATABASE_URL, echo=False)
TestSessionLocal = async_sessionmaker(
    test_engine, class_=AsyncSession, expire_on_commit=False
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest_asyncio.fixture(scope="function")
async def db_session():
    """Provide an async SQLAlchemy session backed by in-memory SQLite.

    The full schema is created before each test and dropped after so that
    tests are completely isolated.
    """
    async with test_engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    async with TestSessionLocal() as session:
        yield session

    async with test_engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)


@pytest_asyncio.fixture(scope="function")
async def client(db_session: AsyncSession):
    """AsyncClient pointing at the FastAPI app with the DB dependency overridden."""

    async def override_get_db():
        yield db_session

    app.dependency_overrides[get_db] = override_get_db

    # Patch Celery task so it does not try to reach a real broker.
    # Patch bot_service functions so they never make real HTTP calls.
    with patch("app.routers.requests.expire_request") as mock_expire, \
         patch("app.services.bot_service.post_request_to_group", new_callable=AsyncMock) as mock_post, \
         patch("app.services.bot_service.post_resolution", new_callable=AsyncMock) as mock_resolve:

        mock_expire.apply_async = MagicMock(return_value=None)
        mock_post.return_value = None
        mock_resolve.return_value = None

        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as ac:
            # Expose mocks as attributes so tests can inspect calls
            ac.mock_expire = mock_expire
            ac.mock_post_request = mock_post
            ac.mock_resolve = mock_resolve
            yield ac

    app.dependency_overrides.clear()


# ---------------------------------------------------------------------------
# Helper factories (imported into test modules)
# ---------------------------------------------------------------------------


async def create_user(db: AsyncSession, telegram_id: int, username: str = None):
    """Insert a User row and return the ORM object."""
    from app.models import User

    user = User(telegram_id=telegram_id, username=username or f"user_{telegram_id}")
    db.add(user)
    await db.flush()
    return user


async def create_group(
    db: AsyncSession, telegram_chat_id: int, vote_threshold: int = 1
):
    """Insert a Group row and return the ORM object."""
    from app.models import Group

    group = Group(
        telegram_chat_id=telegram_chat_id,
        name=f"group_{telegram_chat_id}",
        vote_threshold=vote_threshold,
    )
    db.add(group)
    await db.flush()
    return group


async def add_member(db: AsyncSession, user, group) -> None:
    """Add a Membership linking user to group."""
    from app.models import Membership

    membership = Membership(user_id=user.id, group_id=group.id)
    db.add(membership)
    await db.flush()
