from pydantic import BaseModel

from app.schemas.screen_time import AppUsage


class WeeklyScreenTimeSubmitRequest(BaseModel):
    telegram_id: int
    group_telegram_chat_id: int
    apps: list[AppUsage]
    screenshot_url: str | None = None
