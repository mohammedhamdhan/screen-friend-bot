"""
Leaderboard handler: /leaderboard (group chats only)
"""

import logging

import httpx
from telegram import Update
from telegram.ext import ContextTypes

logger = logging.getLogger(__name__)

_BASE_URL = "http://localhost:8000"


async def leaderboard_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """/leaderboard — fetch and display the weekly leaderboard (group only)."""
    chat = update.effective_chat
    if chat is None:
        return

    if chat.type not in ("group", "supergroup"):
        await update.message.reply_text(
            "This command can only be used in a group chat."
        )
        return

    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.get(
                f"{_BASE_URL}/api/v1/leaderboard/{chat.id}"
            )
            if resp.status_code == 404:
                await update.message.reply_text(
                    "This group is not registered. Use /link to register."
                )
                return
            resp.raise_for_status()
            data = resp.json()
    except httpx.HTTPStatusError as exc:
        logger.error("leaderboard_command: HTTP error: %s", exc)
        await update.message.reply_text(
            "Failed to fetch leaderboard. Please try again."
        )
        return
    except Exception as exc:
        logger.error("leaderboard_command: error: %s", exc)
        await update.message.reply_text("An error occurred. Please try again.")
        return

    formatted = data.get("formatted", "No leaderboard data available.")
    await update.message.reply_text(formatted, parse_mode="HTML")
