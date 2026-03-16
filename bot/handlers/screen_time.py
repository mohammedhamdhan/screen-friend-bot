"""
Group photo handler for screen time OCR check-in.

When a screenshot collection window is active for a group, photos sent by
pending users are processed through GPT-4o vision to extract per-app usage.
"""

import json
import logging
import os

import httpx
from telegram import Update
from telegram.ext import ContextTypes

logger = logging.getLogger(__name__)

_BASE_URL = f"http://127.0.0.1:{os.environ.get('PORT', '8000')}"


async def _get_redis():
    """Get an async Redis client."""
    import redis.asyncio as aioredis
    from app.config import get_settings

    settings = get_settings()
    return aioredis.from_url(settings.REDIS_URL, decode_responses=True)


async def handle_group_screenshot(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> None:
    """Handle photos sent in groups during a screenshot collection window or personal check-in."""
    message = update.message
    if message is None or not message.photo:
        return

    user = update.effective_user
    chat = update.effective_chat
    if user is None or chat is None:
        return

    # Check if this user has a personal check-in pending (from /checkin)
    r = await _get_redis()
    try:
        checkin_key = f"screengate:checkin:{user.id}"
        checkin_raw = await r.get(checkin_key)
        if checkin_raw:
            checkin_state = json.loads(checkin_raw)
            # Only handle if photo is in the same chat where /checkin was issued
            if checkin_state.get("chat_id") == chat.id:
                await _handle_personal_checkin(
                    update, context, r, checkin_key, checkin_state
                )
                return
    finally:
        await r.aclose()

    chat_id = chat.id
    redis_key = f"screengate:collection:{chat_id}"

    logger.info(
        "handle_group_screenshot: photo from user=%s in chat=%s, redis_key=%s",
        user.id, chat_id, redis_key,
    )

    # Check if there's an active collection window for this group
    r = await _get_redis()
    try:
        raw = await r.get(redis_key)
        logger.info("handle_group_screenshot: redis raw=%s", raw)
        if not raw:
            return  # No active collection — ignore photo

        state = json.loads(raw)
        pending_users = state.get("pending_users", [])

        # Check if this user is in the pending list
        if user.id not in pending_users:
            return  # User already submitted or not in group

        # Download the photo
        photo = message.photo[-1]  # Largest size
        try:
            photo_file = await context.bot.get_file(photo.file_id)
            file_bytes = bytes(await photo_file.download_as_bytearray())
        except Exception as exc:
            logger.error("handle_group_screenshot: failed to download photo: %s", exc)
            await message.reply_text(
                "Failed to download your screenshot. Please try again."
            )
            return

        # Upload to R2
        screenshot_url = None
        try:
            from app.services.storage_service import upload_photo

            screenshot_url = await upload_photo(file_bytes, f"{photo.file_id}.jpg")
        except Exception as exc:
            logger.warning("handle_group_screenshot: R2 upload failed: %s", exc)

        # Run OCR
        from app.services.ocr_service import extract_screen_time, compare_against_limits

        ocr_result = await extract_screen_time(file_bytes)

        if "error" in ocr_result:
            logger.info(
                "handle_group_screenshot: OCR failed for user %s: %s",
                user.id,
                ocr_result["error"],
            )
            # Send manual fallback buttons
            from bot.keyboards import screenshot_fallback_keyboard

            await message.reply_text(
                f"Couldn't read your screenshot ({ocr_result['error']}). "
                "Please check in manually:",
                reply_markup=screenshot_fallback_keyboard(user.id),
            )
            # Remove user from pending list
            await _remove_pending_user(r, redis_key, state, user.id)
            return

        extracted_apps = ocr_result.get("apps", [])

        # Get user's app limits
        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                resp = await client.get(f"{_BASE_URL}/api/v1/limits/{user.id}")
                if resp.status_code == 200:
                    limits_data = resp.json()
                    user_limits = [
                        {
                            "app_name": lim["app_name"],
                            "daily_limit_mins": lim["daily_limit_mins"],
                        }
                        for lim in limits_data
                    ]
                else:
                    user_limits = []
        except Exception as exc:
            logger.warning("handle_group_screenshot: failed to fetch limits: %s", exc)
            user_limits = []

        # Compare against limits
        stayed_clean, violations = compare_against_limits(extracted_apps, user_limits)

        # Submit to API
        submit_payload = {
            "telegram_id": user.id,
            "group_telegram_chat_id": chat_id,
            "apps": [
                {"app_name": a["app_name"], "minutes": a["minutes"]}
                for a in extracted_apps
            ],
            "screenshot_url": screenshot_url,
            "stayed_clean": stayed_clean,
            "violations": violations,
        }

        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                resp = await client.post(
                    f"{_BASE_URL}/api/v1/screen-time", json=submit_payload
                )
                if resp.status_code == 409:
                    await message.reply_text("You've already checked in today!")
                    await _remove_pending_user(r, redis_key, state, user.id)
                    return
                resp.raise_for_status()
                result = resp.json()
        except Exception as exc:
            logger.error("handle_group_screenshot: API submit failed: %s", exc)
            from bot.keyboards import screenshot_fallback_keyboard

            await message.reply_text(
                "Something went wrong processing your screenshot. "
                "Please check in manually:",
                reply_markup=screenshot_fallback_keyboard(user.id),
            )
            await _remove_pending_user(r, redis_key, state, user.id)
            return

        # Post result to group
        username = user.username or user.first_name
        streak = result.get("streak", 0)

        if stayed_clean:
            app_summary = ", ".join(
                f"{a['app_name']} {a['minutes']}m" for a in extracted_apps[:5]
            )
            await message.reply_text(
                f"✅ @{username} stayed clean! Streak: {streak} 🔥\n"
                f"📊 {app_summary}",
            )
        else:
            violation_text = ", ".join(violations)
            await message.reply_text(
                f"❌ @{username} slipped: {violation_text}\n"
                f"Streak reset to 0.",
            )

        # Remove user from pending list
        await _remove_pending_user(r, redis_key, state, user.id)

    finally:
        await r.aclose()


async def handle_dm_screenshot(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> None:
    """Handle photos sent in DMs for personal check-in."""
    message = update.message
    if message is None or not message.photo:
        return

    user = update.effective_user
    chat = update.effective_chat
    if user is None or chat is None:
        return

    r = await _get_redis()
    try:
        checkin_key = f"screengate:checkin:{user.id}"
        checkin_raw = await r.get(checkin_key)
        if not checkin_raw:
            return  # No pending check-in — ignore photo

        checkin_state = json.loads(checkin_raw)
        await _handle_personal_checkin(
            update, context, r, checkin_key, checkin_state
        )
    finally:
        await r.aclose()


async def _handle_personal_checkin(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    r,
    checkin_key: str,
    checkin_state: dict,
) -> None:
    """Process a screenshot sent in response to /checkin.

    On OCR failure, re-prompts once. On second failure, falls back to manual buttons.
    """
    message = update.message
    user = update.effective_user
    chat = update.effective_chat
    retries = checkin_state.get("retries", 0)

    # Download photo
    photo = message.photo[-1]
    try:
        photo_file = await context.bot.get_file(photo.file_id)
        file_bytes = bytes(await photo_file.download_as_bytearray())
    except Exception as exc:
        logger.error("_handle_personal_checkin: failed to download photo: %s", exc)
        await message.reply_text("Failed to download your screenshot. Please try again.")
        return

    # Upload to R2
    screenshot_url = None
    try:
        from app.services.storage_service import upload_photo

        screenshot_url = await upload_photo(file_bytes, f"{photo.file_id}.jpg")
    except Exception as exc:
        logger.warning("_handle_personal_checkin: R2 upload failed: %s", exc)

    # Run OCR
    from app.services.ocr_service import extract_screen_time, compare_against_limits

    ocr_result = await extract_screen_time(file_bytes)

    if "error" in ocr_result:
        logger.info(
            "_handle_personal_checkin: OCR failed for user %s (retry %d): %s",
            user.id, retries, ocr_result["error"],
        )

        if retries < 1:
            # First failure — ask user to try again
            checkin_state["retries"] = retries + 1
            ttl = await r.ttl(checkin_key)
            if ttl > 0:
                await r.setex(checkin_key, ttl, json.dumps(checkin_state))
            else:
                await r.setex(checkin_key, 600, json.dumps(checkin_state))
            await message.reply_text(
                f"Couldn't read your screenshot ({ocr_result['error']}). "
                "Please try again with a clearer screenshot of your screen time report."
            )
            return
        else:
            # Second failure — fall back to manual check-in
            await r.delete(checkin_key)
            from bot.keyboards import screenshot_fallback_keyboard

            await message.reply_text(
                "Still couldn't read your screenshot. Please check in manually:",
                reply_markup=screenshot_fallback_keyboard(user.id),
            )
            return

    # OCR succeeded — clean up Redis state
    await r.delete(checkin_key)

    extracted_apps = ocr_result.get("apps", [])

    # Get user's app limits
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.get(f"{_BASE_URL}/api/v1/limits/{user.id}")
            if resp.status_code == 200:
                limits_data = resp.json()
                user_limits = [
                    {
                        "app_name": lim["app_name"],
                        "daily_limit_mins": lim["daily_limit_mins"],
                    }
                    for lim in limits_data
                ]
            else:
                user_limits = []
    except Exception as exc:
        logger.warning("_handle_personal_checkin: failed to fetch limits: %s", exc)
        user_limits = []

    # Compare against limits
    stayed_clean, violations = compare_against_limits(extracted_apps, user_limits)

    # Submit to API
    submit_payload = {
        "telegram_id": user.id,
        "group_telegram_chat_id": chat.id,
        "apps": [
            {"app_name": a["app_name"], "minutes": a["minutes"]}
            for a in extracted_apps
        ],
        "screenshot_url": screenshot_url,
        "stayed_clean": stayed_clean,
        "violations": violations,
    }

    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.post(
                f"{_BASE_URL}/api/v1/screen-time", json=submit_payload
            )
            if resp.status_code == 409:
                await message.reply_text("You've already checked in today!")
                return
            if resp.status_code == 404:
                # Group not found — fall back to basic checkin API
                checkin_payload = {
                    "telegram_id": user.id,
                    "stayed_clean": stayed_clean,
                }
                resp = await client.post(
                    f"{_BASE_URL}/api/v1/checkins", json=checkin_payload
                )
                if resp.status_code == 409:
                    await message.reply_text("You've already checked in today!")
                    return
                resp.raise_for_status()
                result = resp.json()
            else:
                resp.raise_for_status()
                result = resp.json()
    except Exception as exc:
        logger.error("_handle_personal_checkin: API submit failed: %s", exc)
        from bot.keyboards import screenshot_fallback_keyboard

        await message.reply_text(
            "Something went wrong processing your screenshot. "
            "Please check in manually:",
            reply_markup=screenshot_fallback_keyboard(user.id),
        )
        return

    # Post result
    username = user.username or user.first_name
    streak = result.get("streak", 0)

    if stayed_clean:
        app_summary = ", ".join(
            f"{a['app_name']} {a['minutes']}m" for a in extracted_apps[:5]
        )
        await message.reply_text(
            f"✅ @{username} stayed clean! Streak: {streak} 🔥\n"
            f"📊 {app_summary}",
        )
    else:
        violation_text = ", ".join(violations)
        await message.reply_text(
            f"❌ @{username} slipped: {violation_text}\n"
            f"Streak reset to 0.",
        )


async def _remove_pending_user(r, redis_key: str, state: dict, user_id: int) -> None:
    """Remove a user from the pending list in Redis."""
    pending = state.get("pending_users", [])
    if user_id in pending:
        pending.remove(user_id)
        state["pending_users"] = pending
        if pending:
            ttl = await r.ttl(redis_key)
            if ttl > 0:
                await r.setex(redis_key, ttl, json.dumps(state))
            else:
                await r.set(redis_key, json.dumps(state))
        else:
            # All users submitted — clean up
            await r.delete(redis_key)
