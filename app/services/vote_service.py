"""
Vote resolution service.

Checks current vote counts against a group's threshold and resolves
a pending request as approved or denied when enough votes exist.
"""

import json
import logging
import uuid
from datetime import datetime, timezone, timedelta

import redis.asyncio as aioredis
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.models import Group, Membership, Request, RequestStatus, User, Vote
from app.services import bot_service
from app.services import leaderboard_service

logger = logging.getLogger(__name__)


async def check_and_resolve(request_id: uuid.UUID, db: AsyncSession) -> None:
    """Evaluate vote counts and resolve a request if the outcome is determined.

    Resolution rules
    ----------------
    - yes_votes >= threshold               → approved
    - no_votes > (total_members - threshold) → denied (cannot mathematically pass)

    After resolution the leaderboard is updated:
    - requests_made is always incremented.
    - requests_denied is incremented only on denial.

    Parameters
    ----------
    request_id:
        UUID of the Request row to evaluate.
    db:
        Active async database session.
    """
    # Fetch the request with its group
    request_result = await db.execute(
        select(Request).where(Request.id == request_id)
    )
    request = request_result.scalar_one_or_none()

    if request is None:
        logger.warning("check_and_resolve: request_id=%s not found", request_id)
        return

    if request.status != RequestStatus.pending:
        # Already resolved — nothing to do
        return

    group_result = await db.execute(
        select(Group).where(Group.id == request.group_id)
    )
    group = group_result.scalar_one_or_none()

    if group is None:
        logger.error(
            "check_and_resolve: group_id=%s not found for request_id=%s",
            request.group_id,
            request_id,
        )
        return

    threshold = group.vote_threshold

    # Count total group members
    total_members_result = await db.execute(
        select(func.count()).where(Membership.group_id == group.id)
    )
    total_members: int = total_members_result.scalar_one()

    # Count yes and no votes for this request
    yes_count_result = await db.execute(
        select(func.count()).where(
            Vote.request_id == request_id,
            Vote.decision.is_(True),
        )
    )
    yes_count: int = yes_count_result.scalar_one()

    no_count_result = await db.execute(
        select(func.count()).where(
            Vote.request_id == request_id,
            Vote.decision.is_(False),
        )
    )
    no_count: int = no_count_result.scalar_one()

    # Determine outcome
    new_status: RequestStatus | None = None

    if yes_count >= threshold:
        new_status = RequestStatus.approved
    elif no_count > (total_members - threshold):
        # Mathematically impossible to reach threshold now
        new_status = RequestStatus.denied

    if new_status is None:
        # Vote still open — no resolution yet
        return

    # Persist the new status
    request.status = new_status
    await db.flush()

    logger.info(
        "check_and_resolve: request_id=%s resolved as %s (yes=%d, no=%d, threshold=%d, members=%d)",
        request_id,
        new_status.value,
        yes_count,
        no_count,
        threshold,
        total_members,
    )

    # Fire-and-forget: post the resolution to the Telegram group
    requester_result = await db.execute(
        select(User).where(User.id == request.user_id)
    )
    requester = requester_result.scalar_one_or_none()

    await bot_service.post_resolution(
        group_chat_id=group.telegram_chat_id,
        request_id=request.id,
        status=new_status.value,
        message_id=request.telegram_message_id,
        requester_username=requester.username if requester else None,
        app_name=request.app_name,
    )

    # If approved, store temporary bonus minutes in Redis (expires end of day UTC)
    if new_status == RequestStatus.approved and requester is not None:
        await _store_daily_bonus(
            requester.telegram_id, request.app_name, request.minutes_requested
        )

    # Update leaderboard — requests_made always incremented
    await leaderboard_service.upsert_leaderboard(
        user_id=request.user_id,
        group_id=request.group_id,
        field="requests_made",
        db=db,
    )

    if new_status == RequestStatus.denied:
        await leaderboard_service.upsert_leaderboard(
            user_id=request.user_id,
            group_id=request.group_id,
            field="requests_denied",
            db=db,
        )


async def _store_daily_bonus(
    telegram_id: int, app_name: str, bonus_minutes: int
) -> None:
    """Store approved bonus minutes in Redis, expiring at end of day UTC.

    Key format: screengate:bonus:{telegram_id}:{app_name_lower}
    Value: JSON with bonus_minutes (accumulates if multiple requests approved).
    """
    settings = get_settings()
    r = aioredis.from_url(settings.REDIS_URL, decode_responses=True)
    try:
        key = f"screengate:bonus:{telegram_id}:{app_name.lower()}"

        # Accumulate if there's already a bonus for this app today
        existing = await r.get(key)
        if existing:
            data = json.loads(existing)
            bonus_minutes += data.get("bonus_minutes", 0)

        # TTL = seconds until end of day UTC
        now = datetime.now(tz=timezone.utc)
        end_of_day = datetime.combine(
            now.date() + timedelta(days=1),
            datetime.min.time(),
        ).replace(tzinfo=timezone.utc)
        ttl = int((end_of_day - now).total_seconds())

        await r.setex(key, ttl, json.dumps({"bonus_minutes": bonus_minutes}))
        logger.info(
            "_store_daily_bonus: telegram_id=%s app=%s bonus=%d ttl=%ds",
            telegram_id, app_name, bonus_minutes, ttl,
        )
    finally:
        await r.aclose()
