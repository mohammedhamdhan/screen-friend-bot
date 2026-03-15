from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.models.user import User
from app.schemas.user import UserRegisterRequest, UserResponse

router = APIRouter(prefix="/auth", tags=["auth"])


@router.post("/register", response_model=UserResponse)
async def register(payload: UserRegisterRequest, db: AsyncSession = Depends(get_db)):
    result = await db.execute(
        select(User).where(User.telegram_id == payload.telegram_id)
    )
    user = result.scalar_one_or_none()

    if user is None:
        user = User(
            telegram_id=payload.telegram_id,
            username=payload.username,
            timezone=payload.timezone,
        )
        db.add(user)
    else:
        if payload.username is not None:
            user.username = payload.username
        user.timezone = payload.timezone

    await db.commit()
    await db.refresh(user)
    return user


@router.get("/profile/{telegram_id}", response_model=UserResponse)
async def get_profile(telegram_id: int, db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(User).where(User.telegram_id == telegram_id))
    user = result.scalar_one_or_none()
    if not user:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="User not found")
    return user
