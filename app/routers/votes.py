from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.models import Membership, Request, RequestStatus, User, Vote
from app.schemas.vote import VoteCreateRequest, VoteResponse
from app.services import vote_service

router = APIRouter(prefix="/votes", tags=["votes"])


@router.post("", response_model=VoteResponse, status_code=status.HTTP_200_OK)
async def record_vote(
    payload: VoteCreateRequest,
    db: AsyncSession = Depends(get_db),
):
    # Resolve the request
    request_result = await db.execute(
        select(Request).where(Request.id == payload.request_id)
    )
    request = request_result.scalar_one_or_none()
    if request is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Request {payload.request_id} not found.",
        )

    # Request must be pending
    if request.status != RequestStatus.pending:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Request is not pending (current status: {request.status.value}).",
        )

    # Resolve the voter
    voter_result = await db.execute(
        select(User).where(User.telegram_id == payload.voter_telegram_id)
    )
    voter = voter_result.scalar_one_or_none()
    if voter is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"User with telegram_id {payload.voter_telegram_id} not found.",
        )

    # Voter must be a member of the request's group
    membership_result = await db.execute(
        select(Membership).where(
            Membership.user_id == voter.id,
            Membership.group_id == request.group_id,
        )
    )
    membership = membership_result.scalar_one_or_none()
    if membership is None:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Voter is not a member of the request's group.",
        )

    # Voter cannot be the requester
    if voter.id == request.user_id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Requester cannot vote on their own request.",
        )

    # Upsert the vote (allow changing vote)
    vote_result = await db.execute(
        select(Vote).where(
            Vote.request_id == request.id,
            Vote.voter_id == voter.id,
        )
    )
    vote = vote_result.scalar_one_or_none()

    if vote is None:
        vote = Vote(
            request_id=request.id,
            voter_id=voter.id,
            decision=payload.decision,
        )
        db.add(vote)
    else:
        vote.decision = payload.decision

    await db.flush()

    # Check and potentially resolve the request
    await vote_service.check_and_resolve(request_id=request.id, db=db)

    await db.commit()
    await db.refresh(vote)
    return vote
