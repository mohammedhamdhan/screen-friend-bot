import uuid
from datetime import datetime

from pydantic import BaseModel


class LimitUpsertRequest(BaseModel):
    telegram_id: int
    app_name: str
    daily_limit_mins: int


class LimitResponse(BaseModel):
    id: uuid.UUID
    user_id: uuid.UUID
    app_name: str
    daily_limit_mins: int
    updated_at: datetime

    model_config = {"from_attributes": True}
