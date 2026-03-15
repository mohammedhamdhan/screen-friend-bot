from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.database import get_db
from app.models import Group, Membership, Request, RequestStatus, User
from app.schemas.request import RequestCreateRequest, RequestResponse
from app.services import bot_service
from app.workers.tasks import expire_request

router = APIRouter(prefix="/requests", tags=["requests"])


@router.post("", response_model=RequestResponse, status_code=status.HTTP_201_CREATED)
async def create_request(
    payload: RequestCreateRequest,
    db: AsyncSession = Depends(get_db),
):
    settings = get_settings()

    # Resolve user
    user_result = await db.execute(
        select(User).where(User.telegram_id == payload.telegram_id)
    )
    user = user_result.scalar_one_or_none()
    if user is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"User with telegram_id {payload.telegram_id} not found.",
        )

    # Resolve group
    group_result = await db.execute(
        select(Group).where(Group.telegram_chat_id == payload.group_telegram_chat_id)
    )
    group = group_result.scalar_one_or_none()
    if group is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Group with telegram_chat_id {payload.group_telegram_chat_id} not found.",
        )

    # Verify membership
    membership_result = await db.execute(
        select(Membership).where(
            Membership.user_id == user.id,
            Membership.group_id == group.id,
        )
    )
    membership = membership_result.scalar_one_or_none()
    if membership is None:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="User is not a member of this group.",
        )

    # Check for existing pending request
    pending_result = await db.execute(
        select(Request).where(
            Request.user_id == user.id,
            Request.group_id == group.id,
            Request.status == RequestStatus.pending,
        )
    )
    pending = pending_result.scalar_one_or_none()
    if pending is not None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="User already has a pending request in this group.",
        )

    # Check cooldown — denied requests within REQUEST_COOLDOWN_MINUTES
    cooldown_cutoff = datetime.now(tz=timezone.utc) - timedelta(
        minutes=settings.REQUEST_COOLDOWN_MINUTES
    )
    cooldown_result = await db.execute(
        select(Request).where(
            Request.user_id == user.id,
            Request.group_id == group.id,
            Request.status == RequestStatus.denied,
            Request.created_at >= cooldown_cutoff,
        )
    )
    recent_denial = cooldown_result.scalar_one_or_none()
    if recent_denial is not None:
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail=(
                f"User is in cooldown. A request was denied within the last "
                f"{settings.REQUEST_COOLDOWN_MINUTES} minutes."
            ),
        )

    # Create the request
    expires_at = datetime.now(tz=timezone.utc) + timedelta(
        minutes=settings.REQUEST_TIMEOUT_MINUTES
    )
    request = Request(
        user_id=user.id,
        group_id=group.id,
        app_name=payload.app_name,
        minutes_requested=payload.minutes_requested,
        photo_url=payload.photo_url,
        caption=payload.caption,
        status=RequestStatus.pending,
        expires_at=expires_at,
    )
    db.add(request)
    await db.flush()  # Assign the PK before using it

    # Enqueue expiry task
    expire_request.apply_async(args=[str(request.id)], eta=expires_at)

    # Post to Telegram group (fire-and-forget; message_id is optional)
    message_id = None
    if payload.photo_url:
        message_id = await bot_service.post_request_to_group(
            group_chat_id=group.telegram_chat_id,
            request_id=request.id,
            photo_url=payload.photo_url,
            requester_username=user.username or str(user.telegram_id),
            app_name=payload.app_name,
            note=payload.caption,
        )

    if message_id is not None:
        request.telegram_message_id = message_id

    await db.commit()
    await db.refresh(request)
    return request


@router.get("/{group_telegram_chat_id}/pending", response_model=list[RequestResponse])
async def list_pending_requests(
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

    result = await db.execute(
        select(Request).where(
            Request.group_id == group.id,
            Request.status == RequestStatus.pending,
        )
    )
    requests = result.scalars().all()
    return requests
