from pydantic import BaseModel


class CheckinCreateRequest(BaseModel):
    telegram_id: int
    stayed_clean: bool
    confession_note: str | None = None
