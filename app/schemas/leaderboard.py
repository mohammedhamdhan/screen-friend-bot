from pydantic import BaseModel


class LeaderboardEntry(BaseModel):
    username: str | None
    requests_made: int
    requests_denied: int
    clean_days: int
    score: int


class LeaderboardResponse(BaseModel):
    group_telegram_chat_id: int
    entries: list[LeaderboardEntry]
    formatted: str
