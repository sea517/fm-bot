"""Auth for dashboard + per-bot extension tokens."""

from __future__ import annotations

from fastapi import Header, HTTPException

from src.config import (
    BOT_TOKENS,
    DASHBOARD_API_TOKEN,
)


def require_dashboard(authorization: str | None = Header(default=None)) -> None:
    if not DASHBOARD_API_TOKEN:
        raise HTTPException(503, "DASHBOARD_API_TOKEN not configured")
    token = _bearer(authorization)
    if token != DASHBOARD_API_TOKEN:
        raise HTTPException(401, "Invalid dashboard token")


def require_bot(
    bot_id: int,
    authorization: str | None = Header(default=None),
) -> int:
    token = _bearer(authorization)
    expected = BOT_TOKENS.get(bot_id)
    if not expected:
        raise HTTPException(503, f"BOT_{bot_id}_TOKEN not configured")
    if token != expected:
        raise HTTPException(401, "Invalid bot token")
    return bot_id


def require_dashboard_or_bot(
    bot_id: int,
    authorization: str | None = Header(default=None),
) -> str:
    """Return 'dashboard' or 'bot'."""
    token = _bearer(authorization)
    if DASHBOARD_API_TOKEN and token == DASHBOARD_API_TOKEN:
        return "dashboard"
    expected = BOT_TOKENS.get(bot_id)
    if expected and token == expected:
        return "bot"
    raise HTTPException(401, "Invalid token")


def _bearer(authorization: str | None) -> str:
    if not authorization:
        raise HTTPException(401, "Missing Authorization header")
    parts = authorization.split()
    if len(parts) == 2 and parts[0].lower() == "bearer":
        return parts[1].strip()
    raise HTTPException(401, "Expected Bearer token")
