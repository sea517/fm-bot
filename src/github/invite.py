import logging
import re

import requests

from src.config import GITHUB_REPO, GITHUB_TOKEN

logger = logging.getLogger(__name__)

# Words the matcher must never treat as a GitHub username.
# (Slack links like <https://github.com/user> used to match "https";
# "github username for the test" used to match "for".)
_RESERVED = frozenset(
    {
        "a",
        "account",
        "and",
        "best",
        "com",
        "for",
        "github",
        "handle",
        "here",
        "http",
        "https",
        "i",
        "io",
        "is",
        "it",
        "mailto",
        "me",
        "much",
        "my",
        "name",
        "org",
        "please",
        "profile",
        "share",
        "so",
        "test",
        "thank",
        "thanks",
        "the",
        "this",
        "to",
        "user",
        "username",
        "will",
        "www",
        "you",
        "your",
    }
)

# github.com/username (Slack: <https://github.com/user>)
_URL_USER = re.compile(
    r"github\.com/([A-Za-z0-9](?:[A-Za-z0-9-]{0,37}[A-Za-z0-9])?)",
    re.I,
)
_AT_USER = re.compile(
    r"(?<![A-Za-z0-9-])@([A-Za-z0-9](?:[A-Za-z0-9-]{0,37}[A-Za-z0-9])?)"
)
_BARE_USER = re.compile(
    r"^([A-Za-z0-9](?:[A-Za-z0-9-]{0,37}[A-Za-z0-9])?)$"
)
# Require "is" or ":" so "github username for the test" does not capture "for".
_LABELED_USER = re.compile(
    r"(?:github\s*(?:username|user|handle)?|username|user\s*name|handle)"
    r"\s*(?:is|:)\s*@?([A-Za-z0-9](?:[A-Za-z0-9-]{0,37}[A-Za-z0-9])?)",
    re.I,
)


def _valid_username(user: str | None) -> str | None:
    if not user or user.lower() in _RESERVED:
        return None
    # Real GitHub usernames are rarely 1–2 letter English particles; still allow
    # longer tokens only when not reserved.
    if len(user) < 3:
        return None
    # Slack member/bot IDs look like U0BQS25R6GM / W0123ABCD — never GitHub.
    if re.fullmatch(r"[UWS][A-Z0-9]{8,}", user, re.I):
        return None
    return user


def extract_github_username(text: str) -> str | None:
    if not text or not text.strip():
        return None

    # Drop Slack mention / channel / link markup before @-matching.
    cleaned_markup = re.sub(r"<@[UWS][A-Z0-9]+(?:\|[^>]*)?>", " ", text, flags=re.I)
    cleaned_markup = re.sub(r"<#[A-Z0-9]+\|[^>]*>", " ", cleaned_markup, flags=re.I)

    m = _URL_USER.search(cleaned_markup)
    if m:
        user = _valid_username(m.group(1))
        if user:
            return user

    m = _AT_USER.search(cleaned_markup)
    if m:
        user = _valid_username(m.group(1))
        if user:
            return user

    cleaned = re.sub(r"<https?://[^>]+>", " ", cleaned_markup)
    cleaned = re.sub(r"https?://\S+", " ", cleaned)

    # Prefer a standalone line that is only the username (common Slack style).
    for raw_line in cleaned.splitlines():
        line = raw_line.strip().strip(".,;:!\"'")
        if not line:
            continue
        m = _BARE_USER.match(line)
        if m:
            user = _valid_username(m.group(1))
            if user:
                return user

    cleaned_flat = cleaned.strip().strip(".,;:!\"'")
    tokens = cleaned_flat.split()
    if len(tokens) == 1:
        m = _BARE_USER.match(tokens[0])
        if m:
            user = _valid_username(m.group(1))
            if user:
                return user

    m = _LABELED_USER.search(cleaned_flat)
    if m:
        user = _valid_username(m.group(1))
        if user:
            return user

    return None


def _repo_parts() -> tuple[str, str]:
    if not GITHUB_TOKEN or not GITHUB_REPO:
        raise ValueError("GITHUB_TOKEN and GITHUB_REPO required in .env")
    owner, repo = GITHUB_REPO.split("/", 1)
    return owner, repo


def _headers() -> dict[str, str]:
    return {
        "Authorization": f"Bearer {GITHUB_TOKEN}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
    }


def invite_to_repo(github_username: str) -> bool:
    owner, repo = _repo_parts()
    url = f"https://api.github.com/repos/{owner}/{repo}/collaborators/{github_username}"
    response = requests.put(url, headers=_headers(), json={"permission": "pull"}, timeout=30)

    if response.status_code in (201, 204):
        logger.info("GitHub invite sent to %s for %s", github_username, GITHUB_REPO)
        return True
    if response.status_code == 422 and "already exists" in response.text.lower():
        logger.info("%s already has access to %s", github_username, GITHUB_REPO)
        return True

    logger.error("GitHub invite failed (%s): %s", response.status_code, response.text[:200])
    return False


def remove_repo_collaborator(github_username: str) -> bool:
    """Cancel a pending invite / remove collaborator access."""
    owner, repo = _repo_parts()
    url = f"https://api.github.com/repos/{owner}/{repo}/collaborators/{github_username}"
    response = requests.delete(url, headers=_headers(), timeout=30)
    if response.status_code in (204, 404):
        logger.info("Removed GitHub collaborator/invite %s from %s", github_username, GITHUB_REPO)
        return True
    logger.error(
        "GitHub remove collaborator failed (%s): %s",
        response.status_code,
        response.text[:200],
    )
    return False


def candidate_submitted_assignment(github_username: str) -> bool:
    """
    True if the candidate appears to have submitted work on the assignment repo:
    a PR they authored, and/or commits attributed to them.
    """
    if not github_username or not GITHUB_TOKEN or not GITHUB_REPO:
        return False

    owner, repo = _repo_parts()
    headers = _headers()
    user = github_username.strip().lstrip("@")
    user_l = user.lower()

    try:
        pulls = requests.get(
            f"https://api.github.com/repos/{owner}/{repo}/pulls",
            headers=headers,
            params={"state": "all", "per_page": 50},
            timeout=30,
        )
        if pulls.ok:
            for pr in pulls.json():
                login = ((pr.get("user") or {}).get("login") or "").lower()
                if login == user_l:
                    logger.info(
                        "Assignment submission found: PR #%s by %s",
                        pr.get("number"),
                        user,
                    )
                    return True

        commits = requests.get(
            f"https://api.github.com/repos/{owner}/{repo}/commits",
            headers=headers,
            params={"author": user, "per_page": 5},
            timeout=30,
        )
        if commits.ok and isinstance(commits.json(), list) and commits.json():
            logger.info("Assignment submission found: commits by %s", user)
            return True
    except requests.RequestException:
        logger.exception("GitHub assignment check failed for %s", user)
        return False

    return False
