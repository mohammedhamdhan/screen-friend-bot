"""
OCR service using GPT-4o vision to extract screen time data from screenshots.

Uses raw httpx (consistent with the rest of the codebase — no openai SDK).
"""

import json
import logging
from difflib import SequenceMatcher

import httpx

from app.config import get_settings

logger = logging.getLogger(__name__)

_SYSTEM_PROMPT = """You are a screen time parser. The user will send you a screenshot of their phone's screen time / digital wellbeing report.

Extract every app name and its usage in minutes. Return ONLY valid JSON in this exact format:
{"apps": [{"app_name": "Instagram", "minutes": 45}, {"app_name": "YouTube", "minutes": 120}]}

Rules:
- Convert hours to minutes (e.g. "1h 30m" = 90 minutes, "2h" = 120 minutes).
- If usage is shown as "< 1 min" or similar, use 1.
- Use the app's common name (e.g. "Instagram" not "Instagram · Social").
- Only include apps with measurable usage time.
- If the image is NOT a screen time report, return: {"error": "not a screen time screenshot"}
- If you cannot read the image clearly, return: {"error": "unable to parse screenshot"}"""


async def extract_screen_time(image_bytes: bytes) -> dict:
    """Call GPT-4o vision to extract per-app screen time from a screenshot.

    Returns dict with either:
      {"apps": [{"app_name": str, "minutes": int}, ...]}
    or:
      {"error": "reason"}
    """
    import base64

    settings = get_settings()
    if not settings.OPENAI_API_KEY:
        return {"error": "OpenAI API key not configured"}

    b64_image = base64.b64encode(image_bytes).decode("utf-8")

    payload = {
        "model": "gpt-4o",
        "temperature": 0,
        "max_tokens": 1024,
        "messages": [
            {"role": "system", "content": _SYSTEM_PROMPT},
            {
                "role": "user",
                "content": [
                    {
                        "type": "image_url",
                        "image_url": {
                            "url": f"data:image/jpeg;base64,{b64_image}",
                            "detail": "high",
                        },
                    },
                    {
                        "type": "text",
                        "text": "Extract the screen time data from this screenshot.",
                    },
                ],
            },
        ],
    }

    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            resp = await client.post(
                "https://api.openai.com/v1/chat/completions",
                headers={
                    "Authorization": f"Bearer {settings.OPENAI_API_KEY}",
                    "Content-Type": "application/json",
                },
                json=payload,
            )
            resp.raise_for_status()
            data = resp.json()

        content = data["choices"][0]["message"]["content"]
        # Strip markdown code fences if present
        content = content.strip()
        if content.startswith("```"):
            content = content.split("\n", 1)[1] if "\n" in content else content[3:]
            if content.endswith("```"):
                content = content[:-3]
            content = content.strip()

        result = json.loads(content)
        return result

    except json.JSONDecodeError:
        logger.error("extract_screen_time: failed to parse GPT response as JSON")
        return {"error": "failed to parse OCR response"}
    except Exception as exc:
        logger.error("extract_screen_time: API call failed: %s", exc)
        return {"error": f"OCR API error: {exc}"}


_WEEKLY_SYSTEM_PROMPT = """You are a screen time parser. The user will send you a screenshot of their phone's WEEKLY screen time / digital wellbeing report.

Extract every app name and its total weekly usage in minutes. Return ONLY valid JSON in this exact format:
{"apps": [{"app_name": "Instagram", "minutes": 315}, {"app_name": "YouTube", "minutes": 840}]}

Rules:
- These are WEEKLY totals so numbers will be larger than daily usage (e.g. several hours per app is normal).
- Convert hours to minutes (e.g. "1h 30m" = 90 minutes, "2h" = 120 minutes, "5h 15m" = 315 minutes).
- If usage is shown as "< 1 min" or similar, use 1.
- Use the app's common name (e.g. "Instagram" not "Instagram · Social").
- Only include apps with measurable usage time.
- If the image is NOT a screen time report, return: {"error": "not a screen time screenshot"}
- If you cannot read the image clearly, return: {"error": "unable to parse screenshot"}"""


async def extract_weekly_screen_time(image_bytes: bytes) -> dict:
    """Call GPT-4o vision to extract per-app WEEKLY screen time from a screenshot.

    Returns dict with either:
      {"apps": [{"app_name": str, "minutes": int}, ...]}
    or:
      {"error": "reason"}
    """
    import base64

    settings = get_settings()
    if not settings.OPENAI_API_KEY:
        return {"error": "OpenAI API key not configured"}

    b64_image = base64.b64encode(image_bytes).decode("utf-8")

    payload = {
        "model": "gpt-4o",
        "temperature": 0,
        "max_tokens": 1024,
        "messages": [
            {"role": "system", "content": _WEEKLY_SYSTEM_PROMPT},
            {
                "role": "user",
                "content": [
                    {
                        "type": "image_url",
                        "image_url": {
                            "url": f"data:image/jpeg;base64,{b64_image}",
                            "detail": "high",
                        },
                    },
                    {
                        "type": "text",
                        "text": "Extract the weekly screen time data from this screenshot.",
                    },
                ],
            },
        ],
    }

    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            resp = await client.post(
                "https://api.openai.com/v1/chat/completions",
                headers={
                    "Authorization": f"Bearer {settings.OPENAI_API_KEY}",
                    "Content-Type": "application/json",
                },
                json=payload,
            )
            resp.raise_for_status()
            data = resp.json()

        content = data["choices"][0]["message"]["content"]
        content = content.strip()
        if content.startswith("```"):
            content = content.split("\n", 1)[1] if "\n" in content else content[3:]
            if content.endswith("```"):
                content = content[:-3]
            content = content.strip()

        result = json.loads(content)
        return result

    except json.JSONDecodeError:
        logger.error("extract_weekly_screen_time: failed to parse GPT response as JSON")
        return {"error": "failed to parse OCR response"}
    except Exception as exc:
        logger.error("extract_weekly_screen_time: API call failed: %s", exc)
        return {"error": f"OCR API error: {exc}"}


def compare_weekly_against_dailies(
    weekly_apps: list[dict], daily_sum_by_app: dict[str, int]
) -> tuple[bool, int, list[str]]:
    """Compare weekly screenshot totals against sum of daily check-ins.

    Parameters
    ----------
    weekly_apps:
        List of {"app_name": str, "minutes": int} from weekly OCR.
    daily_sum_by_app:
        Dict of {app_name: total_minutes} summed from daily ScreenTimeLog entries.

    Returns
    -------
    (passed, discrepancy_minutes, details) where details is a list of
    human-readable strings describing per-app discrepancies.
    """
    from app.config import get_settings

    tolerance = get_settings().WEEKLY_TOLERANCE_MINUTES
    total_weekly = sum(a["minutes"] for a in weekly_apps)
    total_daily = sum(daily_sum_by_app.values())
    discrepancy = total_weekly - total_daily
    details = []

    for app in weekly_apps:
        app_name = app["app_name"]
        weekly_mins = app["minutes"]

        # Find matching daily entry
        matched_daily = 0
        for daily_name, daily_mins in daily_sum_by_app.items():
            if _fuzzy_match(app_name, daily_name):
                matched_daily = daily_mins
                break

        app_diff = weekly_mins - matched_daily
        if app_diff > 0:
            details.append(f"{app_name}: weekly {weekly_mins}m vs daily {matched_daily}m (+{app_diff}m)")

    passed = discrepancy <= tolerance
    return passed, max(discrepancy, 0), details


def _fuzzy_match(name_a: str, name_b: str) -> bool:
    """Case-insensitive fuzzy match using containment + SequenceMatcher."""
    a = name_a.lower().strip()
    b = name_b.lower().strip()

    # Exact match
    if a == b:
        return True

    # Containment check (e.g. "instagram" in "instagram · social")
    if a in b or b in a:
        return True

    # Fuzzy similarity
    ratio = SequenceMatcher(None, a, b).ratio()
    return ratio >= 0.8


def find_missing_limit_apps(
    extracted_apps: list[dict], user_limits: list[dict]
) -> list[str]:
    """Return names of apps that have limits but weren't found in the screenshot.

    Parameters
    ----------
    extracted_apps:
        List of {"app_name": str, "minutes": int} from OCR.
    user_limits:
        List of {"app_name": str, "daily_limit_mins": int} from DB.

    Returns
    -------
    List of app names from user_limits that have no fuzzy match in extracted_apps.
    """
    if not user_limits:
        return []

    missing = []
    for limit in user_limits:
        limit_app = limit["app_name"]
        found = any(
            _fuzzy_match(app["app_name"], limit_app) for app in extracted_apps
        )
        if not found:
            missing.append(limit_app)
    return missing


def compare_against_limits(
    extracted_apps: list[dict], user_limits: list[dict]
) -> tuple[bool, list[str]]:
    """Compare extracted app usage against user-defined limits.

    Parameters
    ----------
    extracted_apps:
        List of {"app_name": str, "minutes": int} from OCR.
    user_limits:
        List of {"app_name": str, "daily_limit_mins": int} from DB.

    Returns
    -------
    (stayed_clean, violations) where violations is a list of human-readable
    strings like "Instagram 90/60 min".
    """
    if not user_limits:
        return True, []

    violations = []

    for limit in user_limits:
        limit_app = limit["app_name"]
        limit_mins = limit["daily_limit_mins"]

        for app in extracted_apps:
            if _fuzzy_match(app["app_name"], limit_app):
                if app["minutes"] > limit_mins:
                    violations.append(
                        f"{app['app_name']} {app['minutes']}/{limit_mins} min"
                    )
                break

    stayed_clean = len(violations) == 0
    return stayed_clean, violations
