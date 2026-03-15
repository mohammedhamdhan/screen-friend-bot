import logging

from fastapi import APIRouter, Request, Response, status

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/webhook", tags=["webhook"])

_bot_initialized = False


@router.post("/telegram", status_code=status.HTTP_200_OK)
async def telegram_webhook(request: Request):
    """Receive Telegram updates and pass them to the PTB Application.

    On the first request, lazily initialises the Application (which
    requires outbound network to api.telegram.org).  By the time
    Telegram sends us an update, outbound networking is guaranteed to
    be working.
    """
    global _bot_initialized

    application = getattr(request.app.state, "application", None)

    if application is None:
        logger.warning("telegram_webhook: app.state.application is not set, ignoring update")
        return Response(status_code=status.HTTP_200_OK)

    # Lazy initialization: run once on the first webhook request
    if not _bot_initialized:
        try:
            from bot.main import initialize_application

            await initialize_application(application)
            _bot_initialized = True
            logger.info("telegram_webhook: lazy init succeeded")
        except Exception as exc:
            logger.error("telegram_webhook: lazy init failed: %s", exc)
            return Response(status_code=status.HTTP_200_OK)

    try:
        data = await request.json()
        from telegram import Update

        update = Update.de_json(data, application.bot)
        await application.process_update(update)
    except Exception as exc:
        logger.error("telegram_webhook: error processing update: %s", exc)

    # Always return 200 to Telegram so it does not retry
    return Response(status_code=status.HTTP_200_OK)
