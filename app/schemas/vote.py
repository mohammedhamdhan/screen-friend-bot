import uuid
from datetime import datetime

from pydantic import BaseModel


class VoteCreateRequest(BaseModel):
    request_id: uuid.UUID
    voter_telegram_id: int
    decision: bool


class VoteResponse(BaseModel):
    request_id: uuid.UUID
    voter_id: uuid.UUID
    decision: bool
    voted_at: datetime

    model_config = {"from_attributes": True}
