import uuid
from datetime import datetime

from pydantic import BaseModel

from app.models.request import RequestStatus


class RequestCreateRequest(BaseModel):
    telegram_id: int
    group_telegram_chat_id: int
    app_name: str
    minutes_requested: int
    photo_url: str | None = None
    caption: str | None = None


class RequestResponse(BaseModel):
    id: uuid.UUID
    user_id: uuid.UUID
    group_id: uuid.UUID
    app_name: str
    minutes_requested: int
    photo_url: str | None
    caption: str | None
    status: RequestStatus
    telegram_message_id: int | None
    expires_at: datetime | None
    created_at: datetime

    model_config = {"from_attributes": True}
