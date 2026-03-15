import uuid
from datetime import date

from sqlalchemy import Date, ForeignKey, Integer
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base


class Leaderboard(Base):
    __tablename__ = "leaderboard"

    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), primary_key=True
    )
    group_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("groups.id", ondelete="CASCADE"), primary_key=True
    )
    week_start: Mapped[date] = mapped_column(Date, primary_key=True)
    requests_made: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    requests_denied: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    clean_days: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    score: Mapped[int] = mapped_column(Integer, default=0, nullable=False)

    # Relationships
    user: Mapped["User"] = relationship("User", back_populates="leaderboard_entries")  # noqa: F821
    group: Mapped["Group"] = relationship("Group", back_populates="leaderboard_entries")  # noqa: F821
