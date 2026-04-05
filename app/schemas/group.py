import uuid
from datetime import datetime
from pydantic import BaseModel


class GroupCreateRequest(BaseModel):
    telegram_chat_id: int
    name: str


class GroupResponse(BaseModel):
    id: uuid.UUID
    telegram_chat_id: int
    name: str | None
    vote_threshold: int
    checkin_time_utc: int | None = None
    checkin_minute_utc: int = 0
    created_at: datetime
    model_config = {"from_attributes": True}


class GroupUpdateRequest(BaseModel):
    vote_threshold: int | None = None
    checkin_time_utc: int | None = None
    checkin_minute_utc: int | None = None


class MembershipCreateRequest(BaseModel):
    telegram_id: int
    group_telegram_chat_id: int
