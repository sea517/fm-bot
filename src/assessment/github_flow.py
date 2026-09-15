"""GitHub username capture, User validation, and collaborator invite (SPEC)."""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from typing import Literal
from urllib.parse import urlparse

import requests

from src.config import ASSESSMENT_REPO, GITHUB_ORG, GITHUB_REPO, GITHUB_TOKEN

logger = logging.getLogger(__name__)

ValidationKind = Literal["ok_user", "organization", "invalid", "ambiguous"]
InviteKind = Literal["ok", "ambiguous"]

_RESERVED = frozenset(
    {
        "about",
        "features",
        "pricing",
        "orgs",
        "settings",
        "login",
        "signup",
        "explore",
        "topics",
        "collections",
        "events",
        "sponsors",
        "marketplace",
        "pulls",
        "issues",
        "notifications",
        "account",
        "organizations",
        "github",
        "raw",
        "gist",
        "www",
        "api",
        "apps",
        "enterprise",
        "customer",
        "security",
        "site",
        "search",
        "new",
        "dashboard",
    }
)


@dataclass
class ParseResult:
    raw: str
    parsed: str | None
    invalid_reason: str | None = None


@dataclass
class ValidationResult:
    kind: ValidationKind
    username: str | None
    detail: str


@dataclass
class InviteResult:
    kind: InviteKind
    detail: str
    status_code: int | None = None


def _headers() -> dict[str, str]:
    return {
        "Authorization": f"Bearer {GITHUB_TOKEN}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
    }


def _repo_target() -> tuple[str, str]:
    org = GITHUB_ORG
    repo = ASSESSMENT_REPO
    if (not org or not repo) and GITHUB_REPO and "/" in GITHUB_REPO:
        org, repo = GITHUB_REPO.split("/", 1)
    if not GITHUB_TOKEN or not org or not repo:
        raise ValueError("GITHUB_TOKEN and GITHUB_ORG/ASSESSMENT_REPO (or GITHUB_REPO) required")
    return org, repo


def normalize_github_reference(text: str) -> ParseResult:
    """Extract and normalize a GitHub username from free text (SPEC steps 2–4)."""
    raw = (text or "").strip()
    if not raw:
        return ParseResult(raw="", parsed=None, invalid_reason="empty")

    # Prefer URL owner segment
    url_m = re.search(
        r"(?:https?://)?(?:www\.)?(?:gist\.)?github\.com/([^\s/?#]+)",
        raw,
        re.I,
    )
    candidate = None
    if url_m:
        path = url_m.group(1)
        # strip query/fragment leftovers if regex caught them
        path = path.split("?")[0].split("#")[0].rstrip("/")
        if path.endswith(".git"):
            path = path[:-4]
        # first path segment only
        candidate = path.split("/")[0]
    else:
        at = re.search(
            r"(?<![A-Za-z0-9-])@([A-Za-z0-9](?:[A-Za-z0-9-]{0,37}[A-Za-z0-9])?)",
            raw,
        )
        if at:
            candidate = at.group(1)
        else:
            labeled = re.search(
                r"(?:github\s*(?:username|user|handle)?|username|user\s*name|handle)"
                r"\s*(?:is|:)\s*@?([A-Za-z0-9](?:[A-Za-z0-9-]{0,37}[A-Za-z0-9])?)",
                raw,
                re.I,
            )
            if labeled:
                candidate = labeled.group(1)
            else:
                # bare token on its own line or whole message
                for line in raw.splitlines():
                    tok = line.strip().strip(".,;:<>\"'()[]")
                    tok = tok.lstrip("@")
                    if re.fullmatch(
                        r"[A-Za-z0-9](?:[A-Za-z0-9-]{0,37}[A-Za-z0-9])?", tok
                    ):
                        candidate = tok
                        break
                if not candidate:
                    tok = raw.strip().strip(".,;:<>\"'()[]").lstrip("@")
                    if re.fullmatch(
                        r"[A-Za-z0-9](?:[A-Za-z0-9-]{0,37}[A-Za-z0-9])?", tok
                    ):
                        candidate = tok

    if not candidate:
        return ParseResult(raw=raw, parsed=None, invalid_reason="no_match")

    user = candidate.strip().lstrip("@").strip().strip("/")
    # strip angle brackets
    user = user.strip("<>").split("?")[0].split("#")[0]
    if user.endswith(".git"):
        user = user[:-4]
    if "/" in user:
        user = user.split("/", 1)[0]

    if not re.fullmatch(r"[A-Za-z0-9](?:[A-Za-z0-9-]{0,37}[A-Za-z0-9])?", user):
        return ParseResult(raw=raw, parsed=None, invalid_reason="username_rules")
    if user.lower() in _RESERVED:
        return ParseResult(raw=raw, parsed=None, invalid_reason="reserved")
    return ParseResult(raw=raw, parsed=user)


def scan_github_reference(text: str) -> ParseResult | None:
    """Return a parse if the message likely contains a GitHub reference."""
    low = (text or "").lower()
    if "github" not in low and "@" not in (text or "") and "gist.github" not in low:
        # still allow bare username only when explicitly short
        if not re.fullmatch(
            r"\s*@?[A-Za-z0-9](?:[A-Za-z0-9-]{0,37}[A-Za-z0-9])?\s*", text or ""
        ):
            return None
    parsed = normalize_github_reference(text)
    if parsed.parsed or parsed.invalid_reason in ("username_rules", "reserved"):
        return parsed
    if "github.com" in low or "gist.github" in low:
        return parsed
    return None if not parsed.parsed else parsed


def validate_github_user(username: str) -> ValidationResult:
    """GET /users/{owner}; require type == User. No silent retries."""
    if not GITHUB_TOKEN:
        return ValidationResult("ambiguous", username, "GITHUB_TOKEN missing")
    url = f"https://api.github.com/users/{username}"
    try:
        res = requests.get(url, headers=_headers(), timeout=30)
    except requests.RequestException as e:
        return ValidationResult("ambiguous", username, f"timeout_or_network:{e}")

    if res.status_code == 404:
        return ValidationResult("invalid", username, "404")
    if res.status_code != 200:
        return ValidationResult(
            "ambiguous", username, f"status:{res.status_code}:{res.text[:120]}"
        )
    try:
        data = res.json()
    except ValueError:
        return ValidationResult("ambiguous", username, "unparseable_json")
    typ = (data.get("type") or "").strip()
    if typ == "User":
        return ValidationResult("ok_user", username, "ok")
    if typ == "Organization":
        return ValidationResult("organization", username, "organization")
    return ValidationResult("ambiguous", username, f"unexpected_type:{typ}")


def invite_collaborator(username: str) -> InviteResult:
    """PUT collaborator (or org membership). 201/204 = success. No retries."""
    try:
        org, repo = _repo_target()
    except ValueError as e:
        return InviteResult("ambiguous", str(e), None)

    # Prefer repo collaborator endpoint (matches current ops).
    url = f"https://api.github.com/repos/{org}/{repo}/collaborators/{username}"
    try:
        res = requests.put(
            url, headers=_headers(), json={"permission": "pull"}, timeout=30
        )
    except requests.RequestException as e:
        return InviteResult("ambiguous", f"timeout_or_network:{e}", None)

    if res.status_code in (201, 204):
        return InviteResult("ok", "invited_or_already", res.status_code)
    if res.status_code == 422 and "already" in (res.text or "").lower():
        return InviteResult("ok", "already_exists", res.status_code)
    return InviteResult(
        "ambiguous", f"status:{res.status_code}:{res.text[:160]}", res.status_code
    )


# Back-compat wrappers used elsewhere
def extract_github_username(text: str) -> str | None:
    return normalize_github_reference(text).parsed


def invite_to_repo(github_username: str) -> bool:
    return invite_collaborator(github_username).kind == "ok"
