from functools import lru_cache
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    # Database
    DATABASE_URL: str

    # Redis
    REDIS_URL: str

    # Security
    SECRET_KEY: str

    # Telegram
    TELEGRAM_BOT_TOKEN: str
    WEBHOOK_URL: str = ""

    # Cloudflare R2
    R2_ACCOUNT_ID: str
    R2_ACCESS_KEY_ID: str
    R2_SECRET_ACCESS_KEY: str
    R2_BUCKET_NAME: str
    R2_PUBLIC_URL: str

    # OpenAI (GPT-4o vision for screen time OCR)
    OPENAI_API_KEY: str = ""

    # Timezone for user-facing display (IANA name)
    DISPLAY_TIMEZONE: str = "Asia/Singapore"

    # App behaviour
    REQUEST_TIMEOUT_MINUTES: int = 30
    REQUEST_COOLDOWN_MINUTES: int = 15
    CHECKIN_TIME_UTC: int = 13
    LEADERBOARD_DAY: int = 0
    SCREENSHOT_COLLECTION_TIMEOUT_MINUTES: int = 60

    # Weekly check-in
    WEEKLY_CHECKIN_TIME_UTC: int = 10
    WEEKLY_COLLECTION_TIMEOUT_MINUTES: int = 120
    WEEKLY_TOLERANCE_MINUTES: int = 15


@lru_cache
def get_settings() -> Settings:
    return Settings()
