"""
Tests for POST /api/v1/votes.

Scenarios covered:
- 10.2.5  Threshold approval — enough yes-votes resolves the request as approved
- 10.2.6  Self-vote block — requester cannot vote on their own request → 403
- 10.2.7  Duplicate block — same voter cannot cast a second vote (upsert re-uses)
- 10.2.8  Expired / non-pending block — voting on a non-pending request → 409
"""

import pytest
from datetime import datetime, timedelta, timezone

from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Request, RequestStatus
from tests.conftest import create_user, create_group, add_member


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


async def _seed(
    db: AsyncSession,
    requester_id: int = 1000,
    voter_id: int = 1001,
    chat_id: int = 9000,
    vote_threshold: int = 1,
) -> tuple:
    """Create requester, voter, group, membership for both, and a pending request."""
    requester = await create_user(db, telegram_id=requester_id, username="requester")
    voter = await create_user(db, telegram_id=voter_id, username="voter")
    group = await create_group(db, telegram_chat_id=chat_id, vote_threshold=vote_threshold)
    await add_member(db, requester, group)
    await add_member(db, voter, group)

    request = Request(
        user_id=requester.id,
        group_id=group.id,
        app_name="YouTube",
        minutes_requested=60,
        status=RequestStatus.pending,
        expires_at=datetime.now(tz=timezone.utc) + timedelta(minutes=30),
    )
    db.add(request)
    await db.commit()
    await db.refresh(request)
    return requester, voter, group, request


def _vote_payload(request_id, voter_telegram_id: int, decision: bool):
    return {
        "request_id": str(request_id),
        "voter_telegram_id": voter_telegram_id,
        "decision": decision,
    }


# ---------------------------------------------------------------------------
# Test: threshold approval
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_vote_threshold_approval(client, db_session):
    """A yes-vote that meets the threshold should resolve the request as approved."""
    from unittest.mock import AsyncMock, patch

    requester, voter, group, request = await _seed(
        db_session,
        requester_id=1000,
        voter_id=1001,
        chat_id=9000,
        vote_threshold=1,
    )

    with patch(
        "app.services.vote_service.bot_service.post_resolution",
        new_callable=AsyncMock,
    ) as mock_resolve:
        mock_resolve.return_value = None

        response = await client.post(
            "/api/v1/votes",
            json=_vote_payload(request.id, voter.telegram_id, True),
        )

    assert response.status_code == 200
    data = response.json()
    assert data["decision"] is True

    # Reload request from DB and check status
    await db_session.refresh(request)
    assert request.status == RequestStatus.approved


@pytest.mark.asyncio
async def test_vote_no_threshold_denial(client, db_session):
    """A no-vote that makes approval mathematically impossible resolves as denied."""
    from unittest.mock import AsyncMock, patch

    # threshold=1, 2 members (requester + voter), so no_count > (2-1)=1 triggers denial
    # With only 2 members: threshold=1. no_count > 2-1=1 means no_count >= 2.
    # Add a third member (extra voter) so we have 3 members total.
    # threshold=2: need 2 yes votes. If no_count > (3-2)=1 => no_count >= 2 triggers denial.
    requester = await create_user(db_session, telegram_id=2000, username="req2000")
    voter1 = await create_user(db_session, telegram_id=2001, username="v2001")
    voter2 = await create_user(db_session, telegram_id=2002, username="v2002")
    group = await create_group(db_session, telegram_chat_id=9100, vote_threshold=2)
    await add_member(db_session, requester, group)
    await add_member(db_session, voter1, group)
    await add_member(db_session, voter2, group)

    request = Request(
        user_id=requester.id,
        group_id=group.id,
        app_name="TikTok",
        minutes_requested=30,
        status=RequestStatus.pending,
        expires_at=datetime.now(tz=timezone.utc) + timedelta(minutes=30),
    )
    db_session.add(request)
    await db_session.commit()
    await db_session.refresh(request)

    with patch(
        "app.services.vote_service.bot_service.post_resolution",
        new_callable=AsyncMock,
    ) as mock_resolve:
        mock_resolve.return_value = None

        # Two no-votes → no_count=2 > (3-2)=1 → denied
        r1 = await client.post(
            "/api/v1/votes",
            json=_vote_payload(request.id, voter1.telegram_id, False),
        )
        assert r1.status_code == 200

        r2 = await client.post(
            "/api/v1/votes",
            json=_vote_payload(request.id, voter2.telegram_id, False),
        )
        assert r2.status_code == 200

    await db_session.refresh(request)
    assert request.status == RequestStatus.denied


# ---------------------------------------------------------------------------
# Test: self-vote block
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_vote_self_vote_blocked(client, db_session):
    """Requester casting a vote on their own request must be rejected with 403."""
    requester, _, group, request = await _seed(
        db_session, requester_id=3000, voter_id=3001, chat_id=9200
    )

    response = await client.post(
        "/api/v1/votes",
        json=_vote_payload(request.id, requester.telegram_id, True),
    )

    assert response.status_code == 403
    assert "own" in response.json()["detail"].lower()


# ---------------------------------------------------------------------------
# Test: duplicate vote / upsert
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_vote_duplicate_updates_decision(client, db_session):
    """A voter who already voted can change their decision (upsert semantics)."""
    from unittest.mock import AsyncMock, patch

    # Use threshold=2 so a single yes-vote from voter does NOT immediately resolve it
    requester = await create_user(db_session, telegram_id=4000, username="req4000")
    voter = await create_user(db_session, telegram_id=4001, username="v4001")
    extra = await create_user(db_session, telegram_id=4002, username="extra4002")
    group = await create_group(db_session, telegram_chat_id=9300, vote_threshold=2)
    await add_member(db_session, requester, group)
    await add_member(db_session, voter, group)
    await add_member(db_session, extra, group)

    request = Request(
        user_id=requester.id,
        group_id=group.id,
        app_name="Reddit",
        minutes_requested=20,
        status=RequestStatus.pending,
        expires_at=datetime.now(tz=timezone.utc) + timedelta(minutes=30),
    )
    db_session.add(request)
    await db_session.commit()
    await db_session.refresh(request)

    with patch(
        "app.services.vote_service.bot_service.post_resolution",
        new_callable=AsyncMock,
    ):
        # First vote: yes
        r1 = await client.post(
            "/api/v1/votes",
            json=_vote_payload(request.id, voter.telegram_id, True),
        )
        assert r1.status_code == 200
        assert r1.json()["decision"] is True

        # Second vote from same voter: no (change of mind)
        r2 = await client.post(
            "/api/v1/votes",
            json=_vote_payload(request.id, voter.telegram_id, False),
        )
        assert r2.status_code == 200
        assert r2.json()["decision"] is False

    # Request should still be pending (not enough votes to resolve either way)
    await db_session.refresh(request)
    assert request.status == RequestStatus.pending


# ---------------------------------------------------------------------------
# Test: vote on expired / non-pending request
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_vote_on_expired_request_blocked(client, db_session):
    """Voting on a request that is no longer pending must return 409."""
    requester = await create_user(db_session, telegram_id=5000, username="req5000")
    voter = await create_user(db_session, telegram_id=5001, username="v5001")
    group = await create_group(db_session, telegram_chat_id=9400, vote_threshold=1)
    await add_member(db_session, requester, group)
    await add_member(db_session, voter, group)

    # Create a request that is already expired
    expired_request = Request(
        user_id=requester.id,
        group_id=group.id,
        app_name="Snapchat",
        minutes_requested=10,
        status=RequestStatus.expired,
        expires_at=datetime.now(tz=timezone.utc) - timedelta(minutes=5),
    )
    db_session.add(expired_request)
    await db_session.commit()
    await db_session.refresh(expired_request)

    response = await client.post(
        "/api/v1/votes",
        json=_vote_payload(expired_request.id, voter.telegram_id, True),
    )

    assert response.status_code == 409
    assert "pending" in response.json()["detail"].lower()


@pytest.mark.asyncio
async def test_vote_on_approved_request_blocked(client, db_session):
    """Voting on an already-approved request must return 409."""
    requester = await create_user(db_session, telegram_id=6000, username="req6000")
    voter = await create_user(db_session, telegram_id=6001, username="v6001")
    group = await create_group(db_session, telegram_chat_id=9500, vote_threshold=1)
    await add_member(db_session, requester, group)
    await add_member(db_session, voter, group)

    approved_request = Request(
        user_id=requester.id,
        group_id=group.id,
        app_name="Netflix",
        minutes_requested=60,
        status=RequestStatus.approved,
        expires_at=datetime.now(tz=timezone.utc) + timedelta(minutes=10),
    )
    db_session.add(approved_request)
    await db_session.commit()
    await db_session.refresh(approved_request)

    response = await client.post(
        "/api/v1/votes",
        json=_vote_payload(approved_request.id, voter.telegram_id, False),
    )

    assert response.status_code == 409


@pytest.mark.asyncio
async def test_vote_unknown_request(client, db_session):
    """Voting on a non-existent request should return 404."""
    import uuid

    voter = await create_user(db_session, telegram_id=7000, username="v7000")
    await db_session.commit()

    response = await client.post(
        "/api/v1/votes",
        json=_vote_payload(uuid.uuid4(), voter.telegram_id, True),
    )

    assert response.status_code == 404


@pytest.mark.asyncio
async def test_vote_non_member_blocked(client, db_session):
    """A user who is not a member of the group cannot vote → 403."""
    requester, _, group, request = await _seed(
        db_session, requester_id=8000, voter_id=8001, chat_id=9600
    )
    outsider = await create_user(db_session, telegram_id=8002, username="outsider")
    await db_session.commit()

    response = await client.post(
        "/api/v1/votes",
        json=_vote_payload(request.id, outsider.telegram_id, True),
    )

    assert response.status_code == 403
