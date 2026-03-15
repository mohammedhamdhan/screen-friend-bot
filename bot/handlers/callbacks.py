"""
Callback query handlers.

Prefix routing:
  vote:{request_id}:{decision}              → POST /api/v1/votes
  checkin:clean                             → log clean day for calling user
  checkin:<user_id>:clean                   → log clean day for given user
  checkin:slipped                           → prompt for confession
  checkin:<user_id>:slipped                 → prompt for confession for given user
  react:{request_id}:{reaction}             → fire-and-forget engagement log
  duration:<minutes>                        → handled by ConversationHandler (ignored here)
"""

import logging

import httpx
from telegram import Update
from telegram.ext import ContextTypes

logger = logging.getLogger(__name__)

_BASE_URL = "http://localhost:8000"


async def callback_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Route all callback queries by prefix."""
    query = update.callback_query
    if query is None:
        return

    data = query.data or ""

    if data.startswith("vote:"):
        await _handle_vote(query, update, context)
    elif data.startswith("checkin:"):
        await _handle_checkin(query, update, context)
    elif data.startswith("react:"):
        await _handle_react(query, update, context)
    else:
        # Unknown / handled elsewhere (e.g. duration: by ConversationHandler)
        await query.answer()


# ---------------------------------------------------------------------------
# Vote
# ---------------------------------------------------------------------------

async def _handle_vote(query, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle vote:{request_id}:{decision} callbacks."""
    voter = update.effective_user
    if voter is None:
        await query.answer("Could not identify you.", show_alert=True)
        return

    parts = (query.data or "").split(":")
    if len(parts) != 3:
        await query.answer("Invalid vote data.", show_alert=True)
        return

    _, request_id, decision_str = parts
    try:
        decision = int(decision_str)
    except ValueError:
        await query.answer("Invalid decision value.", show_alert=True)
        return

    payload = {
        "request_id": request_id,
        "voter_telegram_id": voter.id,
        "decision": decision,
    }

    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.post(f"{_BASE_URL}/api/v1/votes", json=payload)

            if resp.status_code == 403:
                detail = resp.json().get("detail", "You cannot vote on this request.")
                await query.answer(detail, show_alert=True)
                return
            if resp.status_code == 409:
                detail = resp.json().get("detail", "This request is no longer pending.")
                await query.answer(detail, show_alert=True)
                return
            if resp.status_code == 404:
                await query.answer("Request or user not found.", show_alert=True)
                return
            resp.raise_for_status()
    except httpx.HTTPStatusError as exc:
        logger.error("_handle_vote: HTTP error: %s", exc)
        await query.answer("Failed to record vote. Please try again.", show_alert=True)
        return
    except Exception as exc:
        logger.error("_handle_vote: error: %s", exc)
        await query.answer("An error occurred.", show_alert=True)
        return

    label = "✅ Yes" if decision else "❌ No"
    await query.answer(f"Your vote ({label}) has been recorded!")


# ---------------------------------------------------------------------------
# Check-in
# ---------------------------------------------------------------------------

async def _handle_checkin(query, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle checkin:clean / checkin:slipped callbacks."""
    user = update.effective_user
    if user is None:
        await query.answer()
        return

    data = query.data or ""
    # Possible formats:
    #   checkin:clean
    #   checkin:slipped
    #   checkin:<user_id>:clean
    #   checkin:<user_id>:slipped
    parts = data.split(":")
    if len(parts) == 2:
        # checkin:clean or checkin:slipped
        action = parts[1]
        target_user_id = user.id
    elif len(parts) == 3:
        # checkin:<user_id>:clean or checkin:<user_id>:slipped
        try:
            target_user_id = int(parts[1])
        except ValueError:
            await query.answer("Invalid check-in data.", show_alert=True)
            return
        action = parts[2]
    else:
        await query.answer("Invalid check-in data.", show_alert=True)
        return

    # Only allow the intended user to respond
    if target_user_id != user.id:
        await query.answer("This check-in is not for you.", show_alert=True)
        return

    if action == "clean":
        await _log_checkin(query, update, context, user_id=user.id, stayed_clean=True)
    elif action == "slipped":
        await query.answer()
        await _prompt_confession(update, context, user_id=user.id)
    else:
        await query.answer("Unknown action.", show_alert=True)


async def _log_checkin(
    query,
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    user_id: int,
    stayed_clean: bool,
    confession_note: str | None = None,
) -> None:
    """Post the check-in to the API and increment streak if clean."""
    payload = {
        "telegram_id": user_id,
        "stayed_clean": stayed_clean,
        "confession_note": confession_note,
    }

    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.post(
                f"{_BASE_URL}/api/v1/checkins", json=payload
            )
            if resp.status_code == 404:
                await query.answer(
                    "You are not registered. Use /start first.", show_alert=True
                )
                return
            resp.raise_for_status()
            data = resp.json()
    except Exception as exc:
        logger.error("_log_checkin: error: %s", exc)
        await query.answer("Failed to log check-in. Please try again.", show_alert=True)
        return

    streak = data.get("streak", 0)
    if stayed_clean:
        await query.answer(
            f"✅ Clean day logged! Current streak: {streak} day(s) 🔥",
            show_alert=True,
        )
        if query.message:
            await query.message.reply_text(
                f"✅ <b>{update.effective_user.first_name}</b> stayed clean today! "
                f"Streak: {streak} 🔥",
                parse_mode="HTML",
            )
    else:
        await query.answer("Logged.", show_alert=False)


async def _prompt_confession(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    user_id: int,
) -> None:
    """Prompt the user to send a confession via /confess."""
    message = update.effective_message
    if message:
        await message.reply_text(
            "😔 It happens. Use /confess <app> [note] to share with your group.",
            parse_mode="HTML",
        )


# ---------------------------------------------------------------------------
# React (fire-and-forget)
# ---------------------------------------------------------------------------

async def _handle_react(query, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle react:{request_id}:{reaction} — log engagement, fire-and-forget."""
    await query.answer()

    user = update.effective_user
    if user is None:
        return

    parts = (query.data or "").split(":")
    if len(parts) != 3:
        return

    _, request_id, reaction = parts

    # Fire-and-forget: log to API (endpoint may not exist yet; ignore errors)
    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            await client.post(
                f"{_BASE_URL}/api/v1/reactions",
                json={
                    "request_id": request_id,
                    "telegram_id": user.id,
                    "reaction": reaction,
                },
            )
    except Exception as exc:
        logger.debug("_handle_react: ignored error: %s", exc)
