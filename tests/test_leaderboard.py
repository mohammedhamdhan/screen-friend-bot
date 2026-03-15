"""
Tests for leaderboard logic.

Scenarios covered:
- 10.2.9   Score formula — score = (clean_days*10) - (requests_made*3) - (requests_denied*5)
- 10.2.10  Weekly reset — a new week_start row is created when the week changes
- 10.2.11  clean_days increment — upsert_leaderboard("clean_days") increments counter
- 10.2.12  denied increment — upsert_leaderboard("requests_denied") increments counter
- 10.2.13  GET /api/v1/leaderboard/{chat_id} returns ranked entries
"""

import pytest
from datetime import date, timedelta

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Leaderboard
from app.services import leaderboard_service
from tests.conftest import create_user, create_group, add_member


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


async def _seed(db: AsyncSession, telegram_id: int = 10000, chat_id: int = 50000):
    user = await create_user(db, telegram_id=telegram_id, username=f"user_{telegram_id}")
    group = await create_group(db, telegram_chat_id=chat_id)
    await add_member(db, user, group)
    await db.commit()
    return user, group


# ---------------------------------------------------------------------------
# Test: score formula
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_score_formula_requests_made(db_session):
    """Incrementing requests_made should lower the score by 3."""
    user, group = await _seed(db_session, 10001, 50001)

    await leaderboard_service.upsert_leaderboard(
        user_id=user.id, group_id=group.id, field="requests_made", db=db_session
    )
    await db_session.commit()

    result = await db_session.execute(
        select(Leaderboard).where(
            Leaderboard.user_id == user.id,
            Leaderboard.group_id == group.id,
        )
    )
    row = result.scalar_one()

    # score = (0*10) - (1*3) - (0*5) = -3
    assert row.requests_made == 1
    assert row.score == -3


@pytest.mark.asyncio
async def test_score_formula_requests_denied(db_session):
    """Incrementing requests_denied should lower the score by 5."""
    user, group = await _seed(db_session, 10002, 50002)

    await leaderboard_service.upsert_leaderboard(
        user_id=user.id, group_id=group.id, field="requests_denied", db=db_session
    )
    await db_session.commit()

    result = await db_session.execute(
        select(Leaderboard).where(
            Leaderboard.user_id == user.id,
            Leaderboard.group_id == group.id,
        )
    )
    row = result.scalar_one()

    # score = (0*10) - (0*3) - (1*5) = -5
    assert row.requests_denied == 1
    assert row.score == -5


@pytest.mark.asyncio
async def test_score_formula_clean_days(db_session):
    """Incrementing clean_days should raise the score by 10."""
    user, group = await _seed(db_session, 10003, 50003)

    await leaderboard_service.upsert_leaderboard(
        user_id=user.id, group_id=group.id, field="clean_days", db=db_session
    )
    await db_session.commit()

    result = await db_session.execute(
        select(Leaderboard).where(
            Leaderboard.user_id == user.id,
            Leaderboard.group_id == group.id,
        )
    )
    row = result.scalar_one()

    # score = (1*10) - (0*3) - (0*5) = 10
    assert row.clean_days == 1
    assert row.score == 10


@pytest.mark.asyncio
async def test_score_formula_combined(db_session):
    """Score reflects the full formula after multiple increments."""
    user, group = await _seed(db_session, 10004, 50004)

    # 3 clean days, 2 requests made, 1 denied
    for _ in range(3):
        await leaderboard_service.upsert_leaderboard(
            user_id=user.id, group_id=group.id, field="clean_days", db=db_session
        )
    for _ in range(2):
        await leaderboard_service.upsert_leaderboard(
            user_id=user.id, group_id=group.id, field="requests_made", db=db_session
        )
    await leaderboard_service.upsert_leaderboard(
        user_id=user.id, group_id=group.id, field="requests_denied", db=db_session
    )
    await db_session.commit()

    result = await db_session.execute(
        select(Leaderboard).where(
            Leaderboard.user_id == user.id,
            Leaderboard.group_id == group.id,
        )
    )
    row = result.scalar_one()

    # score = (3*10) - (2*3) - (1*5) = 30 - 6 - 5 = 19
    assert row.clean_days == 3
    assert row.requests_made == 2
    assert row.requests_denied == 1
    assert row.score == 19


# ---------------------------------------------------------------------------
# Test: weekly reset — new week_start produces a new row
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_weekly_reset_creates_new_row(db_session, monkeypatch):
    """Calling upsert with a different week_start creates a separate row."""
    from app.services import leaderboard_service as ls

    user, group = await _seed(db_session, 10010, 50010)

    # --- Week A ---
    week_a = date(2025, 1, 6)  # a known Monday
    monkeypatch.setattr(ls, "_current_week_start", lambda: week_a)

    await ls.upsert_leaderboard(
        user_id=user.id, group_id=group.id, field="clean_days", db=db_session
    )
    await db_session.commit()

    # --- Week B (next week) ---
    week_b = week_a + timedelta(weeks=1)
    monkeypatch.setattr(ls, "_current_week_start", lambda: week_b)

    await ls.upsert_leaderboard(
        user_id=user.id, group_id=group.id, field="requests_made", db=db_session
    )
    await db_session.commit()

    # Both rows should exist independently
    result = await db_session.execute(
        select(Leaderboard).where(
            Leaderboard.user_id == user.id,
            Leaderboard.group_id == group.id,
        )
    )
    rows = result.scalars().all()
    assert len(rows) == 2

    week_starts = {r.week_start for r in rows}
    assert week_a in week_starts
    assert week_b in week_starts


# ---------------------------------------------------------------------------
# Test: clean_days increment
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_clean_days_increment(db_session):
    """Each call to upsert_leaderboard('clean_days') increments the counter by 1."""
    user, group = await _seed(db_session, 10020, 50020)

    for expected in range(1, 6):
        await leaderboard_service.upsert_leaderboard(
            user_id=user.id, group_id=group.id, field="clean_days", db=db_session
        )
        await db_session.commit()

        result = await db_session.execute(
            select(Leaderboard).where(
                Leaderboard.user_id == user.id,
                Leaderboard.group_id == group.id,
            )
        )
        row = result.scalar_one()
        assert row.clean_days == expected


# ---------------------------------------------------------------------------
# Test: denied increment
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_denied_increment(db_session):
    """Each call to upsert_leaderboard('requests_denied') increments the counter by 1."""
    user, group = await _seed(db_session, 10030, 50030)

    for expected in range(1, 4):
        await leaderboard_service.upsert_leaderboard(
            user_id=user.id, group_id=group.id, field="requests_denied", db=db_session
        )
        await db_session.commit()

        result = await db_session.execute(
            select(Leaderboard).where(
                Leaderboard.user_id == user.id,
                Leaderboard.group_id == group.id,
            )
        )
        row = result.scalar_one()
        assert row.requests_denied == expected


# ---------------------------------------------------------------------------
# Test: GET /api/v1/leaderboard/{chat_id}
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_leaderboard_endpoint_returns_entries(client, db_session):
    """The leaderboard endpoint returns the correct data for the current week."""
    user, group = await _seed(db_session, 10040, 50040)

    # Seed two leaderboard rows for the current week
    await leaderboard_service.upsert_leaderboard(
        user_id=user.id, group_id=group.id, field="clean_days", db=db_session
    )
    await leaderboard_service.upsert_leaderboard(
        user_id=user.id, group_id=group.id, field="clean_days", db=db_session
    )
    await db_session.commit()

    response = await client.get(f"/api/v1/leaderboard/{group.telegram_chat_id}")

    assert response.status_code == 200
    data = response.json()
    assert data["group_telegram_chat_id"] == group.telegram_chat_id
    assert len(data["entries"]) == 1
    entry = data["entries"][0]
    assert entry["clean_days"] == 2
    assert entry["score"] == 20  # 2*10 = 20


@pytest.mark.asyncio
async def test_leaderboard_endpoint_ordering(client, db_session):
    """Leaderboard entries are sorted by score descending."""
    user1 = await create_user(db_session, telegram_id=10050, username="u10050")
    user2 = await create_user(db_session, telegram_id=10051, username="u10051")
    group = await create_group(db_session, telegram_chat_id=50050)
    await add_member(db_session, user1, group)
    await add_member(db_session, user2, group)
    await db_session.commit()

    # user1: 3 clean days → score 30
    for _ in range(3):
        await leaderboard_service.upsert_leaderboard(
            user_id=user1.id, group_id=group.id, field="clean_days", db=db_session
        )

    # user2: 1 clean day → score 10
    await leaderboard_service.upsert_leaderboard(
        user_id=user2.id, group_id=group.id, field="clean_days", db=db_session
    )
    await db_session.commit()

    response = await client.get(f"/api/v1/leaderboard/{group.telegram_chat_id}")

    assert response.status_code == 200
    entries = response.json()["entries"]
    assert len(entries) == 2
    # Higher score first
    assert entries[0]["score"] >= entries[1]["score"]
    assert entries[0]["score"] == 30
    assert entries[1]["score"] == 10


@pytest.mark.asyncio
async def test_leaderboard_endpoint_unknown_group(client, db_session):
    """Leaderboard endpoint with unknown group should return 404."""
    response = await client.get("/api/v1/leaderboard/99999999")
    assert response.status_code == 404


@pytest.mark.asyncio
async def test_leaderboard_empty_week(client, db_session):
    """Leaderboard for a group with no activity this week returns empty entries."""
    _, group = await _seed(db_session, 10060, 50060)

    response = await client.get(f"/api/v1/leaderboard/{group.telegram_chat_id}")

    assert response.status_code == 200
    data = response.json()
    assert data["entries"] == []
    assert "No leaderboard data" in data["formatted"]


# ---------------------------------------------------------------------------
# Test: invalid field guard
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_upsert_invalid_field_raises(db_session):
    """upsert_leaderboard raises ValueError for an unknown field name."""
    user, group = await _seed(db_session, 10070, 50070)

    with pytest.raises(ValueError, match="field must be one of"):
        await leaderboard_service.upsert_leaderboard(
            user_id=user.id, group_id=group.id, field="invalid_field", db=db_session
        )
