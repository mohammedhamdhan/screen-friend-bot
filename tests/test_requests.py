"""
Tests for POST /api/v1/requests.

Scenarios covered:
- 10.2.1  Create success — happy path, returns 201 with pending status
- 10.2.2  Pending rejection — second request while one is already pending → 409
- 10.2.3  Cooldown rejection — new request too soon after a denial → 429
- 10.2.4  Expiry — request created with a non-null expires_at
"""

import pytest
import pytest_asyncio
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock, patch

from sqlalchemy.ext.asyncio import AsyncSession

from tests.conftest import create_user, create_group, add_member


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


async def _seed(db: AsyncSession, telegram_id: int = 111, chat_id: int = 999):
    """Create a user, a group, and a membership. Return (user, group)."""
    user = await create_user(db, telegram_id=telegram_id, username="tester")
    group = await create_group(db, telegram_chat_id=chat_id, vote_threshold=1)
    await add_member(db, user, group)
    await db.commit()
    return user, group


def _req_payload(telegram_id: int, chat_id: int, **kwargs):
    payload = {
        "telegram_id": telegram_id,
        "group_telegram_chat_id": chat_id,
        "app_name": "Instagram",
        "minutes_requested": 30,
    }
    payload.update(kwargs)
    return payload


# ---------------------------------------------------------------------------
# Test: create success
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_create_request_success(client, db_session):
    """A valid request should be created and returned with status=pending."""
    user, group = await _seed(db_session)

    response = await client.post(
        "/api/v1/requests",
        json=_req_payload(user.telegram_id, group.telegram_chat_id),
    )

    assert response.status_code == 201, response.text
    data = response.json()
    assert data["status"] == "pending"
    assert data["app_name"] == "Instagram"
    assert data["minutes_requested"] == 30
    assert data["expires_at"] is not None


@pytest.mark.asyncio
async def test_create_request_with_photo(client, db_session):
    """Request with photo_url and caption should succeed."""
    user, group = await _seed(db_session, telegram_id=112, chat_id=1001)

    response = await client.post(
        "/api/v1/requests",
        json=_req_payload(
            user.telegram_id,
            group.telegram_chat_id,
            photo_url="https://example.com/shot.png",
            caption="Just 30 mins please",
        ),
    )

    assert response.status_code == 201
    data = response.json()
    assert data["photo_url"] == "https://example.com/shot.png"
    assert data["caption"] == "Just 30 mins please"


@pytest.mark.asyncio
async def test_create_request_unknown_user(client, db_session):
    """Request for an unregistered user should return 404."""
    _, group = await _seed(db_session, telegram_id=200, chat_id=1002)

    response = await client.post(
        "/api/v1/requests",
        json=_req_payload(99999, group.telegram_chat_id),
    )

    assert response.status_code == 404


@pytest.mark.asyncio
async def test_create_request_unknown_group(client, db_session):
    """Request for an unknown group should return 404."""
    user, _ = await _seed(db_session, telegram_id=300, chat_id=1003)

    response = await client.post(
        "/api/v1/requests",
        json=_req_payload(user.telegram_id, 99999),
    )

    assert response.status_code == 404


@pytest.mark.asyncio
async def test_create_request_not_member(client, db_session):
    """Request from a user who is not a group member should return 403."""
    user = await create_user(db_session, telegram_id=400, username="outsider")
    group = await create_group(db_session, telegram_chat_id=1004)
    # Deliberately NOT adding a membership
    await db_session.commit()

    response = await client.post(
        "/api/v1/requests",
        json=_req_payload(user.telegram_id, group.telegram_chat_id),
    )

    assert response.status_code == 403


# ---------------------------------------------------------------------------
# Test: pending rejection
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_create_request_pending_rejected(client, db_session):
    """Submitting a second request while one is pending must return 409."""
    user, group = await _seed(db_session, telegram_id=500, chat_id=2000)

    # First request — should succeed
    r1 = await client.post(
        "/api/v1/requests",
        json=_req_payload(user.telegram_id, group.telegram_chat_id),
    )
    assert r1.status_code == 201

    # Second request while first is still pending — should fail
    r2 = await client.post(
        "/api/v1/requests",
        json=_req_payload(user.telegram_id, group.telegram_chat_id),
    )

    assert r2.status_code == 409
    assert "pending" in r2.json()["detail"].lower()


# ---------------------------------------------------------------------------
# Test: cooldown rejection
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_create_request_cooldown_rejected(client, db_session):
    """A request submitted within the cooldown window after a denial should return 429."""
    from app.models import Request, RequestStatus

    user, group = await _seed(db_session, telegram_id=600, chat_id=3000)

    # Insert a denied request whose created_at is only 5 minutes ago
    # (well within the 15-minute cooldown)
    recent_denial = Request(
        user_id=user.id,
        group_id=group.id,
        app_name="TikTok",
        minutes_requested=15,
        status=RequestStatus.denied,
        created_at=datetime.now(tz=timezone.utc) - timedelta(minutes=5),
        expires_at=datetime.now(tz=timezone.utc) + timedelta(minutes=25),
    )
    db_session.add(recent_denial)
    await db_session.commit()

    response = await client.post(
        "/api/v1/requests",
        json=_req_payload(user.telegram_id, group.telegram_chat_id),
    )

    assert response.status_code == 429
    assert "cooldown" in response.json()["detail"].lower()


@pytest.mark.asyncio
async def test_create_request_after_cooldown_succeeds(client, db_session):
    """A request submitted after the cooldown window has passed should succeed."""
    from app.models import Request, RequestStatus

    user, group = await _seed(db_session, telegram_id=700, chat_id=4000)

    # Insert a denied request whose created_at is 20 minutes ago
    # (outside the 15-minute cooldown)
    old_denial = Request(
        user_id=user.id,
        group_id=group.id,
        app_name="TikTok",
        minutes_requested=15,
        status=RequestStatus.denied,
        created_at=datetime.now(tz=timezone.utc) - timedelta(minutes=20),
        expires_at=datetime.now(tz=timezone.utc) - timedelta(minutes=10),
    )
    db_session.add(old_denial)
    await db_session.commit()

    response = await client.post(
        "/api/v1/requests",
        json=_req_payload(user.telegram_id, group.telegram_chat_id),
    )

    assert response.status_code == 201


# ---------------------------------------------------------------------------
# Test: expiry — expires_at is set on creation
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_create_request_expires_at_set(client, db_session):
    """Created request must have expires_at set approximately 30 minutes in the future."""
    user, group = await _seed(db_session, telegram_id=800, chat_id=5000)

    before = datetime.now(tz=timezone.utc)
    response = await client.post(
        "/api/v1/requests",
        json=_req_payload(user.telegram_id, group.telegram_chat_id),
    )
    after = datetime.now(tz=timezone.utc)

    assert response.status_code == 201
    expires_str = response.json()["expires_at"]
    expires_at = datetime.fromisoformat(expires_str)

    # Should expire ~30 minutes from now (allow ±5 seconds for test execution time)
    expected_min = before + timedelta(minutes=29, seconds=55)
    expected_max = after + timedelta(minutes=30, seconds=5)
    assert expected_min <= expires_at <= expected_max, (
        f"expires_at {expires_at} not within expected range "
        f"[{expected_min}, {expected_max}]"
    )
