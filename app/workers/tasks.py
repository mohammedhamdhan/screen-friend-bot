"""
Celery tasks for ScreenGate.

All tasks are idempotent — each one checks current state before performing
any side-effectful action.

Celery workers are synchronous; async service calls are wrapped with
asyncio.run().
"""

import asyncio
import json
import logging
import uuid
from datetime import date, timezone

from sqlalchemy import select

from app.config import get_settings
from app.database import AsyncSessionLocal
from app.models import Checkin, Group, Membership, Request, RequestStatus, User
from app.services import bot_service, leaderboard_service
from app.workers.celery_app import celery_app

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _run(coro):
    """Run an async coroutine from synchronous Celery task code."""
    return asyncio.run(coro)


# ---------------------------------------------------------------------------
# Task: expire a single request
# ---------------------------------------------------------------------------

@celery_app.task(name="app.workers.tasks.expire_request", bind=True, max_retries=3)
def expire_request(self, request_id: str) -> None:
    """Expire a pending request and notify the requester.

    The task is idempotent: if the request is no longer in *pending* state
    (e.g. it was already approved or denied) nothing is done.

    Parameters
    ----------
    request_id:
        String representation of the Request UUID.
    """
    async def _inner():
        rid = uuid.UUID(request_id)
        async with AsyncSessionLocal() as db:
            result = await db.execute(
                select(Request).where(Request.id == rid)
            )
            req = result.scalar_one_or_none()

            if req is None:
                logger.warning("expire_request: request_id=%s not found", request_id)
                return

            if req.status != RequestStatus.pending:
                logger.info(
                    "expire_request: request_id=%s already in status=%s, skipping",
                    request_id,
                    req.status,
                )
                return

            # Fetch related user for notification
            user_result = await db.execute(
                select(User).where(User.id == req.user_id)
            )
            user = user_result.scalar_one_or_none()

            req.status = RequestStatus.expired
            await db.commit()
            logger.info("expire_request: set request_id=%s to expired", request_id)

            # Notify requester via DM
            if user is not None:
                text = (
                    f"Your request for <b>{req.app_name}</b> has expired "
                    f"(no response within the allowed time)."
                )
                await bot_service.dm_user(user.telegram_id, text)

            # Update the group vote message if one exists
            group_result = await db.execute(
                select(Group).where(Group.id == req.group_id)
            )
            group = group_result.scalar_one_or_none()
            if group is not None:
                await bot_service.post_resolution(
                    group_chat_id=group.telegram_chat_id,
                    request_id=str(req.id),
                    status="expired",
                    message_id=req.telegram_message_id,
                    requester_username=user.username if user else None,
                    app_name=req.app_name,
                )

    try:
        _run(_inner())
    except Exception as exc:
        logger.error("expire_request failed for request_id=%s: %s", request_id, exc)
        raise self.retry(exc=exc, countdown=60)


# ---------------------------------------------------------------------------
# Task: send daily check-in reminders
# ---------------------------------------------------------------------------

@celery_app.task(name="app.workers.tasks.send_daily_checkins")
def send_daily_checkins() -> None:
    """Send screenshot collection prompts to each group for unchecked-in members.

    For each group, posts a message asking pending users to send screen time
    screenshots. Stores collection state in Redis and schedules a timeout task.
    """
    async def _inner():
        import redis.asyncio as aioredis
        from datetime import datetime, timezone as tz

        settings = get_settings()
        today = date.today()
        timeout_seconds = settings.SCREENSHOT_COLLECTION_TIMEOUT_MINUTES * 60

        async with AsyncSessionLocal() as db:
            # Users who already have a check-in today
            checked_in_result = await db.execute(
                select(Checkin.user_id).where(Checkin.date == today)
            )
            checked_in_ids = {row[0] for row in checked_in_result.all()}

            # Get all groups with their members
            groups_result = await db.execute(select(Group))
            groups = groups_result.scalars().all()

            for group in groups:
                # Get members of this group who haven't checked in
                members_result = await db.execute(
                    select(User)
                    .join(Membership, Membership.user_id == User.id)
                    .where(Membership.group_id == group.id)
                )
                members = members_result.scalars().all()
                pending = [u for u in members if u.id not in checked_in_ids]

                if not pending:
                    logger.info(
                        "send_daily_checkins: all members checked in for group %s",
                        group.telegram_chat_id,
                    )
                    continue

                # Build mention list
                mention_parts = []
                pending_telegram_ids = []
                for u in pending:
                    if u.username:
                        mention_parts.append(f"@{u.username}")
                    else:
                        mention_parts.append(f'<a href="tg://user?id={u.telegram_id}">{u.telegram_id}</a>')
                    pending_telegram_ids.append(u.telegram_id)

                mention_text = " ".join(mention_parts)

                # Send prompt to group
                message_id = await bot_service.post_screenshot_request(
                    group_chat_id=group.telegram_chat_id,
                    mention_text=mention_text,
                )

                # Store collection state in Redis
                r = aioredis.from_url(settings.REDIS_URL, decode_responses=True)
                try:
                    redis_key = f"screengate:collection:{group.telegram_chat_id}"
                    state = {
                        "pending_users": pending_telegram_ids,
                        "message_id": message_id,
                        "started_at": datetime.now(tz.utc).isoformat(),
                    }
                    await r.setex(redis_key, timeout_seconds, json.dumps(state))
                finally:
                    await r.aclose()

                # Schedule timeout task
                close_screenshot_collection.apply_async(
                    args=[group.telegram_chat_id],
                    countdown=timeout_seconds,
                )

                logger.info(
                    "send_daily_checkins: sent screenshot request to group %s "
                    "for %d users, timeout=%ds",
                    group.telegram_chat_id,
                    len(pending),
                    timeout_seconds,
                )

    _run(_inner())


@celery_app.task(name="app.workers.tasks.close_screenshot_collection")
def close_screenshot_collection(group_telegram_chat_id: int) -> None:
    """Send manual fallback buttons for users who didn't submit screenshots."""
    async def _inner():
        import redis.asyncio as aioredis

        settings = get_settings()
        r = aioredis.from_url(settings.REDIS_URL, decode_responses=True)

        try:
            redis_key = f"screengate:collection:{group_telegram_chat_id}"
            raw = await r.get(redis_key)

            if not raw:
                logger.info(
                    "close_screenshot_collection: no pending collection for %s",
                    group_telegram_chat_id,
                )
                return

            state = json.loads(raw)
            pending_users = state.get("pending_users", [])

            if not pending_users:
                await r.delete(redis_key)
                return

            # Look up usernames for each pending user
            async with AsyncSessionLocal() as db:
                for telegram_id in pending_users:
                    user_result = await db.execute(
                        select(User).where(User.telegram_id == telegram_id)
                    )
                    user = user_result.scalar_one_or_none()
                    username = user.username if user and user.username else str(telegram_id)

                    await bot_service.post_manual_fallback(
                        group_chat_id=group_telegram_chat_id,
                        user_telegram_id=telegram_id,
                        username=username,
                    )

                    logger.info(
                        "close_screenshot_collection: sent fallback for user %s in group %s",
                        telegram_id,
                        group_telegram_chat_id,
                    )

            # Clean up Redis
            await r.delete(redis_key)

        finally:
            await r.aclose()

    _run(_inner())


# ---------------------------------------------------------------------------
# Task: send weekly leaderboard
# ---------------------------------------------------------------------------

@celery_app.task(name="app.workers.tasks.send_weekly_leaderboard")
def send_weekly_leaderboard() -> None:
    """Fetch and post the weekly leaderboard for every group."""

    async def _inner():
        async with AsyncSessionLocal() as db:
            groups_result = await db.execute(select(Group))
            groups = groups_result.scalars().all()

            logger.info(
                "send_weekly_leaderboard: posting leaderboard to %d groups",
                len(groups),
            )

            for group in groups:
                try:
                    rows = await leaderboard_service.get_weekly_leaderboard(
                        group_id=group.id, db=db
                    )
                    message = leaderboard_service.format_leaderboard_message(rows)
                    await bot_service.post_leaderboard(
                        group_chat_id=group.telegram_chat_id,
                        message=message,
                    )
                    logger.info(
                        "send_weekly_leaderboard: posted to group_id=%s chat_id=%s",
                        group.id,
                        group.telegram_chat_id,
                    )
                except Exception as exc:
                    logger.error(
                        "send_weekly_leaderboard: failed for group_id=%s: %s",
                        group.id,
                        exc,
                    )

    _run(_inner())
