"""Telegram alerts for handoff (deterministic layer only)."""

from __future__ import annotations

import logging

import requests

from src.config import TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID

logger = logging.getLogger(__name__)


def send_handoff_alert(
    *,
    thread_link: str | None,
    candidate_name: str | None,
    stage: str | None,
    trigger: str,
    last_messages: list[str],
) -> bool:
    """
    Notify TELEGRAM_CHAT_ID. Failures are logged; never reopen the thread.
    """
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        logger.error(
            "Telegram handoff alert skipped — TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_ID unset"
        )
        return False
    lines = [
        "FM handoff",
        f"trigger: {trigger}",
        f"stage: {stage or '?'}",
        f"candidate: {candidate_name or '?'}",
        f"thread: {thread_link or '?'}",
        "last messages:",
    ]
    for i, msg in enumerate(last_messages[-2:], 1):
        lines.append(f"  {i}. {(msg or '')[:500]}")
    text = "\n".join(lines)
    try:
        url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
        res = requests.post(
            url,
            json={"chat_id": TELEGRAM_CHAT_ID, "text": text},
            timeout=20,
        )
        if not res.ok:
            logger.error(
                "Telegram handoff alert failed %s: %s",
                res.status_code,
                res.text[:300],
            )
            return False
        return True
    except requests.RequestException:
        logger.exception("Telegram handoff alert request failed")
        return False
