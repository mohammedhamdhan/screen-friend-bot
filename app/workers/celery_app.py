"""
Celery application factory for ScreenGate.

Uses Redis as both the message broker and result backend.
Beat schedule drives daily check-in reminders and weekly leaderboard posts.
"""

from celery import Celery
from celery.schedules import crontab

from app.config import get_settings

settings = get_settings()

celery_app = Celery(
    "screengate",
    broker=settings.REDIS_URL,
    backend=settings.REDIS_URL,
    include=["app.workers.tasks"],
)

celery_app.conf.update(
    task_serializer="json",
    result_serializer="json",
    accept_content=["json"],
    timezone="UTC",
    enable_utc=True,
    beat_schedule={
        "send-checkins": {
            "task": "app.workers.tasks.send_daily_checkins",
            "schedule": crontab(hour=settings.CHECKIN_TIME_UTC, minute=0),
        },
        "send-leaderboard": {
            "task": "app.workers.tasks.send_weekly_leaderboard",
            "schedule": crontab(
                hour=1,
                minute=0,
                day_of_week=settings.LEADERBOARD_DAY,
            ),
        },
    },
)
