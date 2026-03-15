from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.models import Group
from app.schemas.leaderboard import LeaderboardEntry, LeaderboardResponse
from app.services import leaderboard_service

router = APIRouter(prefix="/leaderboard", tags=["leaderboard"])


@router.get("/{group_telegram_chat_id}", response_model=LeaderboardResponse)
async def get_leaderboard(
    group_telegram_chat_id: int,
    db: AsyncSession = Depends(get_db),
):
    # Resolve group
    group_result = await db.execute(
        select(Group).where(Group.telegram_chat_id == group_telegram_chat_id)
    )
    group = group_result.scalar_one_or_none()
    if group is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Group with telegram_chat_id {group_telegram_chat_id} not found.",
        )

    rows = await leaderboard_service.get_weekly_leaderboard(group_id=group.id, db=db)
    formatted = leaderboard_service.format_leaderboard_message(rows)

    entries = [LeaderboardEntry(**row) for row in rows]

    return LeaderboardResponse(
        group_telegram_chat_id=group_telegram_chat_id,
        entries=entries,
        formatted=formatted,
    )
