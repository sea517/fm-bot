"""FastAPI control plane: dashboard + 3 Chrome extension workers."""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from fastapi import FastAPI, Header, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from src.control import auth
from src.control.db import sb_get, sb_insert, sb_patch, sb_upsert, supabase_ok
from src.control.schemas import (
    BotSettings,
    BotSettingsBody,
    ContactUpsertBody,
    CreateJobBody,
    HeartbeatBody,
    JobEventBody,
    JobStatsBody,
)

logger = logging.getLogger(__name__)

STATIC_DIR = Path(__file__).resolve().parent / "static"

app = FastAPI(title="Freelancermap Bot Control", version="0.1.0")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _require_supabase() -> None:
    if not supabase_ok():
        raise HTTPException(503, "Supabase not configured on server")


def _bot_settings(bot_id: int) -> BotSettings:
    """Load per-bot settings from Supabase (works on Vercel; no local disk)."""
    _require_supabase()
    rows = sb_get(
        "bots",
        params={"id": f"eq.{bot_id}", "select": "settings", "limit": "1"},
    )
    raw = (rows[0].get("settings") if rows else None) or {}
    if not isinstance(raw, dict):
        raw = {}
    return BotSettings(**{k: v for k, v in raw.items() if k in BotSettings.model_fields})


@app.exception_handler(RuntimeError)
async def runtime_error_handler(_request, exc: RuntimeError):
    msg = str(exc)
    if "control_plane.sql" in msg or "missing" in msg.lower():
        return JSONResponse({"detail": msg}, status_code=503)
    return JSONResponse({"detail": msg}, status_code=500)


@app.get("/api/health")
def health() -> dict[str, Any]:
    return {
        "ok": True,
        "supabase": supabase_ok(),
        "time": _now(),
    }


# ----- Dashboard: bots -----


@app.get("/api/bots")
def list_bots(authorization: str | None = Header(default=None)) -> list[dict]:
    auth.require_dashboard(authorization)
    _require_supabase()
    bots = sb_get("bots", params={"select": "*", "order": "id.asc"})
    for b in bots:
        jid = b.get("current_job_id")
        b["current_job"] = None
        if jid:
            jobs = sb_get(
                "outreach_jobs",
                params={"id": f"eq.{jid}", "select": "*", "limit": "1"},
            )
            b["current_job"] = jobs[0] if jobs else None
    return bots


@app.get("/api/bots/{bot_id}")
def get_bot(bot_id: int, authorization: str | None = Header(default=None)) -> dict:
    auth.require_dashboard(authorization)
    if bot_id not in (1, 2, 3):
        raise HTTPException(404, "bot not found")
    _require_supabase()
    rows = sb_get("bots", params={"id": f"eq.{bot_id}", "select": "*", "limit": "1"})
    if not rows:
        raise HTTPException(404, "bot not found")
    bot = rows[0]
    jid = bot.get("current_job_id")
    bot["current_job"] = None
    if jid:
        jobs = sb_get(
            "outreach_jobs",
            params={"id": f"eq.{jid}", "select": "*", "limit": "1"},
        )
        bot["current_job"] = jobs[0] if jobs else None
    return bot


# ----- Jobs -----


@app.get("/api/bots/{bot_id}/jobs")
def list_jobs(
    bot_id: int,
    limit: int = 20,
    authorization: str | None = Header(default=None),
) -> list[dict]:
    auth.require_dashboard(authorization)
    if bot_id not in (1, 2, 3):
        raise HTTPException(404)
    _require_supabase()
    return sb_get(
        "outreach_jobs",
        params={
            "bot_id": f"eq.{bot_id}",
            "select": "*",
            "order": "created_at.desc",
            "limit": str(max(1, min(limit, 100))),
        },
    )


@app.post("/api/bots/{bot_id}/jobs")
def create_job(
    bot_id: int,
    body: CreateJobBody,
    authorization: str | None = Header(default=None),
) -> dict:
    auth.require_dashboard(authorization)
    if bot_id not in (1, 2, 3):
        raise HTTPException(404)
    _require_supabase()
    # Only one active job per bot
    active = sb_get(
        "outreach_jobs",
        params={
            "bot_id": f"eq.{bot_id}",
            "status": "in.(queued,running,cancel_requested)",
            "select": "id,status",
            "limit": "1",
        },
    )
    if active:
        raise HTTPException(
            409,
            f"Bot {bot_id} already has an active job #{active[0]['id']} ({active[0]['status']})",
        )
    rows = sb_insert(
        "outreach_jobs",
        {
            "bot_id": bot_id,
            "keyword": body.keyword,
            "subject": body.subject,
            "message_body": body.message_body,
            "min_interval_sec": body.min_interval_sec,
            "max_interval_sec": body.max_interval_sec,
            "max_freelancers": body.max_freelancers,
            "dry_run": body.dry_run,
            "status": "queued",
            "updated_at": _now(),
        },
    )
    job = rows[0]
    sb_insert(
        "job_events",
        {
            "job_id": job["id"],
            "bot_id": bot_id,
            "level": "info",
            "message": f"Job queued: keyword={body.keyword!r} dry_run={body.dry_run}",
        },
        return_representation=False,
    )
    return job


@app.post("/api/bots/{bot_id}/jobs/{job_id}/stop")
def stop_job(
    bot_id: int,
    job_id: int,
    authorization: str | None = Header(default=None),
) -> dict:
    auth.require_dashboard(authorization)
    _require_supabase()
    jobs = sb_get(
        "outreach_jobs",
        params={
            "id": f"eq.{job_id}",
            "bot_id": f"eq.{bot_id}",
            "select": "*",
            "limit": "1",
        },
    )
    if not jobs:
        raise HTTPException(404, "job not found")
    job = jobs[0]
    if job["status"] in ("completed", "failed", "cancelled", "stopped"):
        return job
    # Dashboard stop is immediate: mark stopped and free the bot.
    # Extension will see no active job on next poll and halt.
    updated = sb_patch(
        "outreach_jobs",
        match={"id": f"eq.{job_id}"},
        row={
            "status": "stopped" if job["status"] != "queued" else "cancelled",
            "finished_at": _now(),
            "updated_at": _now(),
        },
    )
    sb_patch(
        "bots",
        match={"id": f"eq.{bot_id}"},
        row={
            "status": "online",
            "current_job_id": None,
            "updated_at": _now(),
        },
        return_representation=False,
    )
    sb_insert(
        "job_events",
        {
            "job_id": job_id,
            "bot_id": bot_id,
            "level": "warn",
            "message": "Stopped from dashboard",
        },
        return_representation=False,
    )
    return updated[0] if updated else job


@app.get("/api/jobs/{job_id}/events")
def job_events(
    job_id: int,
    limit: int = 100,
    authorization: str | None = Header(default=None),
) -> list[dict]:
    auth.require_dashboard(authorization)
    _require_supabase()
    return sb_get(
        "job_events",
        params={
            "job_id": f"eq.{job_id}",
            "select": "*",
            "order": "created_at.desc,id.desc",
            "limit": str(max(1, min(limit, 500))),
        },
    )


# ----- Extension worker API -----


@app.post("/api/bots/{bot_id}/heartbeat")
def heartbeat(
    bot_id: int,
    body: HeartbeatBody,
    authorization: str | None = Header(default=None),
) -> dict:
    auth.require_bot(bot_id, authorization)
    _require_supabase()
    status = body.status if body.status in ("online", "busy", "error", "offline") else "online"
    rows = sb_patch(
        "bots",
        match={"id": f"eq.{bot_id}"},
        row={
            "status": status,
            "last_heartbeat_at": _now(),
            "last_error": body.last_error,
            "updated_at": _now(),
        },
    )
    return rows[0] if rows else {"id": bot_id, "status": status}


@app.post("/api/bots/{bot_id}/jobs/claim")
def claim_job(
    bot_id: int,
    authorization: str | None = Header(default=None),
) -> dict[str, Any]:
    """Extension claims the next queued job for this bot."""
    auth.require_bot(bot_id, authorization)
    _require_supabase()
    queued = sb_get(
        "outreach_jobs",
        params={
            "bot_id": f"eq.{bot_id}",
            "status": "eq.queued",
            "select": "*",
            "order": "created_at.asc",
            "limit": "1",
        },
    )
    if not queued:
        return {"job": None}
    job = queued[0]
    updated = sb_patch(
        "outreach_jobs",
        match={"id": f"eq.{job['id']}", "status": "eq.queued"},
        row={
            "status": "running",
            "started_at": _now(),
            "updated_at": _now(),
        },
    )
    if not updated:
        return {"job": None}
    job = updated[0]
    sb_patch(
        "bots",
        match={"id": f"eq.{bot_id}"},
        row={
            "status": "busy",
            "current_job_id": job["id"],
            "last_heartbeat_at": _now(),
            "updated_at": _now(),
        },
        return_representation=False,
    )
    sb_insert(
        "job_events",
        {
            "job_id": job["id"],
            "bot_id": bot_id,
            "level": "info",
            "message": "Job claimed by extension",
        },
        return_representation=False,
    )
    return {"job": job}


@app.get("/api/bots/{bot_id}/jobs/active")
def active_job(
    bot_id: int,
    authorization: str | None = Header(default=None),
) -> dict[str, Any]:
    auth.require_bot(bot_id, authorization)
    _require_supabase()
    rows = sb_get(
        "outreach_jobs",
        params={
            "bot_id": f"eq.{bot_id}",
            "status": "in.(running,cancel_requested)",
            "select": "*",
            "order": "started_at.desc",
            "limit": "1",
        },
    )
    return {"job": rows[0] if rows else None}


@app.post("/api/bots/{bot_id}/jobs/{job_id}/events")
def add_event(
    bot_id: int,
    job_id: int,
    body: JobEventBody,
    authorization: str | None = Header(default=None),
) -> dict:
    auth.require_bot(bot_id, authorization)
    _require_supabase()
    rows = sb_insert(
        "job_events",
        {
            "job_id": job_id,
            "bot_id": bot_id,
            "level": body.level,
            "message": body.message,
        },
    )
    sb_patch(
        "bots",
        match={"id": f"eq.{bot_id}"},
        row={"last_heartbeat_at": _now(), "updated_at": _now()},
        return_representation=False,
    )
    return rows[0] if rows else {"ok": True}


@app.post("/api/bots/{bot_id}/jobs/{job_id}/finish")
def finish_job(
    bot_id: int,
    job_id: int,
    body: JobStatsBody,
    authorization: str | None = Header(default=None),
) -> dict:
    auth.require_bot(bot_id, authorization)
    _require_supabase()
    status = body.status
    if status not in ("completed", "failed", "cancelled", "stopped"):
        status = "completed"
    # If dashboard asked to cancel, mark cancelled/stopped
    current = sb_get(
        "outreach_jobs",
        params={"id": f"eq.{job_id}", "bot_id": f"eq.{bot_id}", "select": "status", "limit": "1"},
    )
    if current and current[0].get("status") == "cancel_requested":
        status = "stopped"
    updated = sb_patch(
        "outreach_jobs",
        match={"id": f"eq.{job_id}", "bot_id": f"eq.{bot_id}"},
        row={
            "status": status,
            "stats": body.stats,
            "error": body.error,
            "finished_at": _now(),
            "updated_at": _now(),
        },
    )
    sb_patch(
        "bots",
        match={"id": f"eq.{bot_id}"},
        row={
            "status": "online",
            "current_job_id": None,
            "last_heartbeat_at": _now(),
            "updated_at": _now(),
        },
        return_representation=False,
    )
    sb_insert(
        "job_events",
        {
            "job_id": job_id,
            "bot_id": bot_id,
            "level": "info",
            "message": f"Job finished status={status} stats={body.stats}",
        },
        return_representation=False,
    )
    return updated[0] if updated else {"ok": True}


@app.post("/api/bots/{bot_id}/jobs/{job_id}/stats")
def update_job_stats(
    bot_id: int,
    job_id: int,
    body: JobStatsBody,
    authorization: str | None = Header(default=None),
) -> dict:
    """Merge live campaign stats (e.g. profiles_found) while a job is running."""
    auth.require_bot(bot_id, authorization)
    _require_supabase()
    rows = sb_get(
        "outreach_jobs",
        params={
            "id": f"eq.{job_id}",
            "bot_id": f"eq.{bot_id}",
            "select": "id,stats",
            "limit": "1",
        },
    )
    if not rows:
        raise HTTPException(404, "job not found")
    merged = dict(rows[0].get("stats") or {})
    merged.update(body.stats or {})
    updated = sb_patch(
        "outreach_jobs",
        match={"id": f"eq.{job_id}", "bot_id": f"eq.{bot_id}"},
        row={"stats": merged, "updated_at": _now()},
    )
    return updated[0] if updated else {"ok": True, "stats": merged}


@app.post("/api/bots/{bot_id}/contacts")
def upsert_contact(
    bot_id: int,
    body: ContactUpsertBody,
    authorization: str | None = Header(default=None),
) -> dict:
    auth.require_bot(bot_id, authorization)
    _require_supabase()
    now = _now()
    row = {
        "profile_key": body.profile_key.strip(),
        "display_name": body.display_name,
        "conversation_id": body.conversation_id,
        "source_bot_id": bot_id,
        "status": body.status,
        "stage": body.stage,
        "last_contacted_at": now,
        "updated_at": now,
        "first_contacted_at": now,
    }
    existing = sb_get(
        "fm_contacts",
        params={
            "profile_key": f"eq.{body.profile_key.strip()}",
            "select": "id,first_contacted_at",
            "limit": "1",
        },
    )
    if existing and existing[0].get("first_contacted_at"):
        row.pop("first_contacted_at", None)
    rows = sb_upsert("fm_contacts", row, on_conflict="profile_key")
    return rows[0] if rows else row


@app.get("/api/bots/{bot_id}/contacts/check")
def check_contact(
    bot_id: int,
    profile_key: str = "",
    display_name: str = "",
    authorization: str | None = Header(default=None),
) -> dict[str, Any]:
    auth.require_bot(bot_id, authorization)
    _require_supabase()
    if profile_key:
        rows = sb_get(
            "fm_contacts",
            params={
                "profile_key": f"eq.{profile_key}",
                "select": "*",
                "limit": "1",
            },
        )
        if rows and rows[0].get("status") != "blocked":
            # contacted or later — treat as duplicate for outreach
            if rows[0].get("status") in (
                "contacted",
                "in_assessment",
                "github_shared",
                "completed",
                "blocked",
            ):
                return {"known": True, "contact": rows[0]}
        if rows and rows[0].get("status") == "blocked":
            return {"known": True, "contact": rows[0]}
    if display_name.strip():
        rows = sb_get(
            "fm_contacts",
            params={
                "display_name": f"ilike.{display_name.strip()}",
                "select": "*",
                "limit": "1",
            },
        )
        if rows:
            return {"known": True, "contact": rows[0]}
    return {"known": False, "contact": None}


# ----- Dashboard: contacts / pipeline / settings -----


@app.get("/api/bots/{bot_id}/contacts")
def list_contacts(
    bot_id: int,
    q: str = "",
    authorization: str | None = Header(default=None),
) -> list[dict]:
    auth.require_dashboard(authorization)
    _require_supabase()
    params: dict[str, str] = {
        "select": "*",
        "order": "updated_at.desc",
        "limit": "100",
    }
    # Show all contacts (shared ledger); optional filter by source bot
    if q.strip():
        params["or"] = f"(display_name.ilike.*{q.strip()}*,profile_key.ilike.*{q.strip()}*)"
    return sb_get("fm_contacts", params=params)


@app.post("/api/contacts/{contact_id}/block")
def block_contact(
    contact_id: int,
    authorization: str | None = Header(default=None),
) -> dict:
    auth.require_dashboard(authorization)
    _require_supabase()
    rows = sb_patch(
        "fm_contacts",
        match={"id": f"eq.{contact_id}"},
        row={"status": "blocked", "updated_at": _now()},
    )
    if not rows:
        raise HTTPException(404)
    return rows[0]


@app.get("/api/bots/{bot_id}/outreach-stats")
def outreach_stats(
    bot_id: int,
    keyword: str = "",
    authorization: str | None = Header(default=None),
) -> dict[str, Any]:
    """Counts for the current keyword's last search run.

    - contacted: freelancers contacted since that search (job stats), not the global ledger
    - available: remaining = profiles_found - contacted
    """
    auth.require_dashboard(authorization)
    if bot_id not in (1, 2, 3):
        raise HTTPException(404)
    _require_supabase()
    kw = keyword.strip()
    available = None
    contacted = None
    profiles_found = None
    last_search_at = None
    if kw:
        jobs = sb_get(
            "outreach_jobs",
            params={
                "bot_id": f"eq.{bot_id}",
                "keyword": f"eq.{kw}",
                "select": "id,stats,started_at,finished_at,created_at,status",
                "order": "created_at.desc",
                "limit": "20",
            },
        )
        for j in jobs:
            stats = j.get("stats") or {}
            found = stats.get("profiles_found")
            if found is None:
                continue
            try:
                profiles_found = max(0, int(found))
            except (TypeError, ValueError):
                continue
            try:
                contacted = max(0, int(stats.get("contacted_since_search") or 0))
            except (TypeError, ValueError):
                contacted = 0
            available = max(0, profiles_found - contacted)
            last_search_at = (
                j.get("started_at") or j.get("finished_at") or j.get("created_at")
            )
            break
    return {
        "keyword": kw or None,
        "profiles_found": profiles_found,
        "contacted": contacted,
        "available": available,
        "last_search_at": last_search_at,
    }


@app.get("/api/bots/{bot_id}/applicants")
def list_applicants(
    bot_id: int,
    authorization: str | None = Header(default=None),
) -> list[dict]:
    auth.require_dashboard(authorization)
    _require_supabase()
    return sb_get(
        "fm_applicants",
        params={
            "bot_id": f"eq.{bot_id}",
            "select": "*",
            "order": "updated_at.desc",
            "limit": "100",
        },
    )


@app.get("/api/bots/{bot_id}/settings")
def get_settings(
    bot_id: int,
    authorization: str | None = Header(default=None),
) -> BotSettings:
    auth.require_dashboard_or_bot(bot_id, authorization)
    return _bot_settings(bot_id)


@app.put("/api/bots/{bot_id}/settings")
def put_settings(
    bot_id: int,
    body: BotSettingsBody,
    authorization: str | None = Header(default=None),
) -> BotSettings:
    auth.require_dashboard(authorization)
    if bot_id not in (1, 2, 3):
        raise HTTPException(404)
    current = _bot_settings(bot_id).model_dump(mode="json")
    patch = body.model_dump(exclude_unset=True)
    current.update({k: v for k, v in patch.items() if v is not None})
    current["updated_at"] = _now()
    sb_patch(
        "bots",
        match={"id": f"eq.{bot_id}"},
        row={"settings": current, "updated_at": _now()},
        return_representation=False,
    )
    return BotSettings(**current)


# ----- Static dashboard -----

if STATIC_DIR.exists():
    app.mount("/assets", StaticFiles(directory=STATIC_DIR), name="assets")


@app.get("/")
def dashboard_index() -> FileResponse:
    index = STATIC_DIR / "index.html"
    if not index.exists():
        raise HTTPException(404, "Dashboard static files missing")
    return FileResponse(index)
