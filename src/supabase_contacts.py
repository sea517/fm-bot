"""
Supabase ledger of freelancers already seen/contacted on freelancermap.

Uses PostgREST (requests) so we do not need the supabase-py SDK.
Disabled when SUPABASE_URL / SUPABASE_SERVICE_ROLE_KEY are unset.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any

import requests

from src.config import SUPABASE_SERVICE_ROLE_KEY, SUPABASE_URL

logger = logging.getLogger(__name__)

_TABLE = "contacted_freelancers"
_TIMEOUT = 20


def supabase_configured() -> bool:
    return bool(SUPABASE_URL and SUPABASE_SERVICE_ROLE_KEY)


def _headers() -> dict[str, str]:
    return {
        "apikey": SUPABASE_SERVICE_ROLE_KEY,
        "Authorization": f"Bearer {SUPABASE_SERVICE_ROLE_KEY}",
        "Content-Type": "application/json",
        "Prefer": "resolution=merge-duplicates,return=minimal",
    }


def _rest_url() -> str:
    return f"{SUPABASE_URL.rstrip('/')}/rest/v1/{_TABLE}"


def normalize_fm_conversation_id(message_id: str | None) -> str | None:
    """Applicant.freelancermap_message_id is usually 'fm-123'; store '123'."""
    raw = (message_id or "").strip()
    if not raw:
        return None
    if raw.lower().startswith("fm-"):
        return raw[3:].strip() or None
    if raw.lower().startswith("email:"):
        return None
    return raw


def _iso(dt: datetime | None) -> str | None:
    if dt is None:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc).isoformat()


def upsert_contacted_freelancer(
    *,
    fm_conversation_id: str | None,
    display_name: str | None = None,
    email: str | None = None,
    project_title: str | None = None,
    local_applicant_id: int | None = None,
    stage: str | None = None,
    contacted: bool = False,
    contacted_at: datetime | None = None,
) -> bool:
    """
    Upsert one freelancer into Supabase.

    contacted=True sets / refreshes first_contacted_at and last_contacted_at
    (outbound freelancermap reply sent).
    """
    if not supabase_configured():
        return False

    cid = normalize_fm_conversation_id(fm_conversation_id)
    if not cid:
        return False

    now = datetime.now(timezone.utc)
    when = contacted_at or now
    row: dict[str, Any] = {
        "fm_conversation_id": cid,
        "updated_at": _iso(now),
    }
    if display_name:
        row["display_name"] = display_name.strip()
    if email:
        row["email"] = email.strip()
    if project_title:
        row["project_title"] = project_title.strip()
    if local_applicant_id is not None:
        row["local_applicant_id"] = local_applicant_id
    if stage:
        row["stage"] = stage

    if contacted:
        row["last_contacted_at"] = _iso(when)
        # first_contacted_at only on insert — merge-duplicates won't clear it;
        # use a separate patch if we need "set if null". PostgREST upsert
        # overwrites columns we send, so only send first_contacted_at when
        # the row is new (we ignore overwrite of same timestamp).
        row["first_contacted_at"] = _iso(when)

    try:
        # If row exists and already has first_contacted_at, avoid clobbering
        # with a later invite timestamp: fetch then merge.
        if contacted:
            existing = _get_by_conversation_id(cid)
            if existing and existing.get("first_contacted_at"):
                row.pop("first_contacted_at", None)

        response = requests.post(
            _rest_url(),
            headers={**_headers(), "Prefer": "resolution=merge-duplicates,return=minimal"},
            params={"on_conflict": "fm_conversation_id"},
            json=row,
            timeout=_TIMEOUT,
        )
        if response.status_code >= 400:
            logger.error(
                "Supabase upsert failed (%s): %s",
                response.status_code,
                response.text[:300],
            )
            return False
        logger.info(
            "Supabase contacted_freelancers upsert fm=%s name=%s contacted=%s",
            cid,
            display_name,
            contacted,
        )
        return True
    except requests.RequestException:
        logger.exception("Supabase upsert error for fm=%s", cid)
        return False


def _get_by_conversation_id(cid: str) -> dict[str, Any] | None:
    try:
        response = requests.get(
            _rest_url(),
            headers={
                "apikey": SUPABASE_SERVICE_ROLE_KEY,
                "Authorization": f"Bearer {SUPABASE_SERVICE_ROLE_KEY}",
            },
            params={"fm_conversation_id": f"eq.{cid}", "select": "*", "limit": "1"},
            timeout=_TIMEOUT,
        )
        if not response.ok:
            return None
        rows = response.json()
        return rows[0] if rows else None
    except requests.RequestException:
        return None


def was_contacted_before(
    *,
    fm_conversation_id: str | None = None,
    email: str | None = None,
) -> bool:
    """True if this freelancer already has a contacted row in Supabase."""
    if not supabase_configured():
        return False

    cid = normalize_fm_conversation_id(fm_conversation_id)
    try:
        if cid:
            row = _get_by_conversation_id(cid)
            if row and (row.get("first_contacted_at") or row.get("last_contacted_at")):
                return True
        if email and email.strip():
            response = requests.get(
                _rest_url(),
                headers={
                    "apikey": SUPABASE_SERVICE_ROLE_KEY,
                    "Authorization": f"Bearer {SUPABASE_SERVICE_ROLE_KEY}",
                },
                params={
                    "email": f"ilike.{email.strip()}",
                    "select": "id,first_contacted_at,last_contacted_at",
                    "limit": "1",
                },
                timeout=_TIMEOUT,
            )
            if response.ok:
                rows = response.json()
                if rows and (
                    rows[0].get("first_contacted_at") or rows[0].get("last_contacted_at")
                ):
                    return True
    except requests.RequestException:
        logger.exception("Supabase was_contacted_before lookup failed")
    return False


def sync_applicant_to_supabase(applicant, *, contacted: bool | None = None) -> bool:
    """
    Push one local Applicant to Supabase.

    contacted defaults to True when the bot has already replied on FM
    (bot_reply_count > 0) or left the initial 'new' stage with a chat_reply.
    """
    if contacted is None:
        contacted = bool(
            (applicant.bot_reply_count or 0) > 0
            or (applicant.chat_reply and applicant.reply_channel == "freelancermap")
        )
    project_title = None
    return upsert_contacted_freelancer(
        fm_conversation_id=applicant.freelancermap_message_id,
        display_name=applicant.name,
        email=applicant.email or applicant.slack_invite_email,
        project_title=project_title,
        local_applicant_id=applicant.id,
        stage=applicant.stage,
        contacted=contacted,
        contacted_at=applicant.updated_at,
    )


def sync_all_contacted_from_db(session) -> tuple[int, int]:
    """
    Upsert every local FM applicant that looks contacted.
    Returns (ok_count, fail_or_skip_count).
    """
    from sqlalchemy import select

    from src.db.models import Applicant

    if not supabase_configured():
        logger.warning("Supabase not configured — skip sync")
        return 0, 0

    rows = session.execute(select(Applicant)).scalars().all()
    ok = 0
    skipped = 0
    for a in rows:
        cid = normalize_fm_conversation_id(a.freelancermap_message_id)
        if not cid:
            skipped += 1
            continue
        contacted = bool((a.bot_reply_count or 0) > 0 or a.chat_reply)
        if sync_applicant_to_supabase(a, contacted=contacted):
            ok += 1
        else:
            skipped += 1
    return ok, skipped
