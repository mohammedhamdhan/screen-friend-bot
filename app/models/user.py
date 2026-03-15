import uuid
from datetime import datetime

from sqlalchemy import BigInteger, DateTime, Integer, String, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base


class User(Base):
    __tablename__ = "users"

    id: Mapped[uuid.UUID] = mapped_column(
        primary_key=True, default=uuid.uuid4
    )
    telegram_id: Mapped[int] = mapped_column(BigInteger, unique=True, nullable=False)
    username: Mapped[str | None] = mapped_column(String(255), nullable=True)
    timezone: Mapped[str] = mapped_column(String(64), default="UTC", nullable=False)
    streak: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    # Relationships
    memberships: Mapped[list["Membership"]] = relationship(  # noqa: F821
        "Membership", back_populates="user"
    )
    app_limits: Mapped[list["AppLimit"]] = relationship(  # noqa: F821
        "AppLimit", back_populates="user"
    )
    requests: Mapped[list["Request"]] = relationship(  # noqa: F821
        "Request", back_populates="user"
    )
    votes: Mapped[list["Vote"]] = relationship(  # noqa: F821
        "Vote", back_populates="voter"
    )
    checkins: Mapped[list["Checkin"]] = relationship(  # noqa: F821
        "Checkin", back_populates="user"
    )
    leaderboard_entries: Mapped[list["Leaderboard"]] = relationship(  # noqa: F821
        "Leaderboard", back_populates="user"
    )
