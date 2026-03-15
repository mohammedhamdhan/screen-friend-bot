import logging

from fastapi import APIRouter, Request, Response, status

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/webhook", tags=["webhook"])


@router.post("/telegram", status_code=status.HTTP_200_OK)
async def telegram_webhook(request: Request):
    """Receive Telegram updates and pass them to the PTB Application."""
    logger.info("telegram_webhook: received POST request")
    application = getattr(request.app.state, "application", None)

    if application is None:
        logger.warning("telegram_webhook: app.state.application is not set, ignoring update")
        return Response(status_code=status.HTTP_200_OK)

    # Guard: background init may not have completed yet
    if not getattr(request.app.state, "bot_initialized", False):
        logger.warning("telegram_webhook: application not yet initialized, ignoring update")
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
