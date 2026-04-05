"""
Requests handler: /more <app> [minutes] → ConversationHandler
States: CHOOSING_DURATION → WAITING_FOR_PHOTO → END
"""

import logging
import os

import httpx
from telegram import Update
from telegram.ext import (
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
    ConversationHandler,
    MessageHandler,
    filters,
)

from bot.keyboards import duration_keyboard

logger = logging.getLogger(__name__)

_BASE_URL = f"http://127.0.0.1:{os.environ.get('PORT', '8000')}"

# ConversationHandler states
CHOOSING_APP, CHOOSING_DURATION, WAITING_FOR_PHOTO = range(3)

# Context user_data keys
_KEY_APP = "more_app"
_KEY_MINUTES = "more_minutes"
_KEY_GROUP_CHAT_ID = "more_group_chat_id"


async def more_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """/more <app> [minutes] — start a screen-time request."""
    user = update.effective_user
    chat = update.effective_chat
    if user is None or chat is None:
        return ConversationHandler.END

    args = context.args or []
    if not args:
        await update.message.reply_text(
            "Which app do you want extra time for?\n"
            "(e.g. Instagram, YouTube, TikTok)"
        )
        return CHOOSING_APP

    app_name = args[0]
    context.user_data[_KEY_APP] = app_name
    context.user_data[_KEY_GROUP_CHAT_ID] = chat.id

    if len(args) >= 2:
        try:
            minutes = int(args[1])
        except ValueError:
            await update.message.reply_text("Minutes must be a whole number.")
            return ConversationHandler.END

        if minutes <= 0:
            await update.message.reply_text("Minutes must be greater than 0.")
            return ConversationHandler.END

        context.user_data[_KEY_MINUTES] = minutes
        await update.message.reply_text(
            f"📸 Now send a screenshot showing your {app_name} usage.\n"
            "Send /cancel to abort."
        )
        return WAITING_FOR_PHOTO
    else:
        await update.message.reply_text(
            f"How many extra minutes do you want for *{app_name}*?",
            parse_mode="Markdown",
            reply_markup=duration_keyboard(),
        )
        return CHOOSING_DURATION


async def app_chosen(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Handle app name typed by user."""
    user = update.effective_user
    chat = update.effective_chat
    if user is None or chat is None:
        return ConversationHandler.END

    app_name = update.message.text.strip()
    if not app_name:
        await update.message.reply_text("Please enter an app name.")
        return CHOOSING_APP

    context.user_data[_KEY_APP] = app_name
    context.user_data[_KEY_GROUP_CHAT_ID] = chat.id

    await update.message.reply_text(
        f"How many extra minutes do you want for *{app_name}*?",
        parse_mode="Markdown",
        reply_markup=duration_keyboard(),
    )
    return CHOOSING_DURATION


async def duration_chosen(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Handle duration selection from inline keyboard."""
    query = update.callback_query
    if query is None:
        return ConversationHandler.END

    await query.answer()
    data = query.data  # format: "duration:<minutes>"
    try:
        minutes = int(data.split(":")[1])
    except (IndexError, ValueError):
        await query.edit_message_text("Invalid selection. Please try again.")
        return ConversationHandler.END

    context.user_data[_KEY_MINUTES] = minutes
    app_name = context.user_data.get(_KEY_APP, "the app")

    await query.edit_message_text(
        f"⏱ {minutes} minutes selected for *{app_name}*.\n\n"
        "📸 Now send a screenshot showing your usage.\n"
        "Send /cancel to abort.",
        parse_mode="Markdown",
    )
    return WAITING_FOR_PHOTO


async def photo_received(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Handle photo upload, then create the request via API."""
    user = update.effective_user
    chat = update.effective_chat
    message = update.message

    if user is None or chat is None or message is None:
        return ConversationHandler.END

    app_name = context.user_data.get(_KEY_APP)
    minutes = context.user_data.get(_KEY_MINUTES)
    group_chat_id = context.user_data.get(_KEY_GROUP_CHAT_ID)

    if not app_name or not minutes or not group_chat_id:
        await message.reply_text("Session expired. Please start again with /more.")
        return ConversationHandler.END

    # Get the largest available photo
    photo = message.photo[-1] if message.photo else None
    if photo is None:
        await message.reply_text("Please send a photo.")
        return WAITING_FOR_PHOTO

    photo_url: str | None = None
    try:
        photo_file = await context.bot.get_file(photo.file_id)
        file_bytes = await photo_file.download_as_bytearray()

        from app.services.storage_service import upload_photo

        photo_url = await upload_photo(bytes(file_bytes), f"{photo.file_id}.jpg")
    except Exception as exc:
        logger.error("photo_received: failed to upload photo: %s", exc)
        await message.reply_text(
            "Failed to upload your screenshot. Please try again."
        )
        return ConversationHandler.END

    caption = message.caption  # optional text from the photo message

    payload = {
        "telegram_id": user.id,
        "group_telegram_chat_id": group_chat_id,
        "app_name": app_name,
        "minutes_requested": minutes,
        "photo_url": photo_url,
        "caption": caption,
    }

    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            resp = await client.post(f"{_BASE_URL}/api/v1/requests", json=payload)
            if resp.status_code == 404:
                await message.reply_text(
                    "You or the group is not registered. "
                    "Use /start and /link first."
                )
                return ConversationHandler.END
            if resp.status_code == 403:
                await message.reply_text(
                    "You are not a member of this group. Use /link first."
                )
                return ConversationHandler.END
            if resp.status_code == 409:
                await message.reply_text(
                    "You already have a pending request in this group."
                )
                return ConversationHandler.END
            if resp.status_code == 429:
                data = resp.json()
                await message.reply_text(
                    f"⏳ Cooldown: {data.get('detail', 'Please wait before requesting again.')}"
                )
                return ConversationHandler.END
            resp.raise_for_status()
    except httpx.HTTPStatusError as exc:
        logger.error("photo_received: HTTP error: %s", exc)
        await message.reply_text("Failed to submit request. Please try again.")
        return ConversationHandler.END
    except Exception as exc:
        logger.error("photo_received: error: %s", exc)
        await message.reply_text("An error occurred. Please try again.")
        return ConversationHandler.END

    await message.reply_text(
        f"✅ Your request for *{app_name}* ({minutes} min) has been submitted!\n"
        "Your group will vote on it.",
        parse_mode="Markdown",
    )

    # Clean up user_data
    for key in (_KEY_APP, _KEY_MINUTES, _KEY_GROUP_CHAT_ID):
        context.user_data.pop(key, None)

    return ConversationHandler.END


async def cancel_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """/cancel — abort the current conversation."""
    for key in (_KEY_APP, _KEY_MINUTES, _KEY_GROUP_CHAT_ID):
        context.user_data.pop(key, None)

    await update.message.reply_text("Cancelled.")
    return ConversationHandler.END


def build_conversation_handler() -> ConversationHandler:
    """Return the ConversationHandler for the /more flow."""
    return ConversationHandler(
        entry_points=[CommandHandler("more", more_command)],
        states={
            CHOOSING_APP: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, app_chosen),
            ],
            CHOOSING_DURATION: [
                CallbackQueryHandler(duration_chosen, pattern=r"^duration:\d+$")
            ],
            WAITING_FOR_PHOTO: [
                MessageHandler(filters.PHOTO, photo_received)
            ],
        },
        fallbacks=[CommandHandler("cancel", cancel_command)],
        per_user=True,
        per_chat=True,
        per_message=False,
    )
