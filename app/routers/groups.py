from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.models import Group, Membership, User
from app.schemas.group import GroupCreateRequest, GroupResponse, MembershipCreateRequest

router = APIRouter(prefix="/groups", tags=["groups"])


@router.post("", response_model=GroupResponse, status_code=status.HTTP_201_CREATED)
async def upsert_group(payload: GroupCreateRequest, db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(Group).where(Group.telegram_chat_id == payload.telegram_chat_id))
    group = result.scalar_one_or_none()
    if group:
        group.name = payload.name
    else:
        group = Group(telegram_chat_id=payload.telegram_chat_id, name=payload.name)
        db.add(group)
    await db.commit()
    await db.refresh(group)
    return group


@router.post("/membership", status_code=status.HTTP_201_CREATED)
async def upsert_membership(payload: MembershipCreateRequest, db: AsyncSession = Depends(get_db)):
    # Look up user and group
    user_result = await db.execute(select(User).where(User.telegram_id == payload.telegram_id))
    user = user_result.scalar_one_or_none()
    if not user:
        raise HTTPException(status_code=404, detail="User not found")

    group_result = await db.execute(select(Group).where(Group.telegram_chat_id == payload.group_telegram_chat_id))
    group = group_result.scalar_one_or_none()
    if not group:
        raise HTTPException(status_code=404, detail="Group not found")

    # Check if already member
    existing = await db.execute(
        select(Membership).where(Membership.user_id == user.id, Membership.group_id == group.id)
    )
    if existing.scalar_one_or_none():
        return {"detail": "Already a member"}

    membership = Membership(user_id=user.id, group_id=group.id)
    db.add(membership)
    await db.commit()
    return {"detail": "Membership created"}
