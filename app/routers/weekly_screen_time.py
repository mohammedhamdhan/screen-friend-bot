from datetime import date, timedelta

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import func as sa_func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.database import get_db
from app.models import Group, Membership, ScreenTimeLog, User
from app.models.weekly_checkin import WeeklyCheckin
from app.models.weekly_screen_time_log import WeeklyScreenTimeLog
from app.schemas.weekly_screen_time import WeeklyScreenTimeSubmitRequest

router = APIRouter(prefix="/weekly-screen-time", tags=["weekly-screen-time"])


def _monday_of_week(d: date) -> date:
    """Return the Monday of the ISO week containing *d*."""
    return d - timedelta(days=d.weekday())


@router.post("", status_code=status.HTTP_201_CREATED)
async def submit_weekly_screen_time(
    payload: WeeklyScreenTimeSubmitRequest, db: AsyncSession = Depends(get_db)
):
    # Look up user
    user_result = await db.execute(
        select(User).where(User.telegram_id == payload.telegram_id)
    )
    user = user_result.scalar_one_or_none()
    if not user:
        raise HTTPException(status_code=404, detail="User not found")

    # Look up group
    group_result = await db.execute(
        select(Group).where(Group.telegram_chat_id == payload.group_telegram_chat_id)
    )
    group = group_result.scalar_one_or_none()
    if not group:
        raise HTTPException(status_code=404, detail="Group not found")

    week_start = _monday_of_week(date.today())

    # Check for existing weekly check-in
    existing = await db.execute(
        select(WeeklyCheckin).where(
            WeeklyCheckin.user_id == user.id,
            WeeklyCheckin.week_start == week_start,
        )
    )
    if existing.scalar_one_or_none():
        raise HTTPException(status_code=409, detail="Already submitted weekly check-in this week")

    settings = get_settings()
    weekly_total = sum(a.minutes for a in payload.apps)
    week_end = week_start + timedelta(days=6)
    tolerance = settings.WEEKLY_TOLERANCE_MINUTES

    # Sum daily ScreenTimeLog entries for Mon-Sun
    daily_result = await db.execute(
        select(sa_func.coalesce(sa_func.sum(ScreenTimeLog.minutes_used), 0))
        .where(
            ScreenTimeLog.user_id == user.id,
            ScreenTimeLog.date >= week_start,
            ScreenTimeLog.date <= week_end,
        )
    )
    daily_total = daily_result.scalar() or 0

    discrepancy = max(weekly_total - daily_total, 0)
    passed = discrepancy <= tolerance

    # Create weekly check-in with comparison already computed
    checkin = WeeklyCheckin(
        user_id=user.id,
        week_start=week_start,
        weekly_total_minutes=weekly_total,
        daily_sum_minutes=daily_total,
        discrepancy_minutes=discrepancy,
        passed=passed,
        ocr_source="screenshot",
    )
    db.add(checkin)

    # Create weekly screen time log entries
    for app in payload.apps:
        log = WeeklyScreenTimeLog(
            user_id=user.id,
            group_id=group.id,
            week_start=week_start,
            app_name=app.app_name,
            minutes_used=app.minutes,
            screenshot_url=payload.screenshot_url,
        )
        db.add(log)

    # Reset streak if failed
    if not passed:
        user.streak = 0

    await db.commit()
    await db.refresh(checkin)

    return {
        "id": str(checkin.id),
        "week_start": str(checkin.week_start),
        "weekly_total_minutes": checkin.weekly_total_minutes,
        "daily_sum_minutes": daily_total,
        "discrepancy_minutes": discrepancy,
        "passed": passed,
    }
