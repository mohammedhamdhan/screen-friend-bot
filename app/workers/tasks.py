"""
Celery tasks for ScreenGate.

All tasks are idempotent — each one checks current state before performing
any side-effectful action.

Celery workers are synchronous; async service calls are wrapped with
asyncio.run().
"""

import asyncio
import logging
import uuid
from datetime import date, timezone

from sqlalchemy import select

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
    """DM every user who has not submitted a check-in for today.

    The message includes inline buttons so the user can respond directly:
      [Clean] — they stayed clean today
      [Slipped] — they did not
    """
    async def _inner():
        today = date.today()

        async with AsyncSessionLocal() as db:
            # Users who already have a check-in today
            checked_in_result = await db.execute(
                select(Checkin.user_id).where(Checkin.date == today)
            )
            checked_in_ids = {row[0] for row in checked_in_result.all()}

            # All users that belong to at least one group (active members)
            all_members_result = await db.execute(
                select(User).join(Membership, Membership.user_id == User.id).distinct()
            )
            all_users = all_members_result.scalars().all()

            pending_users = [u for u in all_users if u.id not in checked_in_ids]

            logger.info(
                "send_daily_checkins: %d users need a check-in for %s",
                len(pending_users),
                today,
            )

            for user in pending_users:
                text = (
                    "Good day! Time for your daily check-in.\n\n"
                    "Did you stay clean today?\n\n"
                    'Tap <b>Clean</b> if you held it together, or '
                    '<b>Slipped</b> if you did not.'
                )
                # Send DM with inline keyboard
                payload = {
                    "chat_id": user.telegram_id,
                    "text": text,
                    "parse_mode": "HTML",
                    "reply_markup": {
                        "inline_keyboard": [
                            [
                                {
                                    "text": "✅ Clean",
                                    "callback_data": f"checkin:{user.id}:clean",
                                },
                                {
                                    "text": "😔 Slipped",
                                    "callback_data": f"checkin:{user.id}:slipped",
                                },
                            ]
                        ]
                    },
                }
                import httpx
                from app.config import get_settings
                token = get_settings().TELEGRAM_BOT_TOKEN
                base_url = f"https://api.telegram.org/bot{token}"
                try:
                    async with httpx.AsyncClient(timeout=15.0) as client:
                        resp = await client.post(f"{base_url}/sendMessage", json=payload)
                        resp.raise_for_status()
                        logger.info(
                            "send_daily_checkins: sent reminder to telegram_id=%s",
                            user.telegram_id,
                        )
                except Exception as exc:
                    logger.error(
                        "send_daily_checkins: failed for telegram_id=%s: %s",
                        user.telegram_id,
                        exc,
                    )

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
