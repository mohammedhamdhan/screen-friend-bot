import uuid
from datetime import date, datetime

from sqlalchemy import Boolean, Date, DateTime, ForeignKey, Text, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base


class Checkin(Base):
    __tablename__ = "checkins"

    __table_args__ = (UniqueConstraint("user_id", "date", name="uq_checkins_user_date"),)

    id: Mapped[uuid.UUID] = mapped_column(
        primary_key=True, default=uuid.uuid4
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    date: Mapped[date] = mapped_column(Date, nullable=False)
    stayed_clean: Mapped[bool] = mapped_column(Boolean, nullable=False)
    ocr_source: Mapped[bool] = mapped_column(Boolean, default=False, server_default="false", nullable=False)
    confession_note: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    # Relationships
    user: Mapped["User"] = relationship("User", back_populates="checkins")  # noqa: F821
