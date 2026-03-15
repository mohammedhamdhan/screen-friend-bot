"""
Setup handlers: /start, /setlimit, /limits, /link
"""

import logging
import os

import httpx
from telegram import Update
from telegram.ext import ContextTypes

logger = logging.getLogger(__name__)

_BASE_URL = f"http://localhost:{os.environ.get('PORT', '8000')}"


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
        "• /setlimit <app> <minutes> — set your daily limit\n"
        "• /limits — view your limits\n"
        "• /more <app> [minutes] — request extra time\n"
        "• /checkin — log your daily check-in\n"
        "• /streak — view your streak\n"
        "• /leaderboard — group leaderboard\n"
        "• /link — link this group (admins only)"
    )


async def setlimit_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """/setlimit <app> <minutes> — set a daily app limit."""
    user = update.effective_user
    if user is None:
        return

    args = context.args or []
    if len(args) < 2:
        await update.message.reply_text(
            "Usage: /setlimit <app_name> <daily_minutes>\n"
            "Example: /setlimit Instagram 30"
        )
        return

    app_name = args[0]
    try:
        daily_limit_mins = int(args[1])
    except ValueError:
        await update.message.reply_text("Minutes must be a whole number.")
        return

    if daily_limit_mins <= 0:
        await update.message.reply_text("Minutes must be greater than 0.")
        return

    payload = {
        "telegram_id": user.id,
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
            resp.raise_for_status()
    except httpx.HTTPStatusError as exc:
        logger.error("setlimit_command: HTTP error: %s", exc)
        await update.message.reply_text("Failed to set limit. Please try again.")
        return
    except Exception as exc:
        logger.error("setlimit_command: error: %s", exc)
        await update.message.reply_text("An error occurred. Please try again.")
        return

    await update.message.reply_text(
        f"✅ Limit set: {app_name} → {daily_limit_mins} min/day"
    )


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
            "No limits set yet. Use /setlimit <app> <minutes> to set one."
        )
        return

    lines = ["📋 *Your app limits:*", ""]
    for lim in limits:
        lines.append(f"• *{lim['app_name']}*: {lim['daily_limit_mins']} min/day")

    await update.message.reply_text(
        "\n".join(lines), parse_mode="Markdown"
    )


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
