from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.models.app_limit import AppLimit
from app.models.user import User
from app.schemas.limit import LimitResponse, LimitUpsertRequest

router = APIRouter(prefix="/limits", tags=["limits"])


@router.post("", response_model=LimitResponse, status_code=status.HTTP_200_OK)
async def upsert_limit(payload: LimitUpsertRequest, db: AsyncSession = Depends(get_db)):
    # Resolve user by telegram_id
    result = await db.execute(
        select(User).where(User.telegram_id == payload.telegram_id)
    )
    user = result.scalar_one_or_none()
    if user is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"User with telegram_id {payload.telegram_id} not found.",
        )

    # Upsert the app limit
    result = await db.execute(
        select(AppLimit).where(
            AppLimit.user_id == user.id,
            AppLimit.app_name == payload.app_name,
        )
    )
    limit = result.scalar_one_or_none()

    if limit is None:
        limit = AppLimit(
            user_id=user.id,
            app_name=payload.app_name,
            daily_limit_mins=payload.daily_limit_mins,
        )
        db.add(limit)
    else:
        limit.daily_limit_mins = payload.daily_limit_mins

    await db.commit()
    await db.refresh(limit)
    return limit


@router.get("/{telegram_id}", response_model=list[LimitResponse])
async def get_limits(telegram_id: int, db: AsyncSession = Depends(get_db)):
    # Resolve user by telegram_id
    result = await db.execute(
        select(User).where(User.telegram_id == telegram_id)
    )
    user = result.scalar_one_or_none()
    if user is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"User with telegram_id {telegram_id} not found.",
        )

    result = await db.execute(
        select(AppLimit).where(AppLimit.user_id == user.id)
    )
    limits = result.scalars().all()
    return limits
