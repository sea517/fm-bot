import logging
import re
from dataclasses import dataclass

from slack_sdk import WebClient
from slack_sdk.errors import SlackApiError

from src.config import SLACK_BOT_TOKEN, SLACK_TEAM_MEMBER_IDS, SLACK_USER_TOKEN
from src.slack.chat import MARKED_CHANNEL_TOKEN, is_marked_slack_channel

logger = logging.getLogger(__name__)


@dataclass
class SlackInviteResult:
    channel_id: str | None = None
    added_to_channel: bool = False
    invite_url: str | None = None
    extra_chat_message: str = ""


def _bot_client() -> WebClient:
    token = SLACK_BOT_TOKEN or SLACK_USER_TOKEN
    if not token:
        raise ValueError("SLACK_BOT_TOKEN required")
    return WebClient(token=token)


def _channel_name(candidate_name: str | None, email: str) -> str:
    base = (candidate_name or email.split("@")[0]).strip()
    slug = re.sub(r"[^a-z0-9]+", "-", base.lower()).strip("-")
    slug = slug[:60] or "candidate"
    return f"candidate-{slug}"


def rename_candidate_channel(channel_id: str, candidate_name: str, email: str = "") -> str | None:
    """
    Rename a private candidate channel to match their real name.
    Returns the new channel name on success, else None.
    """
    if not channel_id or not (candidate_name or "").strip():
        return None
    if is_marked_slack_channel(channel_id):
        logger.info(
            "Skipping rename for %s — channel name contains %s",
            channel_id,
            MARKED_CHANNEL_TOKEN,
        )
        return None
    new_name = _channel_name(candidate_name, email or "candidate")
    client = _bot_client()
    for attempt in range(5):
        name = new_name if attempt == 0 else f"{new_name}-{attempt}"
        try:
            client.conversations_rename(channel=channel_id, name=name)
            logger.info("Renamed Slack channel %s -> #%s", channel_id, name)
            return name
        except SlackApiError as e:
            error = e.response.get("error", str(e))
            if error == "name_taken":
                continue
            if error in ("not_in_channel", "channel_not_found"):
                # Bot may need to be in the channel; try user token if available.
                break
            logger.error("conversations.rename failed for %s: %s", channel_id, error)
            return None

    if SLACK_USER_TOKEN and SLACK_USER_TOKEN != (SLACK_BOT_TOKEN or ""):
        user_client = WebClient(token=SLACK_USER_TOKEN)
        for attempt in range(5):
            name = new_name if attempt == 0 else f"{new_name}-{attempt}"
            try:
                user_client.conversations_rename(channel=channel_id, name=name)
                logger.info("Renamed Slack channel %s -> #%s (user token)", channel_id, name)
                return name
            except SlackApiError as e:
                error = e.response.get("error", str(e))
                if error == "name_taken":
                    continue
                logger.error(
                    "conversations.rename (user) failed for %s: %s", channel_id, error
                )
                return None
    return None


def _create_private_channel(client: WebClient, base_name: str) -> str:
    for attempt in range(5):
        name = base_name if attempt == 0 else f"{base_name}-{attempt}"
        try:
            response = client.conversations_create(name=name, is_private=True)
            channel_id = response["channel"]["id"]
            logger.info("Created private Slack channel #%s (%s)", name, channel_id)
            return channel_id
        except SlackApiError as e:
            if e.response.get("error") == "name_taken":
                continue
            raise
    raise RuntimeError(f"Could not create private Slack channel for {base_name}")


def _add_team_members(client: WebClient, channel_id: str) -> None:
    if not SLACK_TEAM_MEMBER_IDS:
        return
    try:
        client.conversations_invite(channel=channel_id, users=",".join(SLACK_TEAM_MEMBER_IDS))
        logger.info("Added team members to private channel %s", channel_id)
    except SlackApiError as e:
        error = e.response.get("error", str(e))
        if error not in ("already_in_channel", "cant_invite_self"):
            logger.warning("Could not add team members to %s: %s", channel_id, error)


def _invite_external(client: WebClient, email: str, channel_id: str) -> tuple[bool, str | None]:
    """
    Invite candidate as Slack Connect / external connection to the private channel.
    Uses conversations.inviteShared — no workspace membership required.
    """
    try:
        response = client.conversations_inviteShared(
            channel=channel_id,
            emails=[email],
            external_limited=True,
        )
        if not response.get("ok"):
            logger.error("Slack Connect invite failed for %s: %s", email, response.get("error"))
            return False, None

        invite_url = response.get("invite_url") or response.get("url")
        logger.info(
            "Slack Connect invite sent to %s for channel %s (invite_id=%s)",
            email,
            channel_id,
            response.get("invite_id"),
        )
        return True, invite_url
    except SlackApiError as e:
        error = e.response.get("error", str(e))
        logger.error("Slack Connect API error for %s: %s", email, error)
        return False, None


def invite_to_slack(email: str, candidate_name: str | None = None) -> SlackInviteResult:
    """
    Create a private channel per candidate and invite them as an external connection
    (Slack Connect) — they do not join the workspace as a full member.
    """
    if not email:
        raise ValueError("Candidate email required for Slack invite")

    client = _bot_client()
    channel_id = _create_private_channel(client, _channel_name(candidate_name, email))
    _add_team_members(client, channel_id)

    invited, invite_url = _invite_external(client, email, channel_id)
    if not invited:
        return SlackInviteResult(channel_id=channel_id)

    extra = (
        "We've invited you to a private Slack channel as an external connection. "
        "Check your email for the Slack Connect invite"
    )
    if invite_url:
        extra += f", or open this link: {invite_url}"
    extra += "."

    return SlackInviteResult(
        channel_id=channel_id,
        added_to_channel=True,
        invite_url=invite_url,
        extra_chat_message=extra,
    )


def _bot_user_id(client: WebClient) -> str | None:
    try:
        return client.auth_test().get("user_id")
    except SlackApiError as e:
        logger.warning("auth.test failed: %s", e.response.get("error", str(e)))
        return None


def _channel_member_ids(client: WebClient, channel_id: str) -> list[str]:
    members: list[str] = []
    cursor = None
    while True:
        kwargs: dict = {"channel": channel_id, "limit": 200}
        if cursor:
            kwargs["cursor"] = cursor
        try:
            response = client.conversations_members(**kwargs)
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


def _kick_external_members(client: WebClient, channel_id: str) -> bool:
    """
    Remove everyone who is not the bot or a configured team recruiter.

    Slack Connect candidates show up as normal member IDs in the channel;
    we identify them by exclusion rather than by email (lookupByEmail often
    fails for external users).
    """
    keep = set(SLACK_TEAM_MEMBER_IDS)
    bot_id = _bot_user_id(client)
    if bot_id:
        keep.add(bot_id)

    ok = True
    for user_id in _channel_member_ids(client, channel_id):
        if user_id in keep:
            continue
        try:
            client.conversations_kick(channel=channel_id, user=user_id)
            logger.info("Removed user %s from channel %s", user_id, channel_id)
        except SlackApiError as e:
            error = e.response.get("error", str(e))
            if error in ("not_in_channel", "user_not_found", "cant_kick_self"):
                continue
            logger.warning(
                "Could not remove user %s from %s: %s", user_id, channel_id, error
            )
            ok = False
    return ok


def _archive_or_delete_channel(client: WebClient, channel_id: str) -> bool:
    """
    Permanently remove the private channel from active use.

    Workspace bot tokens can archive (conversations.archive). True delete needs
    Enterprise admin.conversations.delete — try that first, then archive.
    """
    try:
        client.admin_conversations_delete(channel_id=channel_id)
        logger.info("Deleted Slack channel %s", channel_id)
        return True
    except SlackApiError as e:
        error = e.response.get("error", str(e))
        if error not in (
            "not_allowed_token_type",
            "missing_scope",
            "not_an_admin",
            "access_denied",
            "unknown_method",
            "invalid_auth",
            "no_permission",
        ):
            logger.warning(
                "admin.conversations.delete for %s: %s — trying archive",
                channel_id,
                error,
            )
        else:
            logger.info(
                "admin.conversations.delete unavailable (%s) — archiving %s",
                error,
                channel_id,
            )
    except Exception as e:
        # Method may not exist on older slack_sdk builds.
        logger.info("admin.conversations.delete unavailable (%s) — archiving %s", e, channel_id)

    try:
        client.conversations_archive(channel=channel_id)
        logger.info("Archived Slack channel %s", channel_id)
        return True
    except SlackApiError as e:
        error = e.response.get("error", str(e))
        if error in ("already_archived", "channel_not_found", "is_archived"):
            logger.info("Channel %s already gone/archived (%s)", channel_id, error)
            return True
        logger.error("Could not archive channel %s: %s", channel_id, error)
        return False


def cleanup_candidate_channel(channel_id: str) -> bool:
    """
    Final step after rejection: remove the applicant from the private channel,
    then delete/archive the channel.
    """
    if not channel_id:
        return True
    if is_marked_slack_channel(channel_id):
        logger.info(
            "Skipping cleanup for %s — channel name contains %s",
            channel_id,
            MARKED_CHANNEL_TOKEN,
        )
        return False
    client = _bot_client()
    kicked = _kick_external_members(client, channel_id)
    archived = _archive_or_delete_channel(client, channel_id)
    if kicked and archived:
        logger.info("Cleaned up candidate Slack channel %s", channel_id)
        return True
    logger.warning(
        "Partial Slack cleanup for %s (kicked=%s archived=%s)",
        channel_id,
        kicked,
        archived,
    )
    # Archive is the irreversible part for our purpose; if that succeeded,
    # treat the cleanup as done even if a kick failed (e.g. already left).
    return archived
