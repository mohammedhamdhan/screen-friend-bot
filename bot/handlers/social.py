"""
Social handlers: /checkin, /confess, /streak, /history
"""

import logging

import httpx
from telegram import Update
from telegram.ext import ContextTypes

from bot.keyboards import checkin_keyboard

logger = logging.getLogger(__name__)

_BASE_URL = "http://localhost:8000"


async def checkin_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """/checkin — prompt user to log their daily check-in."""
    user = update.effective_user
    if user is None:
        return

    name = user.first_name or user.username or "you"
    await update.message.reply_text(
        f"📅 Hey {name}, how did today go?",
        reply_markup=checkin_keyboard(user_id=user.id),
    )


async def confess_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """/confess <app> [note] — confess you used an app without a vote."""
    user = update.effective_user
    chat = update.effective_chat
    if user is None or chat is None:
        return

    args = context.args or []
    if not args:
        await update.message.reply_text(
            "Usage: /confess <app_name> [optional note]\n"
            "Example: /confess Instagram I just couldn't help it"
        )
        return

    app_name = args[0]
    note = " ".join(args[1:]) if len(args) > 1 else None

    payload = {
        "telegram_id": user.id,
        "stayed_clean": False,
        "confession_note": note,
    }

    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.post(f"{_BASE_URL}/api/v1/checkins", json=payload)
            if resp.status_code == 404:
                await update.message.reply_text(
                    "You are not registered. Use /start first."
                )
                return
            resp.raise_for_status()
    except Exception as exc:
        logger.error("confess_command: error logging checkin: %s", exc)

    # Post confession to group if in a group chat
    if chat.type in ("group", "supergroup"):
        username = user.username or user.first_name or str(user.id)
        lines = ["🙏 *Confession*", "", f"👤 @{username} used *{app_name}* without a vote."]
        if note:
            lines.append(f'📝 "{note}"')

        try:
            await update.message.reply_text(
                "\n".join(lines), parse_mode="Markdown"
            )
        except Exception as exc:
            logger.error("confess_command: failed to post confession: %s", exc)
    else:
        await update.message.reply_text(
            f"Confession logged for *{app_name}*. "
            "Use this command in your group to share with your accountability partners.",
            parse_mode="Markdown",
        )


async def streak_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """/streak — show the user's current clean streak."""
    user = update.effective_user
    if user is None:
        return

    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.get(f"{_BASE_URL}/api/v1/auth/profile/{user.id}")
            if resp.status_code == 404:
                await update.message.reply_text(
                    "You are not registered. Use /start first."
                )
                return
            resp.raise_for_status()
            data = resp.json()
    except Exception as exc:
        logger.error("streak_command: error: %s", exc)
        await update.message.reply_text("Failed to fetch streak. Please try again.")
        return

    streak = data.get("streak", 0)
    if streak == 0:
        msg = "Your current streak is 0 days. Start fresh today with /checkin!"
    elif streak == 1:
        msg = "🔥 1 day clean! Keep it up!"
    else:
        msg = f"🔥 {streak} days clean! Amazing streak!"

    await update.message.reply_text(msg)


async def history_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """/history — show the user's recent check-in history."""
    user = update.effective_user
    if user is None:
        return

    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.get(f"{_BASE_URL}/api/v1/checkins/{user.id}")
            if resp.status_code == 404:
                await update.message.reply_text(
                    "You are not registered. Use /start first."
                )
                return
            resp.raise_for_status()
            records = resp.json()
    except Exception as exc:
        logger.error("history_command: error: %s", exc)
        await update.message.reply_text("Failed to fetch history. Please try again.")
        return

    if not records:
        await update.message.reply_text(
            "No check-in history yet. Use /checkin to log your first day!"
        )
        return

    lines = ["📅 *Your recent check-ins:*", ""]
    for record in records[:10]:  # Show last 10
        date_str = record.get("date", "?")
        clean = record.get("stayed_clean", False)
        icon = "✅" if clean else "❌"
        note = record.get("confession_note")
        line = f"{icon} {date_str}"
        if note:
            line += f' — "{note}"'
        lines.append(line)

    await update.message.reply_text("\n".join(lines), parse_mode="Markdown")
