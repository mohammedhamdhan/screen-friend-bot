import uuid
from datetime import datetime

from pydantic import BaseModel


class UserRegisterRequest(BaseModel):
    telegram_id: int
    username: str | None = None
    timezone: str = "UTC"


class UserResponse(BaseModel):
    id: uuid.UUID
    telegram_id: int
    username: str | None
    timezone: str
    streak: int
    created_at: datetime

    model_config = {"from_attributes": True}
