"""
Bot application factory.

Creates and configures the PTB Application with all handlers registered.
Initialization (which requires network) runs as a background task after
server startup — see app.main._background_init.
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


def create_application() -> Application:
    """Build and configure the PTB Application (no network calls).

    Registers all command and callback handlers but does NOT call
    application.initialize() or set the webhook — that is handled
    lazily on the first incoming webhook request.

    Returns
    -------
    The configured (but not yet initialised) Application instance.
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

    logger.info("create_application: handlers registered (init deferred)")
    return application


async def initialize_application(application: Application) -> None:
    """Initialise the Application and set the webhook (requires network).

    Called from the background init task after server startup.
    """
    settings = get_settings()

    await application.initialize()
    logger.info("initialize_application: bot initialised")

    if settings.WEBHOOK_URL:
        await application.bot.set_webhook(settings.WEBHOOK_URL, drop_pending_updates=True)
        logger.info("initialize_application: webhook set to %s", settings.WEBHOOK_URL)

        info = await application.bot.get_webhook_info()
        logger.info(
            "initialize_application: webhook info — url=%s, pending=%d, last_error=%s",
            info.url,
            info.pending_update_count,
            info.last_error_message,
        )
