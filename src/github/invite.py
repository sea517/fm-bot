"""GitHub helpers — SPEC validation lives in assessment.github_flow."""

from src.assessment.github_flow import (  # noqa: F401
    extract_github_username,
    invite_collaborator,
    invite_to_repo,
    normalize_github_reference,
    scan_github_reference,
    validate_github_user,
)
from src.assessment.github_flow import invite_to_repo as _invite

# Keep candidate_submitted_assignment / remove_repo_collaborator from prior module body
import logging
import requests

from src.config import ASSESSMENT_REPO, GITHUB_ORG, GITHUB_REPO, GITHUB_TOKEN

logger = logging.getLogger(__name__)


def _repo_parts() -> tuple[str, str]:
    org = GITHUB_ORG
    repo = ASSESSMENT_REPO
    if (not org or not repo) and GITHUB_REPO and "/" in GITHUB_REPO:
        org, repo = GITHUB_REPO.split("/", 1)
    if not GITHUB_TOKEN or not org or not repo:
        raise ValueError("GITHUB_TOKEN and repo config required")
    return org, repo


def _headers() -> dict[str, str]:
    return {
        "Authorization": f"Bearer {GITHUB_TOKEN}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
    }


def remove_repo_collaborator(github_username: str) -> bool:
    owner, repo = _repo_parts()
    url = f"https://api.github.com/repos/{owner}/{repo}/collaborators/{github_username}"
    response = requests.delete(url, headers=_headers(), timeout=30)
    if response.status_code in (204, 404):
        logger.info("Removed GitHub collaborator/invite %s from %s/%s", github_username, owner, repo)
        return True
    logger.error(
        "GitHub remove collaborator failed (%s): %s",
        response.status_code,
        response.text[:200],
    )
    return False


def candidate_submitted_assignment(github_username: str) -> bool:
    if not github_username or not GITHUB_TOKEN:
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
                    return True
        commits = requests.get(
            f"https://api.github.com/repos/{owner}/{repo}/commits",
            headers=headers,
            params={"author": user, "per_page": 5},
            timeout=30,
        )
        if commits.ok and isinstance(commits.json(), list) and commits.json():
            return True
    except requests.RequestException:
        logger.exception("GitHub assignment check failed for %s", user)
        return False
    return False
