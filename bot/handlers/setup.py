"""
Setup handlers: /start, /setlimit, /removelimit, /limits, /link, /setcheckintime

/setlimit, /removelimit, and /setcheckintime support conversational flows.
"""

import logging
import os
from zoneinfo import ZoneInfo

import httpx

from app.config import get_settings
from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import (
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
    ConversationHandler,
    MessageHandler,
    filters,
)

logger = logging.getLogger(__name__)

_BASE_URL = f"http://127.0.0.1:{os.environ.get('PORT', '8000')}"

# ---------------------------------------------------------------------------
# ConversationHandler states
# ---------------------------------------------------------------------------
SETLIMIT_APP, SETLIMIT_MINUTES = range(2)
SETCHECKINTIME_HOUR = range(2, 3)[0]

# user_data keys
_SL_APP = "setlimit_app"
_SCT_CHAT = "setcheckintime_chat_id"


# ---------------------------------------------------------------------------
# /start
# ---------------------------------------------------------------------------

async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """/start — register user and send welcome message."""
    user = update.effective_user
    if user is None:
        return

    payload = {
        "telegram_id": user.id,
        "username": user.username,
        "timezone": "UTC",
    }

    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.post(f"{_BASE_URL}/api/v1/auth/register", json=payload)
            resp.raise_for_status()
    except Exception as exc:
        logger.error("start_command: failed to register user %s: %s", user.id, exc)

    name = user.first_name or user.username or "there"
    await update.message.reply_text(
        f"👋 Hey {name}! Welcome to ScreenGate.\n\n"
        "I help your accountability group vote on extra screen time.\n\n"
        "Commands:\n"
        "• /setlimit — set your daily app limit\n"
        "• /limits — view your limits\n"
        "• /setcheckintime — set check-in time\n"
        "• /more — request extra time\n"
        "• /checkin — log your daily check-in\n"
        "• /streak — view your streak\n"
        "• /leaderboard — group leaderboard\n"
        "• /link — link this group (admins only)"
    )


# ---------------------------------------------------------------------------
# /setlimit — conversational flow
# ---------------------------------------------------------------------------

async def setlimit_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """/setlimit [app] [minutes] — set a daily app limit."""
    user = update.effective_user
    if user is None:
        return ConversationHandler.END

    args = context.args or []

    if len(args) >= 2:
        # Inline usage: /setlimit Instagram 30
        app_name = args[0]
        try:
            daily_limit_mins = int(args[1])
        except ValueError:
            await update.message.reply_text("Minutes must be a whole number.")
            return ConversationHandler.END
        if daily_limit_mins <= 0:
            await update.message.reply_text("Minutes must be greater than 0.")
            return ConversationHandler.END
        await _submit_limit(update, user.id, app_name, daily_limit_mins)
        return ConversationHandler.END

    if len(args) == 1:
        # Got app name, need minutes
        context.user_data[_SL_APP] = args[0]
        await update.message.reply_text(
            f"How many minutes per day for *{args[0]}*?",
            parse_mode="Markdown",
        )
        return SETLIMIT_MINUTES

    # No args — ask for app name
    await update.message.reply_text(
        "Which app do you want to set a limit for?\n"
        "(e.g. Instagram, YouTube, TikTok)"
    )
    return SETLIMIT_APP


async def _setlimit_got_app(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """User replied with the app name."""
    app_name = update.message.text.strip()
    if not app_name:
        await update.message.reply_text("Please enter an app name.")
        return SETLIMIT_APP

    context.user_data[_SL_APP] = app_name
    await update.message.reply_text(
        f"How many minutes per day for *{app_name}*?",
        parse_mode="Markdown",
    )
    return SETLIMIT_MINUTES


async def _setlimit_got_minutes(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """User replied with the minutes."""
    user = update.effective_user
    if user is None:
        return ConversationHandler.END

    try:
        daily_limit_mins = int(update.message.text.strip())
    except ValueError:
        await update.message.reply_text("Please enter a whole number.")
        return SETLIMIT_MINUTES

    if daily_limit_mins <= 0:
        await update.message.reply_text("Minutes must be greater than 0.")
        return SETLIMIT_MINUTES

    app_name = context.user_data.pop(_SL_APP, "unknown")
    await _submit_limit(update, user.id, app_name, daily_limit_mins)
    return ConversationHandler.END


async def _submit_limit(update: Update, telegram_id: int, app_name: str, daily_limit_mins: int) -> None:
    """Submit the limit to the API."""
    payload = {
        "telegram_id": telegram_id,
        "app_name": app_name,
        "daily_limit_mins": daily_limit_mins,
    }

    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.post(f"{_BASE_URL}/api/v1/limits", json=payload)
            if resp.status_code == 404:
                await update.message.reply_text(
                    "You are not registered. Use /start first."
                )
                return
            if resp.status_code == 409:
                detail = resp.json().get("detail", "A similar limit already exists.")
                await update.message.reply_text(f"⚠️ {detail}")
                return
            resp.raise_for_status()
    except httpx.HTTPStatusError as exc:
        logger.error("setlimit: HTTP error: %s", exc)
        await update.message.reply_text("Failed to set limit. Please try again.")
        return
    except Exception as exc:
        logger.error("setlimit: error: %s", exc)
        await update.message.reply_text("An error occurred. Please try again.")
        return

    await update.message.reply_text(
        f"✅ Limit set: {app_name} → {daily_limit_mins} min/day"
    )


async def _setlimit_cancel(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    context.user_data.pop(_SL_APP, None)
    await update.message.reply_text("Cancelled.")
    return ConversationHandler.END


def build_setlimit_handler() -> ConversationHandler:
    """ConversationHandler for /setlimit."""
    return ConversationHandler(
        entry_points=[CommandHandler("setlimit", setlimit_command)],
        states={
            SETLIMIT_APP: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, _setlimit_got_app),
            ],
            SETLIMIT_MINUTES: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, _setlimit_got_minutes),
            ],
        },
        fallbacks=[CommandHandler("cancel", _setlimit_cancel)],
        per_user=True,
        per_chat=True,
    )


# ---------------------------------------------------------------------------
# /removelimit — conversational flow
# ---------------------------------------------------------------------------

REMOVELIMIT_CHOOSE = range(10, 11)[0]


async def removelimit_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """/removelimit [app] — remove an app from your limits."""
    user = update.effective_user
    if user is None:
        return ConversationHandler.END

    args = context.args or []

    if args:
        # Inline usage: /removelimit Instagram
        app_name = args[0]
        await _submit_remove_limit(update, user.id, app_name)
        return ConversationHandler.END

    # No args — fetch limits and show as buttons
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.get(f"{_BASE_URL}/api/v1/limits/{user.id}")
            if resp.status_code == 404:
                await update.message.reply_text("You are not registered. Use /start first.")
                return ConversationHandler.END
            resp.raise_for_status()
            limits = resp.json()
    except Exception as exc:
        logger.error("removelimit: error fetching limits: %s", exc)
        await update.message.reply_text("Failed to fetch your limits.")
        return ConversationHandler.END

    if not limits:
        await update.message.reply_text("You have no limits set. Use /setlimit to add one.")
        return ConversationHandler.END

    buttons = [
        [InlineKeyboardButton(
            f"{lim['app_name']} ({lim['daily_limit_mins']} min)",
            callback_data=f"removelimit:{lim['app_name']}",
        )]
        for lim in limits
    ]
    await update.message.reply_text(
        "Which limit do you want to remove?",
        reply_markup=InlineKeyboardMarkup(buttons),
    )
    return REMOVELIMIT_CHOOSE


async def _removelimit_button(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Handle limit selection from inline keyboard."""
    query = update.callback_query
    if query is None:
        return ConversationHandler.END

    user = update.effective_user
    if user is None:
        return ConversationHandler.END

    await query.answer()
    app_name = (query.data or "").split(":", 1)[1] if ":" in (query.data or "") else ""
    if not app_name:
        await query.edit_message_text("Invalid selection.")
        return ConversationHandler.END

    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.delete(f"{_BASE_URL}/api/v1/limits/{user.id}/{app_name}")
            if resp.status_code == 404:
                await query.edit_message_text(f"No limit found for {app_name}.")
                return ConversationHandler.END
            resp.raise_for_status()
    except Exception as exc:
        logger.error("removelimit: error: %s", exc)
        await query.edit_message_text("Failed to remove limit.")
        return ConversationHandler.END

    await query.edit_message_text(f"✅ Limit for {app_name} removed.")
    return ConversationHandler.END


async def _removelimit_text(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Handle typed app name."""
    user = update.effective_user
    if user is None:
        return ConversationHandler.END

    app_name = update.message.text.strip()
    if not app_name:
        await update.message.reply_text("Please enter an app name.")
        return REMOVELIMIT_CHOOSE

    await _submit_remove_limit(update, user.id, app_name)
    return ConversationHandler.END


async def _submit_remove_limit(update, telegram_id, app_name):
    """Delete a limit via the API."""
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.delete(f"{_BASE_URL}/api/v1/limits/{telegram_id}/{app_name}")
            if resp.status_code == 404:
                await update.message.reply_text(f"No limit found for {app_name}.")
                return
            resp.raise_for_status()
    except Exception as exc:
        logger.error("removelimit: error: %s", exc)
        await update.message.reply_text("Failed to remove limit.")
        return

    await update.message.reply_text(f"✅ Limit for {app_name} removed.")


async def _removelimit_cancel(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    await update.message.reply_text("Cancelled.")
    return ConversationHandler.END


def build_removelimit_handler() -> ConversationHandler:
    """ConversationHandler for /removelimit."""
    return ConversationHandler(
        entry_points=[CommandHandler("removelimit", removelimit_command)],
        states={
            REMOVELIMIT_CHOOSE: [
                CallbackQueryHandler(_removelimit_button, pattern=r"^removelimit:.+$"),
                MessageHandler(filters.TEXT & ~filters.COMMAND, _removelimit_text),
            ],
        },
        fallbacks=[CommandHandler("cancel", _removelimit_cancel)],
        per_user=True,
        per_chat=True,
        per_message=False,
    )


# ---------------------------------------------------------------------------
# /setcheckintime — conversational flow
# ---------------------------------------------------------------------------

def _display_tz_abbr() -> str:
    """Short label for the display timezone (e.g. 'SGT')."""
    from datetime import datetime as _dt
    tz = ZoneInfo(get_settings().DISPLAY_TIMEZONE)
    return _dt.now(tz).strftime("%Z")


def _local_to_utc(hour: int, minute: int) -> tuple[int, int]:
    """Convert a local display-timezone time to UTC hour and minute."""
    from datetime import datetime as _dt
    tz = ZoneInfo(get_settings().DISPLAY_TIMEZONE)
    # Build a dummy datetime in the display timezone, then convert to UTC
    local = _dt.now(tz).replace(hour=hour, minute=minute, second=0, microsecond=0)
    utc = local.astimezone(ZoneInfo("UTC"))
    return utc.hour, utc.minute


def _utc_to_local(hour: int, minute: int) -> tuple[int, int]:
    """Convert a UTC hour and minute to the display timezone."""
    from datetime import datetime as _dt
    utc = _dt.now(ZoneInfo("UTC")).replace(hour=hour, minute=minute, second=0, microsecond=0)
    local = utc.astimezone(ZoneInfo(get_settings().DISPLAY_TIMEZONE))
    return local.hour, local.minute


def _time_keyboard() -> InlineKeyboardMarkup:
    """Inline keyboard with common preset times in the display timezone."""
    tz_abbr = _display_tz_abbr()
    # Show local evening/night times (common check-in hours in SGT)
    local_presets = [8, 9, 10, 11, 12, 13, 14, 15, 16, 17, 18, 19, 20, 21, 22, 23]
    buttons = []
    for h in local_presets:
        utc_h, utc_m = _local_to_utc(h, 0)
        buttons.append(
            InlineKeyboardButton(
                f"{h:02d}:00 {tz_abbr}",
                callback_data=f"setcheckintime:{utc_h}:{utc_m}",
            )
        )
    rows = [buttons[i:i + 4] for i in range(0, len(buttons), 4)]
    return InlineKeyboardMarkup(rows)


def _parse_time_input(text: str) -> tuple[int, int] | None:
    """Parse a time string in HHMM or HH:MM format. Returns (hour, minute) or None."""
    text = text.strip()
    # Try HH:MM format
    if ":" in text:
        parts = text.split(":")
        if len(parts) == 2:
            try:
                h, m = int(parts[0]), int(parts[1])
                if 0 <= h <= 23 and 0 <= m <= 59:
                    return (h, m)
            except ValueError:
                pass
        return None
    # Try HHMM format (3 or 4 digits) e.g. 900 -> 09:00, 1117 -> 11:17
    if text.isdigit() and 3 <= len(text) <= 4:
        padded = text.zfill(4)
        h, m = int(padded[:2]), int(padded[2:])
        if 0 <= h <= 23 and 0 <= m <= 59:
            return (h, m)
        return None
    # Try plain hour (1-2 digits)
    if text.isdigit() and len(text) <= 2:
        h = int(text)
        if 0 <= h <= 23:
            return (h, 0)
    return None


async def setcheckintime_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """/setcheckintime [HHMM] — set the daily check-in time (in display timezone)."""
    chat = update.effective_chat
    if chat is None:
        return ConversationHandler.END

    if chat.type not in ("group", "supergroup"):
        await update.message.reply_text("This command can only be used in a group chat.")
        return ConversationHandler.END

    tz_abbr = _display_tz_abbr()
    args = context.args or []

    if args:
        # Inline usage: /setcheckintime 2058 or /setcheckintime 20
        parsed = _parse_time_input(args[0])
        if parsed is None:
            await update.message.reply_text(
                f"Invalid time. Use HHMM (e.g. 2058), HH:MM (e.g. 20:58), or an hour (0-23). Time is in {tz_abbr}."
            )
            return ConversationHandler.END
        local_hour, local_minute = parsed
        utc_hour, utc_minute = _local_to_utc(local_hour, local_minute)
        await _submit_checkin_time(update, chat.id, utc_hour, utc_minute, local_hour, local_minute)
        return ConversationHandler.END

    # No args — show time picker
    context.user_data[_SCT_CHAT] = chat.id
    await update.message.reply_text(
        f"What time should the daily check-in be? ({tz_abbr})\n"
        f"Pick a preset or type any time in 24h format (e.g. 2058, 20:58):",
        reply_markup=_time_keyboard(),
    )
    return SETCHECKINTIME_HOUR


async def _setcheckintime_button(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Handle time selection from inline keyboard."""
    query = update.callback_query
    if query is None:
        return ConversationHandler.END

    await query.answer()
    try:
        # callback_data format: "setcheckintime:{hour}:{minute}"
        parts = query.data.split(":")
        hour = int(parts[1])
        minute = int(parts[2]) if len(parts) > 2 else 0
    except (IndexError, ValueError):
        await query.edit_message_text("Invalid selection.")
        return ConversationHandler.END

    chat_id = context.user_data.pop(_SCT_CHAT, None)
    if not chat_id:
        chat_id = update.effective_chat.id if update.effective_chat else None
    if not chat_id:
        return ConversationHandler.END

    await query.edit_message_text("Updating...")
    await _submit_checkin_time_from_query(query, chat_id, hour, minute)
    return ConversationHandler.END


async def _setcheckintime_text(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Handle typed time input (HHMM, HH:MM, or plain hour) in display timezone."""
    tz_abbr = _display_tz_abbr()
    parsed = _parse_time_input(update.message.text)
    if parsed is None:
        await update.message.reply_text(
            f"Invalid time. Use HHMM (e.g. 2058), HH:MM (e.g. 20:58), or an hour (0-23). Time is in {tz_abbr}."
        )
        return SETCHECKINTIME_HOUR

    local_hour, local_minute = parsed
    utc_hour, utc_minute = _local_to_utc(local_hour, local_minute)

    chat_id = context.user_data.pop(_SCT_CHAT, None)
    if not chat_id:
        chat_id = update.effective_chat.id if update.effective_chat else None
    if not chat_id:
        return ConversationHandler.END

    await _submit_checkin_time(update, chat_id, utc_hour, utc_minute, local_hour, local_minute)
    return ConversationHandler.END


async def _submit_checkin_time(
    update: Update, chat_id: int, hour: int, minute: int = 0,
    local_hour: int | None = None, local_minute: int | None = None,
) -> None:
    """Submit the check-in time (UTC) to the API and reply with local time."""
    payload = {
        "checkin_time_utc": hour,
        "checkin_minute_utc": minute,
    }

    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.patch(
                f"{_BASE_URL}/api/v1/groups/{chat_id}", json=payload
            )
            if resp.status_code == 404:
                await update.message.reply_text(
                    "Group not registered. Use /link first."
                )
                return
            resp.raise_for_status()
    except Exception as exc:
        logger.error("setcheckintime: error: %s", exc)
        await update.message.reply_text("Failed to update check-in time.")
        return

    tz_abbr = _display_tz_abbr()
    if local_hour is not None and local_minute is not None:
        display_h, display_m = local_hour, local_minute
    else:
        display_h, display_m = _utc_to_local(hour, minute)
    await update.message.reply_text(
        f"✅ Daily check-in time set to {display_h:02d}:{display_m:02d} {tz_abbr}."
    )


async def _submit_checkin_time_from_query(query, chat_id: int, hour: int, minute: int = 0) -> None:
    """Submit the check-in time (UTC) to the API and reply with local time via callback query."""
    payload = {
        "checkin_time_utc": hour,
        "checkin_minute_utc": minute,
    }

    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.patch(
                f"{_BASE_URL}/api/v1/groups/{chat_id}", json=payload
            )
            if resp.status_code == 404:
                await query.edit_message_text("Group not registered. Use /link first.")
                return
            resp.raise_for_status()
    except Exception as exc:
        logger.error("setcheckintime: error: %s", exc)
        await query.edit_message_text("Failed to update check-in time.")
        return

    tz_abbr = _display_tz_abbr()
    display_h, display_m = _utc_to_local(hour, minute)
    await query.edit_message_text(
        f"✅ Daily check-in time set to {display_h:02d}:{display_m:02d} {tz_abbr}."
    )


async def _setcheckintime_cancel(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    context.user_data.pop(_SCT_CHAT, None)
    await update.message.reply_text("Cancelled.")
    return ConversationHandler.END


def build_setcheckintime_handler() -> ConversationHandler:
    """ConversationHandler for /setcheckintime."""
    return ConversationHandler(
        entry_points=[CommandHandler("setcheckintime", setcheckintime_command)],
        states={
            SETCHECKINTIME_HOUR: [
                CallbackQueryHandler(_setcheckintime_button, pattern=r"^setcheckintime:\d+(?::\d+)?$"),
                MessageHandler(filters.TEXT & ~filters.COMMAND, _setcheckintime_text),
            ],
        },
        fallbacks=[CommandHandler("cancel", _setcheckintime_cancel)],
        per_user=True,
        per_chat=True,
        per_message=False,
    )


# ---------------------------------------------------------------------------
# /limits
# ---------------------------------------------------------------------------

async def limits_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """/limits — list all app limits for the current user."""
    user = update.effective_user
    if user is None:
        return

    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.get(f"{_BASE_URL}/api/v1/limits/{user.id}")
            if resp.status_code == 404:
                await update.message.reply_text(
                    "You are not registered. Use /start first."
                )
                return
            resp.raise_for_status()
            limits = resp.json()
    except httpx.HTTPStatusError as exc:
        logger.error("limits_command: HTTP error: %s", exc)
        await update.message.reply_text("Failed to fetch limits. Please try again.")
        return
    except Exception as exc:
        logger.error("limits_command: error: %s", exc)
        await update.message.reply_text("An error occurred. Please try again.")
        return

    if not limits:
        await update.message.reply_text(
            "No limits set yet. Use /setlimit to set one."
        )
        return

    lines = ["📋 *Your app limits:*", ""]
    for lim in limits:
        lines.append(f"• *{lim['app_name']}*: {lim['daily_limit_mins']} min/day")

    await update.message.reply_text(
        "\n".join(lines), parse_mode="Markdown"
    )


# ---------------------------------------------------------------------------
# /link
# ---------------------------------------------------------------------------

async def link_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """/link — register this group and add the user as a member (group chats only)."""
    chat = update.effective_chat
    user = update.effective_user

    if chat is None or user is None:
        return

    if chat.type not in ("group", "supergroup"):
        await update.message.reply_text(
            "This command can only be used in a group chat."
        )
        return

    group_payload = {
        "telegram_chat_id": chat.id,
        "name": chat.title,
    }

    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            # Upsert group
            resp = await client.post(
                f"{_BASE_URL}/api/v1/groups", json=group_payload
            )
            if resp.status_code not in (200, 201):
                logger.warning(
                    "link_command: group upsert returned %s: %s",
                    resp.status_code,
                    resp.text,
                )

            # Upsert membership
            membership_payload = {
                "telegram_id": user.id,
                "group_telegram_chat_id": chat.id,
            }
            resp2 = await client.post(
                f"{_BASE_URL}/api/v1/groups/membership", json=membership_payload
            )
            if resp2.status_code not in (200, 201):
                logger.warning(
                    "link_command: membership upsert returned %s: %s",
                    resp2.status_code,
                    resp2.text,
                )
    except Exception as exc:
        logger.error("link_command: error: %s", exc)
        await update.message.reply_text(
            "Failed to link group. Make sure you are registered (/start) first."
        )
        return

    await update.message.reply_text(
        f"🔗 Group *{chat.title}* linked and you are registered as a member!",
        parse_mode="Markdown",
    )
