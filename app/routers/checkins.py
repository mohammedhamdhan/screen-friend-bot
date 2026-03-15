from datetime import date

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.models import Checkin, Membership, User
from app.schemas.checkin import CheckinCreateRequest

router = APIRouter(prefix="/checkins", tags=["checkins"])


@router.post("", status_code=status.HTTP_201_CREATED)
async def create_checkin(payload: CheckinCreateRequest, db: AsyncSession = Depends(get_db)):
    user_result = await db.execute(select(User).where(User.telegram_id == payload.telegram_id))
    user = user_result.scalar_one_or_none()
    if not user:
        raise HTTPException(status_code=404, detail="User not found")

    today = date.today()
    existing = await db.execute(
        select(Checkin).where(Checkin.user_id == user.id, Checkin.date == today)
    )
    if existing.scalar_one_or_none():
        raise HTTPException(status_code=409, detail="Already checked in today")

    checkin = Checkin(
        user_id=user.id,
        date=today,
        stayed_clean=payload.stayed_clean,
        confession_note=payload.confession_note,
    )
    db.add(checkin)

    if payload.stayed_clean:
        user.streak += 1
        # Update leaderboard for all groups user is in
        from app.services.leaderboard_service import upsert_leaderboard

        memberships_result = await db.execute(select(Membership).where(Membership.user_id == user.id))
        for m in memberships_result.scalars().all():
            await upsert_leaderboard(user_id=user.id, group_id=m.group_id, field="clean_days", db=db)
    else:
        user.streak = 0

    await db.commit()
    await db.refresh(checkin)
    return {
        "id": str(checkin.id),
        "date": str(checkin.date),
        "stayed_clean": checkin.stayed_clean,
        "confession_note": checkin.confession_note,
        "streak": user.streak,
    }


@router.get("/{telegram_id}")
async def get_checkins(telegram_id: int, db: AsyncSession = Depends(get_db)):
    user_result = await db.execute(select(User).where(User.telegram_id == telegram_id))
    user = user_result.scalar_one_or_none()
    if not user:
        raise HTTPException(status_code=404, detail="User not found")

    result = await db.execute(
        select(Checkin).where(Checkin.user_id == user.id).order_by(Checkin.date.desc()).limit(10)
    )
    checkins = result.scalars().all()
    return [
        {
            "date": str(c.date),
            "stayed_clean": c.stayed_clean,
            "confession_note": c.confession_note,
        }
        for c in checkins
    ]
