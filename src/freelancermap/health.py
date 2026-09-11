"""Freelancermap account health / backoff when login keeps failing."""

from __future__ import annotations

import json
import logging
import time
from pathlib import Path

from src.config import DATA_DIR

logger = logging.getLogger(__name__)

FM_HEALTH_FILE = DATA_DIR / "fm_health.json"

# After this many consecutive failures, stop trying FM until backoff expires.
FM_FAIL_THRESHOLD = 3
# How long to skip freelancermap after the threshold (default 6 hours).
FM_BACKOFF_SECONDS = 6 * 60 * 60


def _load() -> dict:
    if not FM_HEALTH_FILE.exists():
        return {"failures": 0, "backoff_until": 0.0, "last_error": None}
    try:
        return json.loads(FM_HEALTH_FILE.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {"failures": 0, "backoff_until": 0.0, "last_error": None}


def _save(data: dict) -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    FM_HEALTH_FILE.write_text(json.dumps(data, indent=2), encoding="utf-8")


def fm_backoff_remaining() -> float:
    """Seconds left in backoff, or 0 if FM may be attempted."""
    data = _load()
    until = float(data.get("backoff_until") or 0)
    return max(0.0, until - time.time())


def fm_should_attempt() -> bool:
    remaining = fm_backoff_remaining()
    if remaining > 0:
        data = _load()
        logger.error(
            "Freelancermap SKIPPED — account appears dead / in backoff "
            "for %d more minutes (failures=%s, last_error=%s). "
            "Fix login, then delete %s or wait for backoff to end. "
            "Slack + email continue.",
            int(remaining // 60) + 1,
            data.get("failures"),
            data.get("last_error"),
            FM_HEALTH_FILE,
        )
        return False
    return True


def fm_record_success() -> None:
    data = _load()
    if data.get("failures") or data.get("backoff_until"):
        logger.info("Freelancermap login recovered — clearing failure backoff")
    _save({"failures": 0, "backoff_until": 0.0, "last_error": None, "last_ok_at": time.time()})


def fm_record_failure(exc: BaseException) -> None:
    data = _load()
    failures = int(data.get("failures") or 0) + 1
    err = f"{type(exc).__name__}: {exc}"
    if len(err) > 300:
        err = err[:300] + "…"
    data["failures"] = failures
    data["last_error"] = err
    data["last_fail_at"] = time.time()

    if failures >= FM_FAIL_THRESHOLD:
        data["backoff_until"] = time.time() + FM_BACKOFF_SECONDS
        logger.error(
            "Freelancermap UNAVAILABLE after %d consecutive failures — "
            "backing off %dh. Continuing with Slack + email only. "
            "Last error: %s",
            failures,
            FM_BACKOFF_SECONDS // 3600,
            err,
        )
    else:
        logger.error(
            "Freelancermap failed (%d/%d) — continuing with Slack + email. Error: %s",
            failures,
            FM_FAIL_THRESHOLD,
            err,
        )
    _save(data)
