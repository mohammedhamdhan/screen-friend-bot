import uuid
from datetime import date, datetime

from sqlalchemy import Date, DateTime, ForeignKey, Integer, String, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base


class WeeklyScreenTimeLog(Base):
    __tablename__ = "weekly_screen_time_logs"

    __table_args__ = (
        UniqueConstraint(
            "user_id", "group_id", "week_start", "app_name",
            name="uq_weekly_stl_user_group_week_app",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    group_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("groups.id", ondelete="CASCADE"), nullable=False
    )
    week_start: Mapped[date] = mapped_column(Date, nullable=False)
    app_name: Mapped[str] = mapped_column(String(255), nullable=False)
    minutes_used: Mapped[int] = mapped_column(Integer, nullable=False)
    screenshot_url: Mapped[str | None] = mapped_column(String(2048), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    user: Mapped["User"] = relationship("User")  # noqa: F821
    group: Mapped["Group"] = relationship("Group")  # noqa: F821
