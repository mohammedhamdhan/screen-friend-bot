from datetime import date

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.models import Checkin, Group, Membership, User
from app.models.screen_time_log import ScreenTimeLog
from app.schemas.screen_time import ScreenTimeSubmitRequest
from app.services.leaderboard_service import upsert_leaderboard

router = APIRouter(prefix="/screen-time", tags=["screen-time"])


@router.post("", status_code=status.HTTP_201_CREATED)
async def submit_screen_time(
    payload: ScreenTimeSubmitRequest, db: AsyncSession = Depends(get_db)
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

    today = date.today()

    # Check for existing check-in
    existing = await db.execute(
        select(Checkin).where(Checkin.user_id == user.id, Checkin.date == today)
    )
    if existing.scalar_one_or_none():
        raise HTTPException(status_code=409, detail="Already checked in today")

    # Create check-in
    checkin = Checkin(
        user_id=user.id,
        date=today,
        stayed_clean=payload.stayed_clean,
        ocr_source=True,
    )
    db.add(checkin)

    # Create screen time log entries
    for app in payload.apps:
        log = ScreenTimeLog(
            user_id=user.id,
            group_id=group.id,
            date=today,
            app_name=app.app_name,
            minutes_used=app.minutes,
            screenshot_url=payload.screenshot_url,
        )
        db.add(log)

    # Update streak and leaderboard
    if payload.stayed_clean:
        user.streak += 1
        memberships_result = await db.execute(
            select(Membership).where(Membership.user_id == user.id)
        )
        for m in memberships_result.scalars().all():
            await upsert_leaderboard(
                user_id=user.id, group_id=m.group_id, field="clean_days", db=db
            )
    else:
        user.streak = 0

    await db.commit()
    await db.refresh(checkin)

    return {
        "id": str(checkin.id),
        "date": str(checkin.date),
        "stayed_clean": checkin.stayed_clean,
        "ocr_source": checkin.ocr_source,
        "streak": user.streak,
        "violations": payload.violations,
    }
