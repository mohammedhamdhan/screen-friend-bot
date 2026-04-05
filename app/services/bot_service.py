"""
Bot messaging service.

Uses raw httpx to call the Telegram Bot API directly.
Does NOT use the python-telegram-bot Application instance to avoid circular imports.
All functions are fire-and-forget async helpers called by FastAPI routers.
"""

import logging
import uuid
from typing import Optional

import httpx

from app.config import get_settings

logger = logging.getLogger(__name__)


def _base_url() -> str:
    token = get_settings().TELEGRAM_BOT_TOKEN
    return f"https://api.telegram.org/bot{token}"


async def post_request_to_group(
    group_chat_id: int | str,
    request_id: uuid.UUID | str | int,
    photo_url: str,
    requester_username: str,
    app_name: str,
    note: Optional[str] = None,
) -> Optional[int]:
    """Send a photo + inline vote buttons to the group for a new screen request.

    Parameters
    ----------
    group_chat_id:
        Target Telegram chat ID for the group.
    request_id:
        Primary key of the ScreenRequest row.
    photo_url:
        Public URL of the screenshot stored in R2.
    requester_username:
        Telegram username (without @) of the person who submitted the request.
    app_name:
        Name of the app/game being requested.
    note:
        Optional note attached to the request.

    Returns
    -------
    The Telegram message_id of the sent message, or None on failure.
    """
    caption_lines = [
        f"📱 *New screen request*",
        f"",
        f"👤 From: @{requester_username}",
        f"🎮 App: {app_name}",
    ]
    if note:
        caption_lines.append(f"📝 Note: {note}")

    caption = "\n".join(caption_lines)

    inline_keyboard = {
        "inline_keyboard": [
            [
                {
                    "text": "✅ Yes",
                    "callback_data": f"vote:{request_id}:1",
                },
                {
                    "text": "❌ No",
                    "callback_data": f"vote:{request_id}:0",
                },
            ]
        ]
    }

    payload = {
        "chat_id": group_chat_id,
        "photo": photo_url,
        "caption": caption,
        "parse_mode": "Markdown",
        "reply_markup": inline_keyboard,
    }

    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            resp = await client.post(f"{_base_url()}/sendPhoto", json=payload)
            resp.raise_for_status()
            data = resp.json()
            message_id: int = data["result"]["message_id"]
            logger.info(
                "post_request_to_group: sent request_id=%s, message_id=%s",
                request_id,
                message_id,
            )
            return message_id
    except Exception as exc:
        logger.error(
            "post_request_to_group failed for request_id=%s: %s", request_id, exc
        )
        return None


async def post_resolution(
    group_chat_id: int | str,
    request_id: uuid.UUID | str | int,
    status: str,
    message_id: Optional[int] = None,
    requester_username: Optional[str] = None,
    app_name: Optional[str] = None,
) -> None:
    """Edit (or send) a resolution message in the group after a vote concludes.

    If ``message_id`` is provided the original vote message caption is edited
    to reflect the outcome; otherwise a new message is sent.

    Parameters
    ----------
    group_chat_id:
        Target Telegram chat ID for the group.
    request_id:
        Primary key of the ScreenRequest row.
    status:
        Resolution status string, e.g. ``"approved"`` or ``"rejected"``.
    message_id:
        Telegram message_id of the original vote message to edit (optional).
    requester_username:
        Telegram username (without @) — used when sending a new message.
    app_name:
        App name — used when sending a new message.
    """
    status_lower = status.lower()
    if status_lower == "approved":
        verdict_line = "✅ *Approved* — screen time granted!"
    elif status_lower == "denied":
        verdict_line = "❌ *Denied* — request denied."
    else:
        verdict_line = f"ℹ️ *{status.capitalize()}*"

    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            if message_id:
                # Edit the caption and remove vote buttons
                payload = {
                    "chat_id": group_chat_id,
                    "message_id": message_id,
                    "caption": verdict_line,
                    "parse_mode": "Markdown",
                    "reply_markup": {"inline_keyboard": []},
                }
                resp = await client.post(
                    f"{_base_url()}/editMessageCaption", json=payload
                )
            else:
                # Fall back to sending a new text message
                lines = [verdict_line]
                if requester_username:
                    lines.append(f"👤 @{requester_username}")
                if app_name:
                    lines.append(f"🎮 {app_name}")
                lines.append(f"_(request #{request_id})_")

                payload = {
                    "chat_id": group_chat_id,
                    "text": "\n".join(lines),
                    "parse_mode": "Markdown",
                }
                resp = await client.post(f"{_base_url()}/sendMessage", json=payload)

            resp.raise_for_status()
            logger.info(
                "post_resolution: request_id=%s status=%s", request_id, status
            )
    except Exception as exc:
        logger.error(
            "post_resolution failed for request_id=%s: %s", request_id, exc
        )


async def dm_user(telegram_id: int | str, text: str) -> None:
    """Send a direct message to a Telegram user.

    Parameters
    ----------
    telegram_id:
        The recipient's Telegram user ID (numeric chat_id for a DM).
    text:
        Message text (HTML parse mode is used).
    """
    payload = {
        "chat_id": telegram_id,
        "text": text,
        "parse_mode": "HTML",
    }

    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            resp = await client.post(f"{_base_url()}/sendMessage", json=payload)
            resp.raise_for_status()
            logger.info("dm_user: sent DM to telegram_id=%s", telegram_id)
    except Exception as exc:
        logger.error("dm_user failed for telegram_id=%s: %s", telegram_id, exc)


async def post_confession(
    group_chat_id: int | str,
    username: str,
    app: str,
    note: Optional[str] = None,
) -> None:
    """Post a confession / check-in message to the group.

    Parameters
    ----------
    group_chat_id:
        Target Telegram chat ID for the group.
    username:
        Telegram username (without @) of the person confessing.
    app:
        App/game that was used without permission.
    note:
        Optional note from the user.
    """
    lines = [
        "🙏 *Confession*",
        "",
        f"👤 @{username} used *{app}* without a vote.",
    ]
    if note:
        lines.append(f"📝 \"{note}\"")

    payload = {
        "chat_id": group_chat_id,
        "text": "\n".join(lines),
        "parse_mode": "Markdown",
    }

    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            resp = await client.post(f"{_base_url()}/sendMessage", json=payload)
            resp.raise_for_status()
            logger.info(
                "post_confession: sent confession for username=%s app=%s",
                username,
                app,
            )
    except Exception as exc:
        logger.error(
            "post_confession failed for username=%s: %s", username, exc
        )


async def post_screenshot_request(
    group_chat_id: int | str, mention_text: str
) -> Optional[int]:
    """Send a screenshot collection prompt to the group.

    Returns the Telegram message_id of the sent message, or None on failure.
    """
    text = (
        "📸 <b>Daily Screen Time Check-in</b>\n\n"
        f"{mention_text}\n\n"
        "Please send a screenshot of your screen time report. "
        "I'll automatically check your usage against your limits!\n\n"
        "⏳ You have 60 minutes to submit."
    )

    payload = {
        "chat_id": group_chat_id,
        "text": text,
        "parse_mode": "HTML",
    }

    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            resp = await client.post(f"{_base_url()}/sendMessage", json=payload)
            resp.raise_for_status()
            data = resp.json()
            message_id: int = data["result"]["message_id"]
            logger.info(
                "post_screenshot_request: sent to chat_id=%s, message_id=%s",
                group_chat_id,
                message_id,
            )
            return message_id
    except Exception as exc:
        logger.error(
            "post_screenshot_request failed for chat_id=%s: %s", group_chat_id, exc
        )
        return None


async def post_ocr_result(
    group_chat_id: int | str,
    username: str,
    stayed_clean: bool,
    app_details: str,
) -> None:
    """Post the OCR check-in result to the group."""
    if stayed_clean:
        text = f"✅ @{username} stayed clean! {app_details}"
    else:
        text = f"❌ @{username} slipped: {app_details}"

    payload = {
        "chat_id": group_chat_id,
        "text": text,
        "parse_mode": "HTML",
    }

    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            resp = await client.post(f"{_base_url()}/sendMessage", json=payload)
            resp.raise_for_status()
            logger.info("post_ocr_result: sent for username=%s", username)
    except Exception as exc:
        logger.error("post_ocr_result failed for username=%s: %s", username, exc)


async def post_manual_fallback(
    group_chat_id: int | str,
    user_telegram_id: int,
    username: str,
) -> None:
    """Send manual check-in buttons for a user who didn't submit a screenshot."""
    text = (
        f"⏰ @{username}, time's up! "
        "You didn't send a screenshot. Please check in manually:"
    )

    inline_keyboard = {
        "inline_keyboard": [
            [
                {
                    "text": "✅ Stayed clean",
                    "callback_data": f"screencheckin:{user_telegram_id}:clean",
                },
                {
                    "text": "😔 Slipped",
                    "callback_data": f"screencheckin:{user_telegram_id}:slipped",
                },
            ]
        ]
    }

    payload = {
        "chat_id": group_chat_id,
        "text": text,
        "reply_markup": inline_keyboard,
    }

    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            resp = await client.post(f"{_base_url()}/sendMessage", json=payload)
            resp.raise_for_status()
            logger.info(
                "post_manual_fallback: sent for telegram_id=%s", user_telegram_id
            )
    except Exception as exc:
        logger.error(
            "post_manual_fallback failed for telegram_id=%s: %s",
            user_telegram_id,
            exc,
        )


async def post_weekly_screenshot_request(
    group_chat_id: int | str, mention_text: str
) -> Optional[int]:
    """Send a weekly screenshot collection prompt to the group.

    Returns the Telegram message_id of the sent message, or None on failure.
    """
    text = (
        "📸 <b>Weekly Screen Time Check-in</b>\n\n"
        f"{mention_text}\n\n"
        "Please send a screenshot of your <b>WEEKLY</b> screen time report. "
        "I'll compare it against your daily check-ins to make sure everything lines up!\n\n"
        "⏳ You have 2 hours to submit."
    )

    payload = {
        "chat_id": group_chat_id,
        "text": text,
        "parse_mode": "HTML",
    }

    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            resp = await client.post(f"{_base_url()}/sendMessage", json=payload)
            resp.raise_for_status()
            data = resp.json()
            message_id: int = data["result"]["message_id"]
            logger.info(
                "post_weekly_screenshot_request: sent to chat_id=%s, message_id=%s",
                group_chat_id,
                message_id,
            )
            return message_id
    except Exception as exc:
        logger.error(
            "post_weekly_screenshot_request failed for chat_id=%s: %s",
            group_chat_id,
            exc,
        )
        return None


async def post_weekly_collation_result(
    group_chat_id: int | str,
    username: str,
    passed: bool,
    discrepancy_minutes: int,
) -> None:
    """Post the weekly collation result to the group."""
    if passed:
        text = (
            f"✅ @{username} — weekly check-in passed! "
            "Your weekly totals match your daily check-ins. Keep it up! 🎉"
        )
    else:
        text = (
            f"⚠️ @{username} — weekly check-in found a discrepancy of "
            f"<b>{discrepancy_minutes} extra minutes</b> beyond your daily check-ins.\n\n"
            "It looks like some screen time happened after your daily check-ins. "
            "Your streak has been reset — but nobody succeeds at first! "
            "Try setting smaller goals and building up, or consider changing your check-in time "
            "to later in the day. You've got this! 💪"
        )

    payload = {
        "chat_id": group_chat_id,
        "text": text,
        "parse_mode": "HTML",
    }

    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            resp = await client.post(f"{_base_url()}/sendMessage", json=payload)
            resp.raise_for_status()
            logger.info("post_weekly_collation_result: sent for username=%s", username)
    except Exception as exc:
        logger.error(
            "post_weekly_collation_result failed for username=%s: %s", username, exc
        )


async def post_weekly_manual_fallback(
    group_chat_id: int | str,
    user_telegram_id: int,
    username: str,
) -> None:
    """Send weekly manual check-in buttons for a user who didn't submit a weekly screenshot."""
    text = (
        f"⏰ @{username}, time's up! "
        "You didn't send a weekly screenshot. Please check in manually:"
    )

    inline_keyboard = {
        "inline_keyboard": [
            [
                {
                    "text": "✅ Submitted",
                    "callback_data": f"weeklyscreencheckin:{user_telegram_id}:submitted",
                },
                {
                    "text": "⏭️ Skip",
                    "callback_data": f"weeklyscreencheckin:{user_telegram_id}:skipped",
                },
            ]
        ]
    }

    payload = {
        "chat_id": group_chat_id,
        "text": text,
        "reply_markup": inline_keyboard,
    }

    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            resp = await client.post(f"{_base_url()}/sendMessage", json=payload)
            resp.raise_for_status()
            logger.info(
                "post_weekly_manual_fallback: sent for telegram_id=%s",
                user_telegram_id,
            )
    except Exception as exc:
        logger.error(
            "post_weekly_manual_fallback failed for telegram_id=%s: %s",
            user_telegram_id,
            exc,
        )


async def post_leaderboard(group_chat_id: int | str, message: str) -> None:
    """Post a pre-formatted leaderboard message to the group.

    Parameters
    ----------
    group_chat_id:
        Target Telegram chat ID for the group.
    message:
        Fully-formatted leaderboard text (HTML parse mode is used).
    """
    payload = {
        "chat_id": group_chat_id,
        "text": message,
        "parse_mode": "HTML",
    }

    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            resp = await client.post(f"{_base_url()}/sendMessage", json=payload)
            resp.raise_for_status()
            logger.info("post_leaderboard: sent leaderboard to chat_id=%s", group_chat_id)
    except Exception as exc:
        logger.error(
            "post_leaderboard failed for chat_id=%s: %s", group_chat_id, exc
        )
