from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import func, select
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

    # Normalize app name to title case
    normalized_name = payload.app_name.strip().title()

    # Case-insensitive lookup for existing limit
    result = await db.execute(
        select(AppLimit).where(
            AppLimit.user_id == user.id,
            func.lower(AppLimit.app_name) == normalized_name.lower(),
        )
    )
    limit = result.scalar_one_or_none()

    if limit is None:
        # Check for fuzzy duplicates (e.g. "Insta" vs "Instagram")
        all_limits_result = await db.execute(
            select(AppLimit).where(AppLimit.user_id == user.id)
        )
        all_limits = all_limits_result.scalars().all()
        for existing in all_limits:
            existing_lower = existing.app_name.lower()
            new_lower = normalized_name.lower()
            if existing_lower in new_lower or new_lower in existing_lower:
                raise HTTPException(
                    status_code=status.HTTP_409_CONFLICT,
                    detail=f"A similar limit already exists: {existing.app_name} ({existing.daily_limit_mins} min/day). "
                           f"Use /setlimit {existing.app_name} <minutes> to update it.",
                )

        limit = AppLimit(
            user_id=user.id,
            app_name=normalized_name,
            daily_limit_mins=payload.daily_limit_mins,
        )
        db.add(limit)
    else:
        # Update existing — also normalize the stored name
        limit.app_name = normalized_name
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


@router.delete("/{telegram_id}/{app_name}", status_code=status.HTTP_200_OK)
async def delete_limit(telegram_id: int, app_name: str, db: AsyncSession = Depends(get_db)):
    result = await db.execute(
        select(User).where(User.telegram_id == telegram_id)
    )
    user = result.scalar_one_or_none()
    if user is None:
        raise HTTPException(status_code=404, detail="User not found")

    # Case-insensitive lookup — delete all matching (handles duplicates)
    result = await db.execute(
        select(AppLimit).where(
            AppLimit.user_id == user.id,
            func.lower(AppLimit.app_name) == app_name.lower(),
        )
    )
    limits = result.scalars().all()
    if not limits:
        raise HTTPException(status_code=404, detail="Limit not found")

    for limit in limits:
        await db.delete(limit)
    await db.commit()
    return {"detail": f"Limit for {app_name} removed"}
