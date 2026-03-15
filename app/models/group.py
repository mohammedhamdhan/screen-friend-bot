import uuid
from datetime import datetime

from sqlalchemy import BigInteger, DateTime, Integer, String, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base


class Group(Base):
    __tablename__ = "groups"

    id: Mapped[uuid.UUID] = mapped_column(
        primary_key=True, default=uuid.uuid4
    )
    telegram_chat_id: Mapped[int] = mapped_column(BigInteger, unique=True, nullable=False)
    name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    vote_threshold: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    checkin_time_utc: Mapped[int | None] = mapped_column(Integer, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    # Relationships
    memberships: Mapped[list["Membership"]] = relationship(  # noqa: F821
        "Membership", back_populates="group"
    )
    requests: Mapped[list["Request"]] = relationship(  # noqa: F821
        "Request", back_populates="group"
    )
    leaderboard_entries: Mapped[list["Leaderboard"]] = relationship(  # noqa: F821
        "Leaderboard", back_populates="group"
    )
