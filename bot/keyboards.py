"""
Reusable inline keyboard builders for the ScreenGate bot.
"""

from telegram import InlineKeyboardButton, InlineKeyboardMarkup


def vote_keyboard(request_id: str | int) -> InlineKeyboardMarkup:
    """Inline keyboard with Yes / No vote buttons for a screen request."""
    return InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton("✅ Yes", callback_data=f"vote:{request_id}:1"),
                InlineKeyboardButton("❌ No", callback_data=f"vote:{request_id}:0"),
            ]
        ]
    )


def duration_keyboard() -> InlineKeyboardMarkup:
    """Inline keyboard for selecting extra screen-time duration (minutes)."""
    durations = [15, 30, 45, 60, 90, 120]
    buttons = [
        InlineKeyboardButton(f"{d} min", callback_data=f"duration:{d}")
        for d in durations
    ]
    # Two buttons per row
    rows = [buttons[i : i + 2] for i in range(0, len(buttons), 2)]
    return InlineKeyboardMarkup(rows)


def screenshot_fallback_keyboard(user_id: int) -> InlineKeyboardMarkup:
    """Inline keyboard for manual check-in when OCR fails or times out."""
    return InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton(
                    "✅ Stayed clean",
                    callback_data=f"screencheckin:{user_id}:clean",
                ),
                InlineKeyboardButton(
                    "😔 Slipped",
                    callback_data=f"screencheckin:{user_id}:slipped",
                ),
            ]
        ]
    )


def weekly_screenshot_fallback_keyboard(user_id: int) -> InlineKeyboardMarkup:
    """Inline keyboard for weekly manual check-in when OCR fails or times out."""
    return InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton(
                    "✅ Submitted",
                    callback_data=f"weeklyscreencheckin:{user_id}:submitted",
                ),
                InlineKeyboardButton(
                    "⏭️ Skip",
                    callback_data=f"weeklyscreencheckin:{user_id}:skipped",
                ),
            ]
        ]
    )


def checkin_keyboard(user_id: int | None = None) -> InlineKeyboardMarkup:
    """Inline keyboard for daily check-in (clean / slipped)."""
    if user_id is not None:
        clean_data = f"checkin:{user_id}:clean"
        slipped_data = f"checkin:{user_id}:slipped"
    else:
        clean_data = "checkin:clean"
        slipped_data = "checkin:slipped"

    return InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton("✅ Stayed clean", callback_data=clean_data),
                InlineKeyboardButton("😔 Slipped", callback_data=slipped_data),
            ]
        ]
    )
