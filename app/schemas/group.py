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
    created_at: datetime
    model_config = {"from_attributes": True}


class MembershipCreateRequest(BaseModel):
    telegram_id: int
    group_telegram_chat_id: int
