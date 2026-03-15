"""
Admin handlers: /setup, /members, /kick — restricted to group admins.
"""

import logging

import httpx
from telegram import Update
from telegram.constants import ChatMemberStatus
from telegram.ext import ContextTypes

logger = logging.getLogger(__name__)

_BASE_URL = "http://localhost:8000"

_ADMIN_STATUSES = {
    ChatMemberStatus.ADMINISTRATOR,
    ChatMemberStatus.OWNER,
}


async def _is_admin(update: Update, context: ContextTypes.DEFAULT_TYPE) -> bool:
    """Return True if the effective user is an admin/owner of the current chat."""
    user = update.effective_user
    chat = update.effective_chat
    if user is None or chat is None:
        return False
    try:
        member = await context.bot.get_chat_member(chat.id, user.id)
        return member.status in _ADMIN_STATUSES
    except Exception as exc:
        logger.warning("_is_admin: could not check admin status: %s", exc)
        return False


async def setup_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """/setup — configure the group (admins only)."""
    chat = update.effective_chat
    if chat is None:
        return

    if chat.type not in ("group", "supergroup"):
        await update.message.reply_text("This command can only be used in a group chat.")
        return

    if not await _is_admin(update, context):
        await update.message.reply_text("Only group admins can use this command.")
        return

    args = context.args or []
    # Usage: /setup [vote_threshold <n>]
    if len(args) == 2 and args[0] == "vote_threshold":
        try:
            threshold = int(args[1])
        except ValueError:
            await update.message.reply_text("Vote threshold must be a whole number.")
            return

        payload = {
            "telegram_chat_id": chat.id,
            "vote_threshold": threshold,
        }
        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                resp = await client.patch(
                    f"{_BASE_URL}/api/v1/groups/{chat.id}", json=payload
                )
                if resp.status_code == 404:
                    await update.message.reply_text(
                        "Group not registered. Use /link first."
                    )
                    return
                resp.raise_for_status()
        except Exception as exc:
            logger.error("setup_command: error updating group: %s", exc)
            await update.message.reply_text("Failed to update group settings.")
            return

        await update.message.reply_text(
            f"✅ Vote threshold set to {threshold}."
        )
    else:
        # Show current settings
        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                resp = await client.get(f"{_BASE_URL}/api/v1/groups/{chat.id}")
                if resp.status_code == 404:
                    await update.message.reply_text(
                        "Group not registered. Use /link first."
                    )
                    return
                resp.raise_for_status()
                data = resp.json()
        except Exception as exc:
            logger.error("setup_command: error fetching group: %s", exc)
            await update.message.reply_text("Failed to fetch group settings.")
            return

        threshold = data.get("vote_threshold", 1)
        await update.message.reply_text(
            f"⚙️ *Group Settings*\n\n"
            f"• Vote threshold: {threshold}\n\n"
            "To change: /setup vote_threshold <n>",
            parse_mode="Markdown",
        )


async def members_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """/members — list registered members of this group (admins only)."""
    chat = update.effective_chat
    if chat is None:
        return

    if chat.type not in ("group", "supergroup"):
        await update.message.reply_text("This command can only be used in a group chat.")
        return

    if not await _is_admin(update, context):
        await update.message.reply_text("Only group admins can use this command.")
        return

    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.get(f"{_BASE_URL}/api/v1/groups/{chat.id}/members")
            if resp.status_code == 404:
                await update.message.reply_text(
                    "Group not registered. Use /link first."
                )
                return
            resp.raise_for_status()
            members = resp.json()
    except Exception as exc:
        logger.error("members_command: error: %s", exc)
        await update.message.reply_text("Failed to fetch members. Please try again.")
        return

    if not members:
        await update.message.reply_text(
            "No registered members in this group yet."
        )
        return

    lines = [f"👥 *Registered members ({len(members)}):*", ""]
    for member in members:
        username = member.get("username") or f"user_{member.get('telegram_id')}"
        streak = member.get("streak", 0)
        lines.append(f"• @{username} — 🔥 {streak} day streak")

    await update.message.reply_text("\n".join(lines), parse_mode="Markdown")


async def kick_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """/kick @username — remove a user from the group's membership (admins only)."""
    chat = update.effective_chat
    if chat is None:
        return

    if chat.type not in ("group", "supergroup"):
        await update.message.reply_text("This command can only be used in a group chat.")
        return

    if not await _is_admin(update, context):
        await update.message.reply_text("Only group admins can use this command.")
        return

    args = context.args or []
    if not args:
        await update.message.reply_text("Usage: /kick @username")
        return

    target_username = args[0].lstrip("@")

    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.delete(
                f"{_BASE_URL}/api/v1/groups/{chat.id}/members/{target_username}"
            )
            if resp.status_code == 404:
                await update.message.reply_text(
                    f"@{target_username} is not a registered member of this group."
                )
                return
            resp.raise_for_status()
    except Exception as exc:
        logger.error("kick_command: error: %s", exc)
        await update.message.reply_text("Failed to remove member. Please try again.")
        return

    await update.message.reply_text(
        f"✅ @{target_username} has been removed from this group's membership."
    )
