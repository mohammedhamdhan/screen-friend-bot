"""
Leaderboard service.

Handles upsert of leaderboard rows and formatting of leaderboard messages.
"""

import logging
import uuid
from datetime import date, timedelta
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Leaderboard, User

logger = logging.getLogger(__name__)

# Score formula: (clean_days * 10) - (requests_made * 3) - (requests_denied * 5)
_SCORE_SQL = "(clean_days * 10) - (requests_made * 3) - (requests_denied * 5)"

_ALLOWED_FIELDS = {"requests_made", "requests_denied", "clean_days"}


def _compute_score(row: "Leaderboard") -> int:
    return (row.clean_days * 10) - (row.requests_made * 3) - (row.requests_denied * 5)


def _current_week_start() -> date:
    """Return the Monday of the current ISO week."""
    today = date.today()
    return today - timedelta(days=today.weekday())


async def upsert_leaderboard(
    user_id: uuid.UUID,
    group_id: uuid.UUID,
    field: str,
    db: AsyncSession,
) -> None:
    """Increment a single counter field on the leaderboard row for the current week.

    Parameters
    ----------
    user_id:
        UUID of the user whose row to update.
    group_id:
        UUID of the group the leaderboard row belongs to.
    field:
        One of ``"requests_made"``, ``"requests_denied"``, or ``"clean_days"``.
    db:
        Active async database session.

    Raises
    ------
    ValueError:
        If ``field`` is not one of the allowed values.
    """
    if field not in _ALLOWED_FIELDS:
        raise ValueError(
            f"upsert_leaderboard: field must be one of {_ALLOWED_FIELDS}, got {field!r}"
        )

    week_start = _current_week_start()

    # Try to find existing row
    result = await db.execute(
        select(Leaderboard).where(
            Leaderboard.user_id == user_id,
            Leaderboard.group_id == group_id,
            Leaderboard.week_start == week_start,
        )
    )
    row = result.scalar_one_or_none()

    if row is None:
        # Create new row with the target field set to 1
        kwargs = {
            "user_id": user_id,
            "group_id": group_id,
            "week_start": week_start,
            "requests_made": 0,
            "requests_denied": 0,
            "clean_days": 0,
        }
        kwargs[field] = 1
        row = Leaderboard(**kwargs)
        row.score = _compute_score(row)
        db.add(row)
    else:
        # Increment the target field
        setattr(row, field, getattr(row, field) + 1)
        row.score = _compute_score(row)

    await db.flush()

    logger.debug(
        "upsert_leaderboard: user_id=%s group_id=%s week_start=%s field=%s",
        user_id,
        group_id,
        week_start,
        field,
    )


async def get_weekly_leaderboard(
    group_id: uuid.UUID,
    db: AsyncSession,
) -> list[dict[str, Any]]:
    """Fetch leaderboard rows for the current week, ordered by computed score descending.

    Parameters
    ----------
    group_id:
        UUID of the group whose leaderboard to retrieve.
    db:
        Active async database session.

    Returns
    -------
    List of dicts with keys: ``username``, ``requests_made``, ``requests_denied``,
    ``clean_days``, ``score``.
    """
    week_start = _current_week_start()

    result = await db.execute(
        select(
            User.username,
            Leaderboard.requests_made,
            Leaderboard.requests_denied,
            Leaderboard.clean_days,
            Leaderboard.score,
        )
        .join(User, Leaderboard.user_id == User.id)
        .where(
            Leaderboard.group_id == group_id,
            Leaderboard.week_start == week_start,
        )
        .order_by(Leaderboard.score.desc())
    )

    rows = result.all()
    return [
        {
            "username": row.username,
            "requests_made": row.requests_made,
            "requests_denied": row.requests_denied,
            "clean_days": row.clean_days,
            "score": row.score,
        }
        for row in rows
    ]


def format_leaderboard_message(rows: list[dict[str, Any]]) -> str:
    """Produce a human-readable leaderboard string suitable for Telegram HTML.

    Rank emojis
    -----------
    - 1st: 🥇
    - 2nd: 🥈
    - 3rd: 🥉
    - 4th+: numbered

    Roast tiers (based on score)
    ----------------------------
    - score >= 30  → clean   (✨)
    - score >= 0   → slipping (😬)
    - score < 0    → cooked  (💀)

    Parameters
    ----------
    rows:
        Ordered list of leaderboard dicts as returned by ``get_weekly_leaderboard``.

    Returns
    -------
    Formatted string (HTML parse mode compatible).
    """
    if not rows:
        return "No leaderboard data for this week yet. Keep it clean! ✨"

    rank_emojis = {1: "🥇", 2: "🥈", 3: "🥉"}

    def roast_tier(score: int) -> str:
        if score >= 30:
            return "✨ clean"
        if score >= 0:
            return "😬 slipping"
        return "💀 cooked"

    lines = ["<b>📊 Weekly Leaderboard</b>", ""]

    for idx, row in enumerate(rows, start=1):
        rank = rank_emojis.get(idx, f"{idx}.")
        username = row["username"] or "unknown"
        score = row["score"]
        tier = roast_tier(score)

        lines.append(
            f"{rank} <b>@{username}</b> — score: {score:+d}  [{tier}]"
        )
        lines.append(
            f"   clean days: {row['clean_days']} | "
            f"requests: {row['requests_made']} | "
            f"denied: {row['requests_denied']}"
        )

    return "\n".join(lines)
