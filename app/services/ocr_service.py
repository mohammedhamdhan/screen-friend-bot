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
