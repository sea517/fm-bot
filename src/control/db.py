"""Supabase REST helpers for the control plane."""

from __future__ import annotations

import logging
from typing import Any

import requests

from src.config import SUPABASE_SERVICE_ROLE_KEY, SUPABASE_URL

logger = logging.getLogger(__name__)
_TIMEOUT = 25


def supabase_ok() -> bool:
    return bool(SUPABASE_URL and SUPABASE_SERVICE_ROLE_KEY)


def _headers(*, prefer: str | None = None) -> dict[str, str]:
    h = {
        "apikey": SUPABASE_SERVICE_ROLE_KEY,
        "Authorization": f"Bearer {SUPABASE_SERVICE_ROLE_KEY}",
        "Content-Type": "application/json",
    }
    if prefer:
        h["Prefer"] = prefer
    return h


def _url(table: str) -> str:
    return f"{SUPABASE_URL.rstrip('/')}/rest/v1/{table}"


def sb_get(
    table: str,
    *,
    params: dict[str, str] | None = None,
) -> list[dict[str, Any]]:
    response = requests.get(
        _url(table),
        headers=_headers(),
        params=params or {},
        timeout=_TIMEOUT,
    )
    if not response.ok:
        logger.error("Supabase GET %s failed %s: %s", table, response.status_code, response.text[:300])
        if response.status_code == 404 and "PGRST205" in response.text:
            raise RuntimeError(
                f"Supabase table '{table}' missing — run supabase/control_plane.sql in the SQL Editor"
            )
        response.raise_for_status()
    data = response.json()
    return data if isinstance(data, list) else []


def sb_insert(
    table: str,
    row: dict[str, Any] | list[dict[str, Any]],
    *,
    return_representation: bool = True,
) -> list[dict[str, Any]]:
    prefer = "return=representation" if return_representation else "return=minimal"
    response = requests.post(
        _url(table),
        headers=_headers(prefer=prefer),
        json=row,
        timeout=_TIMEOUT,
    )
    if not response.ok:
        logger.error("Supabase INSERT %s failed %s: %s", table, response.status_code, response.text[:300])
        response.raise_for_status()
    if not return_representation or not response.content:
        return []
    data = response.json()
    return data if isinstance(data, list) else [data]


def sb_patch(
    table: str,
    *,
    match: dict[str, str],
    row: dict[str, Any],
    return_representation: bool = True,
) -> list[dict[str, Any]]:
    prefer = "return=representation" if return_representation else "return=minimal"
    response = requests.patch(
        _url(table),
        headers=_headers(prefer=prefer),
        params=match,
        json=row,
        timeout=_TIMEOUT,
    )
    if not response.ok:
        logger.error("Supabase PATCH %s failed %s: %s", table, response.status_code, response.text[:300])
        response.raise_for_status()
    if not return_representation or not response.content:
        return []
    data = response.json()
    return data if isinstance(data, list) else [data]


def sb_count(
    table: str,
    *,
    params: dict[str, str] | None = None,
) -> int:
    """Exact row count via PostgREST Content-Range."""
    headers = _headers(prefer="count=exact")
    headers["Range"] = "0-0"
    query = dict(params or {})
    query.setdefault("select", "id")
    response = requests.get(
        _url(table),
        headers=headers,
        params=query,
        timeout=_TIMEOUT,
    )
    if not response.ok:
        logger.error("Supabase COUNT %s failed %s: %s", table, response.status_code, response.text[:300])
        if response.status_code == 404 and "PGRST205" in response.text:
            raise RuntimeError(
                f"Supabase table '{table}' missing — run supabase/control_plane.sql in the SQL Editor"
            )
        response.raise_for_status()
    cr = response.headers.get("Content-Range") or response.headers.get("content-range") or ""
    if "/" in cr:
        total = cr.rsplit("/", 1)[-1]
        if total != "*":
            return int(total)
    return 0


def sb_upsert(
    table: str,
    row: dict[str, Any],
    *,
    on_conflict: str,
) -> list[dict[str, Any]]:
    response = requests.post(
        _url(table),
        headers=_headers(prefer="resolution=merge-duplicates,return=representation"),
        params={"on_conflict": on_conflict},
        json=row,
        timeout=_TIMEOUT,
    )
    if not response.ok:
        logger.error("Supabase UPSERT %s failed %s: %s", table, response.status_code, response.text[:300])
        response.raise_for_status()
    if not response.content:
        return []
    data = response.json()
    return data if isinstance(data, list) else [data]
