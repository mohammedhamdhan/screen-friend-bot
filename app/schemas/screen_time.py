from pydantic import BaseModel


class AppUsage(BaseModel):
    app_name: str
    minutes: int


class ScreenTimeSubmitRequest(BaseModel):
    telegram_id: int
    group_telegram_chat_id: int
    apps: list[AppUsage]
    screenshot_url: str | None = None
    stayed_clean: bool
    violations: list[str] = []
