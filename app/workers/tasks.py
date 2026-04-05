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
from datetime import date, datetime, timezone

from sqlalchemy import select

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.config import get_settings
from app.models import Checkin, Group, Membership, Request, RequestStatus, ScreenTimeLog, User
from app.models.weekly_checkin import WeeklyCheckin
from app.models.weekly_screen_time_log import WeeklyScreenTimeLog
from app.services import bot_service, leaderboard_service
from app.workers.celery_app import celery_app

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_session_factory() -> async_sessionmaker[AsyncSession]:
    """Create a fresh async engine + session factory for this event loop.

    Celery tasks call asyncio.run() which creates a new event loop each time.
    asyncpg connections are bound to the event loop they were created on, so
    reusing the FastAPI app's shared engine causes 'another operation is in
    progress' errors.  Creating a fresh engine here avoids that.
    """
    settings = get_settings()
    engine = create_async_engine(settings.DATABASE_URL)
    return async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)


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
        async with _make_session_factory()() as db:
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

@celery_app.task(name="app.workers.tasks.send_daily_checkins", bind=True, max_retries=3)
def send_daily_checkins(self) -> None:
    """Send screenshot collection prompts to each group for unchecked-in members.

    For each group, posts a message asking pending users to send screen time
    screenshots. Stores collection state in Redis and schedules a timeout task.
    """
    async def _inner():
        import redis.asyncio as aioredis

        settings = get_settings()
        now = datetime.now(timezone.utc)
        today = now.date()
        current_hour = now.hour
        current_minute = now.minute
        timeout_seconds = settings.SCREENSHOT_COLLECTION_TIMEOUT_MINUTES * 60

        async with _make_session_factory()() as db:
            # Users who already have a check-in today
            checked_in_result = await db.execute(
                select(Checkin.user_id).where(Checkin.date == today)
            )
            checked_in_ids = {row[0] for row in checked_in_result.all()}

            # Get groups whose check-in time matches the current UTC hour:minute.
            # Groups without a custom time fall back to the global default (minute=0).
            groups_result = await db.execute(select(Group))
            all_groups = groups_result.scalars().all()
            default_hour = settings.CHECKIN_TIME_UTC
            for g in all_groups:
                g_hour = g.checkin_time_utc if g.checkin_time_utc is not None else default_hour
                g_min = g.checkin_minute_utc or 0
                logger.info(
                    "send_daily_checkins: group %s scheduled at %02d:%02d UTC, now is %02d:%02d UTC",
                    g.telegram_chat_id, g_hour, g_min, current_hour, current_minute,
                )
            groups = [
                g for g in all_groups
                if (g.checkin_time_utc if g.checkin_time_utc is not None else default_hour) == current_hour
                and (g.checkin_minute_utc or 0) == current_minute
            ]

            if not groups:
                logger.info(
                    "send_daily_checkins: no groups matched for %02d:%02d UTC (%d groups exist)",
                    current_hour, current_minute, len(all_groups),
                )
                return

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
                        "started_at": datetime.now(timezone.utc).isoformat(),
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

    try:
        _run(_inner())
    except Exception as exc:
        logger.error("send_daily_checkins failed: %s", exc)
        raise self.retry(exc=exc, countdown=60)


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
            async with _make_session_factory()() as db:
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
# Task: send weekly check-in reminders
# ---------------------------------------------------------------------------

@celery_app.task(name="app.workers.tasks.send_weekly_checkins")
def send_weekly_checkins() -> None:
    """Send weekly screenshot collection prompts to each group."""
    async def _inner():
        import redis.asyncio as aioredis
        from datetime import timedelta

        settings = get_settings()
        today = datetime.now(timezone.utc).date()
        week_start = today - timedelta(days=today.weekday())  # Monday
        timeout_seconds = settings.WEEKLY_COLLECTION_TIMEOUT_MINUTES * 60

        async with _make_session_factory()() as db:
            # Users who already have a weekly check-in this week
            checked_in_result = await db.execute(
                select(WeeklyCheckin.user_id).where(WeeklyCheckin.week_start == week_start)
            )
            checked_in_ids = {row[0] for row in checked_in_result.all()}

            groups_result = await db.execute(select(Group))
            groups = groups_result.scalars().all()

            for group in groups:
                members_result = await db.execute(
                    select(User)
                    .join(Membership, Membership.user_id == User.id)
                    .where(Membership.group_id == group.id)
                )
                members = members_result.scalars().all()
                pending = [u for u in members if u.id not in checked_in_ids]

                if not pending:
                    logger.info(
                        "send_weekly_checkins: all members submitted weekly for group %s",
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
                        mention_parts.append(
                            f'<a href="tg://user?id={u.telegram_id}">{u.telegram_id}</a>'
                        )
                    pending_telegram_ids.append(u.telegram_id)

                mention_text = " ".join(mention_parts)

                message_id = await bot_service.post_weekly_screenshot_request(
                    group_chat_id=group.telegram_chat_id,
                    mention_text=mention_text,
                )

                # Store weekly collection state in Redis
                r = aioredis.from_url(settings.REDIS_URL, decode_responses=True)
                try:
                    redis_key = f"screengate:weekly_collection:{group.telegram_chat_id}"
                    state = {
                        "pending_users": pending_telegram_ids,
                        "message_id": message_id,
                        "started_at": datetime.now(timezone.utc).isoformat(),
                    }
                    await r.setex(redis_key, timeout_seconds, json.dumps(state))
                finally:
                    await r.aclose()

                # Schedule timeout task
                close_weekly_screenshot_collection.apply_async(
                    args=[group.telegram_chat_id],
                    countdown=timeout_seconds,
                )

                logger.info(
                    "send_weekly_checkins: sent weekly screenshot request to group %s "
                    "for %d users, timeout=%ds",
                    group.telegram_chat_id,
                    len(pending),
                    timeout_seconds,
                )

    _run(_inner())


@celery_app.task(name="app.workers.tasks.close_weekly_screenshot_collection")
def close_weekly_screenshot_collection(group_telegram_chat_id: int) -> None:
    """Send manual fallback buttons for users who didn't submit weekly screenshots."""
    async def _inner():
        import redis.asyncio as aioredis

        settings = get_settings()
        r = aioredis.from_url(settings.REDIS_URL, decode_responses=True)

        try:
            redis_key = f"screengate:weekly_collection:{group_telegram_chat_id}"
            raw = await r.get(redis_key)

            if not raw:
                logger.info(
                    "close_weekly_screenshot_collection: no pending collection for %s",
                    group_telegram_chat_id,
                )
                return

            state = json.loads(raw)
            pending_users = state.get("pending_users", [])

            if not pending_users:
                await r.delete(redis_key)
                return

            async with _make_session_factory()() as db:
                for telegram_id in pending_users:
                    user_result = await db.execute(
                        select(User).where(User.telegram_id == telegram_id)
                    )
                    user = user_result.scalar_one_or_none()
                    username = user.username if user and user.username else str(telegram_id)

                    await bot_service.post_weekly_manual_fallback(
                        group_chat_id=group_telegram_chat_id,
                        user_telegram_id=telegram_id,
                        username=username,
                    )

                    logger.info(
                        "close_weekly_screenshot_collection: sent fallback for user %s in group %s",
                        telegram_id,
                        group_telegram_chat_id,
                    )

            await r.delete(redis_key)

        finally:
            await r.aclose()

    _run(_inner())


# ---------------------------------------------------------------------------
# Task: run weekly collation
# ---------------------------------------------------------------------------

@celery_app.task(name="app.workers.tasks.run_weekly_collation")
def run_weekly_collation() -> None:
    """Compare weekly screenshot totals against daily check-in sums for all users."""
    async def _inner():
        from datetime import timedelta
        from sqlalchemy import func as sa_func

        settings = get_settings()
        today = datetime.now(timezone.utc).date()
        week_start = today - timedelta(days=today.weekday())  # Monday
        week_end = week_start + timedelta(days=6)  # Sunday
        tolerance = settings.WEEKLY_TOLERANCE_MINUTES

        async with _make_session_factory()() as db:
            # Only process weekly check-ins that haven't been compared yet
            # (daily_sum_minutes == 0 means inline comparison didn't run)
            checkins_result = await db.execute(
                select(WeeklyCheckin).where(
                    WeeklyCheckin.week_start == week_start,
                    WeeklyCheckin.daily_sum_minutes == 0,
                )
            )
            weekly_checkins = checkins_result.scalars().all()

            if not weekly_checkins:
                logger.info("run_weekly_collation: no unprocessed weekly check-ins for week %s", week_start)
                return

            for wc in weekly_checkins:
                try:
                    # Get user
                    user_result = await db.execute(
                        select(User).where(User.id == wc.user_id)
                    )
                    user = user_result.scalar_one_or_none()
                    if not user:
                        continue

                    # Sum daily ScreenTimeLog entries for Mon-Sun
                    daily_result = await db.execute(
                        select(
                            ScreenTimeLog.app_name,
                            sa_func.sum(ScreenTimeLog.minutes_used).label("total_minutes"),
                        )
                        .where(
                            ScreenTimeLog.user_id == wc.user_id,
                            ScreenTimeLog.date >= week_start,
                            ScreenTimeLog.date <= week_end,
                        )
                        .group_by(ScreenTimeLog.app_name)
                    )
                    daily_rows = daily_result.all()
                    daily_sum_by_app = {row[0]: row[1] for row in daily_rows}
                    daily_total = sum(daily_sum_by_app.values()) if daily_sum_by_app else 0

                    # Get weekly screen time log entries
                    weekly_logs_result = await db.execute(
                        select(WeeklyScreenTimeLog).where(
                            WeeklyScreenTimeLog.user_id == wc.user_id,
                            WeeklyScreenTimeLog.week_start == week_start,
                        )
                    )
                    weekly_logs = weekly_logs_result.scalars().all()
                    weekly_apps = [
                        {"app_name": wl.app_name, "minutes": wl.minutes_used}
                        for wl in weekly_logs
                    ]

                    weekly_total = wc.weekly_total_minutes
                    discrepancy = weekly_total - daily_total
                    passed = discrepancy <= tolerance

                    # Update weekly check-in record
                    wc.daily_sum_minutes = daily_total
                    wc.discrepancy_minutes = max(discrepancy, 0)
                    wc.passed = passed

                    # Find a group this user belongs to for messaging
                    membership_result = await db.execute(
                        select(Membership).where(Membership.user_id == user.id)
                    )
                    membership = membership_result.scalars().first()

                    if not passed:
                        # Reset streak
                        user.streak = 0

                        # DM encouraging message
                        dm_text = (
                            f"📊 <b>Weekly Check-in Result</b>\n\n"
                            f"Your weekly screen time was <b>{weekly_total} min</b>, "
                            f"but your daily check-ins only totalled <b>{daily_total} min</b>.\n"
                            f"That's <b>{max(discrepancy, 0)} extra minutes</b> of usage "
                            f"after your daily check-ins.\n\n"
                            f"Your streak has been reset — but nobody succeeds at first! "
                            f"Try setting smaller goals and building up, "
                            f"or consider changing your check-in time to later in the day. "
                            f"You've got this! 💪"
                        )
                        await bot_service.dm_user(user.telegram_id, dm_text)

                    # Post result to group
                    if membership:
                        group_result = await db.execute(
                            select(Group).where(Group.id == membership.group_id)
                        )
                        group = group_result.scalar_one_or_none()
                        if group:
                            username = user.username or str(user.telegram_id)
                            await bot_service.post_weekly_collation_result(
                                group_chat_id=group.telegram_chat_id,
                                username=username,
                                passed=passed,
                                discrepancy_minutes=max(discrepancy, 0),
                            )

                    logger.info(
                        "run_weekly_collation: user=%s passed=%s discrepancy=%d",
                        user.telegram_id,
                        passed,
                        max(discrepancy, 0),
                    )

                except Exception as exc:
                    logger.error(
                        "run_weekly_collation: failed for user_id=%s: %s",
                        wc.user_id,
                        exc,
                    )

            await db.commit()

    _run(_inner())


# ---------------------------------------------------------------------------
# Task: send weekly leaderboard
# ---------------------------------------------------------------------------

@celery_app.task(name="app.workers.tasks.send_weekly_leaderboard", bind=True, max_retries=3)
def send_weekly_leaderboard(self) -> None:
    """Fetch and post the weekly leaderboard for every group."""

    async def _inner():
        async with _make_session_factory()() as db:
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

    try:
        _run(_inner())
    except Exception as exc:
        logger.error("send_weekly_leaderboard failed: %s", exc)
        raise self.retry(exc=exc, countdown=60)
