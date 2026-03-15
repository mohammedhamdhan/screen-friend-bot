"""
Bot application factory.

Creates and configures the PTB Application, registers all handlers,
and sets the Telegram webhook. Returns the Application instance for
use in the FastAPI lifespan.
"""

import logging

from telegram.ext import (
    Application,
    ApplicationBuilder,
    CallbackQueryHandler,
    CommandHandler,
)

from app.config import get_settings

logger = logging.getLogger(__name__)


async def create_application() -> Application:
    """Build, configure and initialise the PTB Application.

    - Registers all command and callback handlers.
    - Sets the Telegram webhook.
    - Calls application.initialize() (does NOT call start() — updates are
      delivered via webhook, not polling).

    Returns
    -------
    The fully initialised Application instance.
    """
    settings = get_settings()

    application: Application = (
        ApplicationBuilder()
        .token(settings.TELEGRAM_BOT_TOKEN)
        .connect_timeout(30)
        .read_timeout(30)
        .build()
    )

    # ------------------------------------------------------------------
    # Register handlers
    # ------------------------------------------------------------------

    # Setup handlers
    from bot.handlers.setup import (
        link_command,
        limits_command,
        setlimit_command,
        start_command,
    )

    application.add_handler(CommandHandler("start", start_command))
    application.add_handler(CommandHandler("setlimit", setlimit_command))
    application.add_handler(CommandHandler("limits", limits_command))
    application.add_handler(CommandHandler("link", link_command))

    # Request conversation handler (/more flow)
    from bot.handlers.requests import build_conversation_handler

    application.add_handler(build_conversation_handler())

    # Social handlers
    from bot.handlers.social import (
        checkin_command,
        confess_command,
        history_command,
        streak_command,
    )

    application.add_handler(CommandHandler("checkin", checkin_command))
    application.add_handler(CommandHandler("confess", confess_command))
    application.add_handler(CommandHandler("streak", streak_command))
    application.add_handler(CommandHandler("history", history_command))

    # Leaderboard handler
    from bot.handlers.leaderboard import leaderboard_command

    application.add_handler(CommandHandler("leaderboard", leaderboard_command))

    # Admin handlers
    from bot.handlers.admin import (
        kick_command,
        members_command,
        setup_command,
    )

    application.add_handler(CommandHandler("setup", setup_command))
    application.add_handler(CommandHandler("members", members_command))
    application.add_handler(CommandHandler("kick", kick_command))

    # Catch-all callback query handler (vote, checkin, react prefixes)
    # Must be registered AFTER the ConversationHandler so that duration:
    # callbacks are consumed by the conversation first.
    from bot.handlers.callbacks import callback_handler

    application.add_handler(CallbackQueryHandler(callback_handler))

    # ------------------------------------------------------------------
    # Initialise and set webhook
    # ------------------------------------------------------------------
    import asyncio

    for attempt in range(1, 4):
        try:
            await application.initialize()
            logger.info("create_application: bot initialised (attempt %d)", attempt)
            break
        except Exception as exc:
            logger.warning(
                "create_application: initialize attempt %d failed: %s", attempt, exc
            )
            if attempt < 3:
                await asyncio.sleep(5 * attempt)
            else:
                logger.error(
                    "create_application: all init attempts failed — "
                    "bot will not be available until restart"
                )
                return application

    if settings.WEBHOOK_URL:
        try:
            await application.bot.set_webhook(settings.WEBHOOK_URL)
            logger.info("create_application: webhook set to %s", settings.WEBHOOK_URL)
        except Exception as exc:
            logger.error("create_application: failed to set webhook: %s", exc)

    return application
