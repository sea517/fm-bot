"""Conversation replies for freelancermap only (email + Slack channels removed)."""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from datetime import datetime, timezone

from src.ai.gemini import generate_first_reply, generate_second_reply
from src.conversation import stages as S
from src.conversation.templates import MSG_FM_REPLY_3
from src.conversation.timing import schedule_after_candidate_message
from src.db.models import Applicant
from src.db.projects import get_project_for_chat

logger = logging.getLogger(__name__)

FM_STAGES = {
    S.STAGE_WAITING_REPLY_1,
    S.STAGE_WAITING_REPLY_2,
    S.STAGE_WAITING_REPLY_3,
}
# Empty — kept so pipeline/main imports do not break.
EMAIL_STAGES: set[str] = set()
SLACK_STAGES: set[str] = set()
SLACK_ASSESSMENT_STAGES: set[str] = set()


@dataclass
class ReplyAction:
    text: str
    channel: str


def _append_log(applicant: Applicant, role: str, text: str) -> None:
    try:
        log = json.loads(applicant.conversation_log or "[]")
    except json.JSONDecodeError:
        log = []
    if not isinstance(log, list):
        log = []
    log.append(
        {
            "role": role,
            "text": text,
            "at": datetime.now(timezone.utc).isoformat(),
        }
    )
    applicant.conversation_log = json.dumps(log[-80:])


def on_candidate_message(
    applicant: Applicant, message_text: str, channel: str | None = None
) -> None:
    applicant.last_candidate_message = message_text
    applicant.last_candidate_message_at = datetime.now(timezone.utc)
    _append_log(applicant, "candidate", message_text)
    if channel:
        applicant.reply_channel = channel

    if applicant.stage == S.STAGE_NEW:
        applicant.stage = S.STAGE_WAITING_REPLY_1

    if applicant.stage in (
        S.STAGE_WAITING_REPLY_1,
        S.STAGE_WAITING_REPLY_2,
        S.STAGE_WAITING_REPLY_3,
    ):
        applicant.next_action_at = schedule_after_candidate_message(message_text)
        return

    # Non-FM leftovers — do not schedule outbound.
    applicant.next_action_at = None


def build_reply(applicant: Applicant) -> ReplyAction:
    project = get_project_for_chat(applicant.freelancermap_project_id)
    if not project:
        applicant.stage = S.STAGE_FAILED
        applicant.status = "no_project"
        return ReplyAction("", S.CHANNEL_NONE)

    msg = applicant.last_candidate_message or applicant.message_preview or ""
    ctx = applicant.conversation_log or ""

    if applicant.stage == S.STAGE_WAITING_REPLY_1:
        text = generate_first_reply(
            msg, project.proj_title, project.proj_description, applicant.name
        )
        applicant.bot_reply_count = 1
        applicant.stage = S.STAGE_WAITING_REPLY_2
        applicant.next_action_at = None
        applicant.reply_channel = S.CHANNEL_FREELANCERMAP
        _append_log(applicant, "bot", text)
        return ReplyAction(text, S.CHANNEL_FREELANCERMAP)

    if applicant.stage == S.STAGE_WAITING_REPLY_2:
        text = generate_second_reply(
            msg, project.proj_title, project.proj_description, ctx
        )
        applicant.bot_reply_count = 2
        applicant.stage = S.STAGE_WAITING_REPLY_3
        applicant.next_action_at = None
        applicant.reply_channel = S.CHANNEL_FREELANCERMAP
        _append_log(applicant, "bot", text)
        return ReplyAction(text, S.CHANNEL_FREELANCERMAP)

    if applicant.stage == S.STAGE_WAITING_REPLY_3:
        applicant.bot_reply_count = 3
        applicant.stage = S.STAGE_COMPLETED
        applicant.status = "completed"
        applicant.next_action_at = None
        applicant.reply_channel = S.CHANNEL_FREELANCERMAP
        _append_log(applicant, "bot", MSG_FM_REPLY_3)
        return ReplyAction(MSG_FM_REPLY_3, S.CHANNEL_FREELANCERMAP)

    applicant.next_action_at = None
    logger.info(
        "Skipping non-FM stage for applicant=%s stage=%s",
        applicant.id,
        applicant.stage,
    )
    return ReplyAction("", S.CHANNEL_NONE)


def is_action_due(applicant: Applicant) -> bool:
    if applicant.stage in (S.STAGE_COMPLETED, S.STAGE_FAILED):
        return False
    if applicant.next_action_at is None:
        return False
    due = applicant.next_action_at
    if due.tzinfo is None:
        due = due.replace(tzinfo=timezone.utc)
    if due > datetime.now(timezone.utc):
        return False
    return True
