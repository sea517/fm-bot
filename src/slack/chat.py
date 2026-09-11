import logging
import re
import time
from pathlib import Path
from typing import Any, Callable, TypeVar

from slack_sdk import WebClient
from slack_sdk.errors import SlackApiError

from src.config import (
    ROOT_DIR,
    SLACK_BOT_TOKEN,
    SLACK_CLIENT_ID,
    SLACK_CLIENT_SECRET,
    SLACK_TEAM_MEMBER_IDS,
    SLACK_USER_REFRESH_TOKEN,
    SLACK_USER_TOKEN,
)

logger = logging.getLogger(__name__)

# Private channel deleted/archived — stop polling that applicant.
CHANNEL_GONE_ERRORS = frozenset({"channel_not_found", "is_archived", "invalid_channel"})
CHANNEL_GONE = "__CHANNEL_GONE__"

# Human-owned channels: rename to include this token (e.g. candidate-foo-marked-).
# Bot must not poll, post, rename, or clean up those channels.
MARKED_CHANNEL_TOKEN = "-marked-"

_T = TypeVar("_T")
_RATE_LIMIT_RETRIES = 4
_RATE_LIMIT_DEFAULT_WAIT = 5.0
_RATE_LIMIT_MAX_WAIT = 30.0
_CHANNEL_NAME_TTL_SECONDS = 120.0

_cached_poster_user_id: str | None = None
# Mutable copies so a mid-run refresh updates the token used for posting.
_user_token = SLACK_USER_TOKEN
_refresh_token = SLACK_USER_REFRESH_TOKEN
# Set when a poll cycle should stop hammering Slack after repeated rate limits.
_rate_limit_exhausted = False
# channel_id -> (name, monotonic_fetched_at)
_channel_name_cache: dict[str, tuple[str, float]] = {}


def rate_limit_exhausted() -> bool:
    return _rate_limit_exhausted


def clear_rate_limit_exhausted() -> None:
    global _rate_limit_exhausted
    _rate_limit_exhausted = False


def _retry_after_seconds(exc: SlackApiError) -> float:
    headers = getattr(exc.response, "headers", None) or {}
    raw = headers.get("Retry-After") or headers.get("retry-after")
    try:
        wait = float(raw) if raw is not None else _RATE_LIMIT_DEFAULT_WAIT
    except (TypeError, ValueError):
        wait = _RATE_LIMIT_DEFAULT_WAIT
    return max(1.0, min(wait, _RATE_LIMIT_MAX_WAIT))


def _slack_api(call: Callable[[], _T], *, what: str) -> _T:
    """Run a Slack SDK call with Retry-After backoff on ratelimited."""
    global _rate_limit_exhausted
    last_exc: SlackApiError | None = None
    for attempt in range(1, _RATE_LIMIT_RETRIES + 1):
        try:
            return call()
        except SlackApiError as e:
            err = e.response.get("error", str(e))
            if err != "ratelimited":
                raise
            last_exc = e
            wait = _retry_after_seconds(e)
            logger.warning(
                "Slack ratelimited on %s (attempt %s/%s) — sleeping %.1fs",
                what,
                attempt,
                _RATE_LIMIT_RETRIES,
                wait,
            )
            time.sleep(wait)
    _rate_limit_exhausted = True
    assert last_exc is not None
    raise last_exc


def _bot_client() -> WebClient:
    token = SLACK_BOT_TOKEN or _user_token
    if not token:
        raise ValueError("SLACK_BOT_TOKEN required")
    return WebClient(token=token)


def _is_user_access_token(token: str) -> bool:
    return bool(token) and (
        token.startswith("xoxp-") or token.startswith("xoxe.xoxp-")
    )


def _update_env_value(key: str, value: str) -> None:
    """Persist a refreshed token back into .env so restarts keep working."""
    path = ROOT_DIR / ".env"
    if not path.exists() or not value:
        return
    text = path.read_text(encoding="utf-8")
    pattern = re.compile(rf"^{re.escape(key)}=.*$", re.MULTILINE)
    line = f"{key}={value}"
    if pattern.search(text):
        text = pattern.sub(line, text)
    else:
        text = text.rstrip() + f"\n{line}\n"
    path.write_text(text, encoding="utf-8")


def _refresh_user_token() -> bool:
    """Exchange refresh token for a new access token (Slack token rotation)."""
    global _user_token, _refresh_token, _cached_poster_user_id
    if not (_refresh_token and SLACK_CLIENT_ID and SLACK_CLIENT_SECRET):
        logger.error(
            "Oliver user token expired/rotated, but SLACK_USER_REFRESH_TOKEN / "
            "SLACK_CLIENT_ID / SLACK_CLIENT_SECRET are not all set"
        )
        return False
    try:
        import requests

        response = requests.post(
            "https://slack.com/api/oauth.v2.access",
            data={
                "client_id": SLACK_CLIENT_ID,
                "client_secret": SLACK_CLIENT_SECRET,
                "grant_type": "refresh_token",
                "refresh_token": _refresh_token,
            },
            timeout=30,
        )
        payload = response.json()
    except Exception:
        logger.exception("Failed to refresh Slack user token")
        return False

    if not payload.get("ok"):
        logger.error("Slack token refresh failed: %s", payload.get("error"))
        return False

    # Refresh responses may nest under authed_user or top-level access_token.
    authed = payload.get("authed_user") or {}
    new_access = authed.get("access_token") or payload.get("access_token") or ""
    new_refresh = authed.get("refresh_token") or payload.get("refresh_token") or ""
    if not _is_user_access_token(new_access):
        logger.error("Slack token refresh returned no user access token: %s", payload)
        return False

    _user_token = new_access
    if new_refresh:
        _refresh_token = new_refresh
    _cached_poster_user_id = None
    _update_env_value("SLACK_USER_TOKEN", _user_token)
    if new_refresh:
        _update_env_value("SLACK_USER_REFRESH_TOKEN", _refresh_token)
    logger.info("Refreshed Oliver Slack user token")
    return True


def _post_client() -> WebClient:
    """
    Assessment messages must appear as Oliver (a real person), not the Kontrora app.

    Requires Oliver's user OAuth token in SLACK_USER_TOKEN (xoxp- or xoxe.xoxp-).
    """
    if _user_token and _is_user_access_token(_user_token):
        return WebClient(token=_user_token)
    logger.warning(
        "SLACK_USER_TOKEN is empty or not a user token — posting as the Kontrora "
        "bot app. Run: PYTHONPATH=. python3 -m src.slack_user_token ..."
    )
    return _bot_client()


def _poster_user_id() -> str | None:
    """Slack user id of whoever SLACK_USER_TOKEN represents (Oliver)."""
    global _cached_poster_user_id
    if _cached_poster_user_id:
        return _cached_poster_user_id
    if not (_user_token and _is_user_access_token(_user_token)):
        return None
    try:
        _cached_poster_user_id = WebClient(token=_user_token).auth_test().get("user_id")
        return _cached_poster_user_id
    except SlackApiError as e:
        err = e.response.get("error", str(e))
        if err in ("token_expired", "invalid_auth") and _refresh_user_token():
            try:
                _cached_poster_user_id = WebClient(token=_user_token).auth_test().get(
                    "user_id"
                )
                return _cached_poster_user_id
            except SlackApiError as e2:
                logger.warning(
                    "Could not resolve Oliver user id after refresh: %s",
                    e2.response.get("error", str(e2)),
                )
                return None
        logger.warning("Could not resolve Oliver user id from SLACK_USER_TOKEN: %s", err)
        return None


def _ignored_author_ids() -> set[str]:
    """Recruiters / Oliver — never treat their Slack messages as candidate replies."""
    ignored = set(SLACK_TEAM_MEMBER_IDS)
    poster = _poster_user_id()
    if poster:
        ignored.add(poster)
    return ignored


def get_slack_channel_name(channel_id: str) -> str | None:
    """Return the current Slack channel name, with a short in-process cache."""
    if not channel_id:
        return None
    cached = _channel_name_cache.get(channel_id)
    now = time.monotonic()
    if cached and (now - cached[1]) < _CHANNEL_NAME_TTL_SECONDS:
        return cached[0]
    try:
        response = _slack_api(
            lambda: _bot_client().conversations_info(channel=channel_id),
            what=f"conversations.info:{channel_id}",
        )
        name = (response.get("channel") or {}).get("name") or ""
        if name:
            _channel_name_cache[channel_id] = (name, now)
            return name
    except SlackApiError as e:
        err = e.response.get("error", str(e))
        if err in CHANNEL_GONE_ERRORS:
            logger.warning("Slack channel %s is gone (%s) while resolving name", channel_id, err)
            return None
        logger.warning("conversations.info failed for %s: %s", channel_id, err)
    return None


def is_marked_slack_channel(channel_id: str | None) -> bool:
    """True when the private channel name contains -marked- (human-owned; do not touch)."""
    if not channel_id:
        return False
    name = get_slack_channel_name(channel_id)
    if not name:
        return False
    return MARKED_CHANNEL_TOKEN in name


def post_slack_message(channel_id: str, text: str) -> str | None:
    """
    Post as Oliver when possible. Returns the Slack message ts on success,
    CHANNEL_GONE if the channel was deleted/archived, or None on other failure.
    """
    if is_marked_slack_channel(channel_id):
        name = get_slack_channel_name(channel_id) or channel_id
        logger.info("Skipping Slack post — channel #%s is marked", name)
        return None
    as_oliver = bool(_user_token and _is_user_access_token(_user_token))
    for attempt in (1, 2):
        client = _post_client()
        try:
            response = client.chat_postMessage(channel=channel_id, text=text)
            if response.get("ok"):
                ts = response.get("ts")
                logger.info(
                    "Slack message posted to %s as %s ts=%s",
                    channel_id,
                    "Oliver (user token)" if as_oliver else "Kontrora app (bot token)",
                    ts,
                )
                return str(ts) if ts else "0"
            err = response.get("error") or "unknown"
            if err in CHANNEL_GONE_ERRORS:
                logger.warning("Slack channel %s is gone (%s)", channel_id, err)
                return CHANNEL_GONE
            logger.error("Slack post failed: %s", err)
            return None
        except SlackApiError as e:
            err = e.response.get("error", str(e))
            if (
                attempt == 1
                and as_oliver
                and err in ("token_expired", "invalid_auth")
                and _refresh_user_token()
            ):
                logger.warning("Oliver token expired — refreshed, retrying post")
                continue
            if err in CHANNEL_GONE_ERRORS:
                logger.warning("Slack channel %s is gone (%s)", channel_id, err)
                return CHANNEL_GONE
            logger.error("Slack post error: %s", err)
            return None
    return None


def _message_text(item: dict) -> str:
    text = (item.get("text") or "").strip()
    files = item.get("files") or []
    if not text and files:
        names = ", ".join(
            (f.get("name") or f.get("title") or "file") for f in files[:3]
        )
        text = f"[shared: {names}]"
    return text


def _thread_replies(channel_id: str, thread_ts: str) -> list[dict]:
    """All messages in a thread (parent first). Empty on error."""
    try:
        response = _slack_api(
            lambda: _bot_client().conversations_replies(
                channel=channel_id,
                ts=thread_ts,
                limit=100,
                inclusive=True,
            ),
            what=f"conversations.replies:{channel_id}",
        )
        return list(response.get("messages") or [])
    except SlackApiError as e:
        err = e.response.get("error", str(e))
        if err not in CHANNEL_GONE_ERRORS:
            logger.warning(
                "Slack thread replies failed for %s ts=%s: %s",
                channel_id,
                thread_ts,
                err,
            )
        return []


def _message_effective_ts(item: dict) -> float | None:
    """
    High-water timestamp for a Slack message: max(original ts, edit ts).

    Candidates often post a short placeholder then edit in the real answer;
    the original ts does not move, so we must watch edited.ts.
    """
    ts = item.get("ts")
    if not ts:
        return None
    try:
        effective = float(ts)
    except ValueError:
        return None
    edited = item.get("edited") or {}
    edited_ts = edited.get("ts")
    if edited_ts:
        try:
            effective = max(effective, float(edited_ts))
        except ValueError:
            pass
    return effective


def latest_human_message(
    channel_id: str, after_ts: str | None = None
) -> tuple[str | None, str | None]:
    """
    Return (text, latest_ts) for all candidate messages after after_ts.

    Includes top-level channel messages and replies inside threads (candidates
    often paste fork URLs as a thread reply on Oliver's invite).
    Also includes messages that were *edited* after after_ts (same original ts).
    Multiple bubbles are joined oldest → newest. Authors in SLACK_TEAM_MEMBER_IDS
    / Oliver are ignored.

    latest_ts is a cursor watermark (may be an edit timestamp) for slack_last_ts.
    """
    ignored = _ignored_author_ids()
    bot_id = _bot_user_id()
    if bot_id:
        ignored.add(bot_id)
    after = float(after_ts) if after_ts else 0.0
    # Scan parents a bit older than after_ts so new thread replies / edits under
    # an older Oliver message are still found.
    parent_oldest = max(0.0, after - 7 * 24 * 3600) if after_ts else 0.0
    try:
        kwargs: dict[str, Any] = {
            "channel": channel_id,
            "limit": 50,
            "inclusive": False,
        }
        if after_ts:
            kwargs["oldest"] = str(parent_oldest)
        response = _slack_api(
            lambda: _bot_client().conversations_history(**kwargs),
            what=f"conversations.history:{channel_id}",
        )
        parents = list(response.get("messages") or [])

        collected: list[tuple[str, str]] = []
        seen_ts: set[str] = set()

        def _maybe_add(item: dict) -> None:
            if item.get("subtype") or item.get("bot_id") or item.get("app_id"):
                return
            user_id = item.get("user") or ""
            if user_id in ignored:
                return
            ts = item.get("ts")
            if not ts or ts in seen_ts:
                return
            effective = _message_effective_ts(item)
            if effective is None or effective <= after:
                return
            text = _message_text(item)
            if not text:
                return
            seen_ts.add(str(ts))
            # Advance cursor past the edit time so the same edit is not re-read.
            cursor = f"{effective:.6f}"
            if item.get("edited") and float(ts) <= after:
                logger.info(
                    "Slack edit detected in %s ts=%s edited_ts=%s (%d chars)",
                    channel_id,
                    ts,
                    cursor,
                    len(text),
                )
            collected.append((text, cursor))

        for item in parents:
            _maybe_add(item)
            latest_reply = item.get("latest_reply")
            has_thread = int(item.get("reply_count") or 0) > 0 or bool(latest_reply)
            if not has_thread:
                continue
            # Skip cold threads with nothing newer than the cursor, unless the
            # parent is recent enough that a reply edit is still plausible.
            if after_ts and latest_reply:
                try:
                    parent_eff = _message_effective_ts(item) or 0.0
                    if float(latest_reply) <= after and parent_eff <= after:
                        parent_age_ok = float(item.get("ts") or 0) >= after - 2 * 24 * 3600
                        if not parent_age_ok:
                            continue
                except ValueError:
                    pass
            parent_ts = item.get("thread_ts") or item.get("ts")
            if not parent_ts:
                continue
            for reply in _thread_replies(channel_id, str(parent_ts)):
                if reply.get("ts") == parent_ts:
                    continue
                _maybe_add(reply)

        if not collected:
            return None, None
        collected.sort(key=lambda pair: float(pair[1]))
        combined = "\n".join(t for t, _ in collected).strip()
        latest_ts = collected[-1][1]
        return combined, latest_ts
    except SlackApiError as e:
        err = e.response.get("error", str(e))
        if err in CHANNEL_GONE_ERRORS:
            logger.warning("Slack channel %s is gone (%s) — stop reading", channel_id, err)
            return CHANNEL_GONE, None
        logger.error(
            "Slack history error for %s: %s",
            channel_id,
            err,
        )
        return None, None


_cached_bot_user_id: str | None = None


def _bot_user_id() -> str | None:
    global _cached_bot_user_id
    if _cached_bot_user_id:
        return _cached_bot_user_id
    try:
        _cached_bot_user_id = _bot_client().auth_test().get("user_id")
        return _cached_bot_user_id
    except SlackApiError as e:
        logger.warning("auth.test (bot) failed: %s", e.response.get("error", str(e)))
        return None


def channel_member_ids(channel_id: str) -> list[str]:
    """All user ids currently in the private channel."""
    client = _bot_client()
    members: list[str] = []
    cursor = None
    while True:
        kwargs: dict = {"channel": channel_id, "limit": 200}
        if cursor:
            kwargs["cursor"] = cursor
        try:
            response = _slack_api(
                lambda kw=kwargs: client.conversations_members(**kw),
                what=f"conversations.members:{channel_id}",
            )
        except SlackApiError as e:
            logger.error(
                "conversations.members failed for %s: %s",
                channel_id,
                e.response.get("error", str(e)),
            )
            break
        members.extend(response.get("members") or [])
        cursor = (response.get("response_metadata") or {}).get("next_cursor") or None
        if not cursor:
            break
    return members


def candidate_has_joined_channel(channel_id: str) -> bool:
    """
    True once someone other than the bot / recruiters is in the channel.

    Used so Oliver can greet first on waiting_join without waiting for the
    candidate to say hello.
    """
    if not channel_id:
        return False
    keep = set(SLACK_TEAM_MEMBER_IDS)
    bot_id = _bot_user_id()
    if bot_id:
        keep.add(bot_id)
    poster = _poster_user_id()
    if poster:
        keep.add(poster)
    for user_id in channel_member_ids(channel_id):
        if user_id not in keep:
            return True
    return False
