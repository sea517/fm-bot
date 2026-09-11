import json
import logging
import random
import re
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from src.ai.gemini import (
    generate_first_reply,
    generate_second_reply,
    generate_tech_followup,
)
from src.ai.slack_understand import (
    last_bot_question_from_log,
    recent_log_excerpt,
    resolve_availability_start,
    understand_slack_message,
)
from src.conversation import stages as S
from src.conversation.templates import (
    MSG_ASK_AVAILABILITY,
    MSG_ASK_GITHUB,
    MSG_ASK_INTRO,
    MSG_ASK_READY,
    MSG_ASK_RECENT_PROJECT,
    MSG_ASSIGNMENT_CHECK_IN,
    MSG_ASSIGNMENT_FORK_ACCESS,
    MSG_ASSIGNMENT_INVITED,
    MSG_ASSIGNMENT_RECEIVED,
    MSG_AVAILABILITY_ACK,
    MSG_AVAILABILITY_AGREE_LEADS,
    MSG_EMAIL_PROCESS,
    MSG_FINAL_REJECTION,
    MSG_FM_REPLY_3,
    MSG_NEED_GITHUB_ACCOUNT,
    MSG_SLACK_INVITED,
    MSG_TECH_QUESTIONS,
    MSG_TECH_TIME_PASSED,
    TECH_QUESTIONS_TEXT,
)
from src.conversation.timing import (
    EDT,
    agreement_slot_label,
    availability_is_later,
    format_availability_when,
    is_slack_business_hours,
    looks_like_slot_confirmation,
    next_slack_business_open,
    parse_availability_start,
    sanitize_scheduled_start,
    schedule_after_candidate_message,
    schedule_hours_later,
    schedule_slack_after_message,
    schedule_tech_answer_check,
    hours_later,
    three_days_later,
)
from src.db.models import Applicant
from src.db.projects import get_project_for_chat
from src.github.invite import candidate_submitted_assignment, extract_github_username
from src.mail.inbox import extract_candidate_slack_email
from src.slack.invite import cleanup_candidate_channel, invite_to_slack

logger = logging.getLogger(__name__)

FM_STAGES = {
    S.STAGE_WAITING_REPLY_1,
    S.STAGE_WAITING_REPLY_2,
    S.STAGE_WAITING_REPLY_3,
}
EMAIL_STAGES = {
    S.STAGE_WAITING_EMAIL_PROCESS,
    S.STAGE_WAITING_SLACK_EMAIL,
}
SLACK_STAGES = {
    S.STAGE_WAITING_JOIN,
    S.STAGE_WAITING_AVAILABILITY,
    S.STAGE_WAITING_START,
    S.STAGE_WAITING_READY,
    S.STAGE_WAITING_INTRO,
    S.STAGE_WAITING_RECENT_PROJECT,
    S.STAGE_WAITING_TECH_ANSWERS,
    S.STAGE_GEMINI_FOLLOWUP_1,
    S.STAGE_GEMINI_FOLLOWUP_2,
    S.STAGE_GEMINI_FOLLOWUP_3,
    S.STAGE_WAITING_GITHUB_ASK,
    S.STAGE_WAITING_GITHUB,
    S.STAGE_GITHUB_INVITED,
    S.STAGE_ASSIGNMENT_NOTIFIED,
    S.STAGE_ASSIGNMENT_SUBMITTED,
    S.STAGE_WAITING_FINAL_REJECTION,
    S.STAGE_WAITING_CHANNEL_CLEANUP,
}

# Assessment chat itself (after availability is agreed) — Mon–Fri 10–17 EDT only.
SLACK_ASSESSMENT_STAGES = {
    S.STAGE_WAITING_START,
    S.STAGE_WAITING_READY,
    S.STAGE_WAITING_INTRO,
    S.STAGE_WAITING_RECENT_PROJECT,
    S.STAGE_WAITING_TECH_ANSWERS,
    S.STAGE_GEMINI_FOLLOWUP_1,
    S.STAGE_GEMINI_FOLLOWUP_2,
    S.STAGE_GEMINI_FOLLOWUP_3,
    S.STAGE_WAITING_GITHUB_ASK,
    S.STAGE_WAITING_GITHUB,
}


@dataclass
class ReplyAction:
    text: str
    channel: str


def _append_log(applicant: Applicant, role: str, text: str) -> None:
    log = []
    if applicant.conversation_log:
        try:
            log = json.loads(applicant.conversation_log)
        except json.JSONDecodeError:
            log = []
    log.append({"role": role, "text": text, "at": datetime.now(timezone.utc).isoformat()})
    applicant.conversation_log = json.dumps(log)


# Phrases that mean "I'll answer later" / acknowledgment without real content.
_PLACEHOLDER_REPLY_HINTS = (
    "i will share",
    "i'll share",
    "i will send",
    "i'll send",
    "i will provide",
    "i'll provide",
    "let me share",
    "let me write",
    "give me a moment",
    "give me a sec",
    "one moment",
    "one sec",
    "coming up",
    "will do",
    "sure thing",
    "sounds good",
    "okay sure",
    "ok sure",
    "brief introduction",  # "I will share a brief introduction" — not the intro itself
)


def _looks_like_placeholder_reply(text: str) -> bool:
    """True for short acknowledgments that don't answer the question yet."""
    raw = (text or "").strip()
    if not raw:
        return True
    low = " ".join(raw.lower().split())
    if len(low) < 40 and any(
        low in (p, f"{p}.", f"{p}!")
        for p in ("sure", "ok", "okay", "yes", "yep", "yeah", "alright", "got it")
    ):
        return True
    if any(h in low for h in _PLACEHOLDER_REPLY_HINTS) and len(low) < 160:
        return True
    return False


def _looks_like_availability_reply(text: str) -> bool:
    """
    True when the candidate actually shared when they can do the assessment.

    Greetings like "Hi Oliver, I am Nick." must not advance past waiting_availability.
    """
    if _looks_like_placeholder_reply(text):
        return False
    raw = (text or "").strip()
    if not raw:
        return False
    low = " ".join(raw.lower().split())

    # Time-of-day / window patterns (avoid bare "am" matching "I am …").
    if re.search(r"\b\d{1,2}([:.]\d{2})?\s*(am|pm|a\.m\.?|p\.m\.?)\b", low):
        return True
    if re.search(r"\b\d{1,2}([:.]\d{2})?\s*[-–to]+\s*\d{1,2}", low):
        return True

    signals = (
        "available",
        "availability",
        "i am free",
        "i'm free",
        "free to",
        "free for",
        "free after",
        "free between",
        "ready to",
        "ready for",
        "i can start",
        "i can do",
        "i can join",
        "i can chat",
        "i can talk",
        "i can take",
        "works for me",
        "work for me",
        "anytime",
        "any time",
        "a.m",
        "p.m",
        "edt",
        "est",
        "cet",
        "cest",
        "timezone",
        "time zone",
        "my time",
        "this week",
        "next week",
        "after 1",
        "after 2",
        "after an hour",
        "after a hour",
        "after two",
        "in an hour",
        "in 1 hour",
        "in 2 hour",
        "within an hour",
        "within the next",
        "monday",
        "tuesday",
        "wednesday",
        "thursday",
        "friday",
        "today",
        "tomorrow",
        "hour",
        "hours",
    )
    if any(s in low for s in signals):
        return True

    # Short hello / name-only messages are never availability.
    if len(low) < 100 and any(
        low.startswith(p) or low == p.rstrip(" ,")
        for p in (
            "hi ",
            "hello ",
            "hey ",
            "hi,",
            "hello,",
            "hey,",
            "hi!",
            "hello!",
            "hey!",
            "good morning",
            "good afternoon",
            "good evening",
        )
    ):
        return False
    return False


def _parse_log(applicant: Applicant) -> list[dict]:
    if not applicant.conversation_log:
        return []
    try:
        return json.loads(applicant.conversation_log)
    except json.JSONDecodeError:
        return []


def _candidate_text_since_last_bot(applicant: Applicant) -> str:
    """
    Join all candidate bubbles after the last bot message.

    Slack candidates often split an intro across many short messages; judging
    only the latest bubble wrongly treats a finished answer as incomplete.

    Returns "" when there is nothing new after the last bot message — do not
    fall back to a stale last_candidate_message (that re-processes old text and
    can spam the same ask forever).
    """
    parts: list[str] = []
    for entry in reversed(_parse_log(applicant)):
        role = entry.get("role")
        if role == "bot":
            break
        if role == "candidate":
            text = (entry.get("text") or "").strip()
            if text:
                parts.append(text)
    if not parts:
        return ""
    return "\n".join(reversed(parts)).strip()


def _already_asked_github(applicant: Applicant) -> bool:
    if _bot_already_sent(applicant, "share your GitHub username"):
        return True
    if _bot_already_sent(applicant, MSG_ASK_GITHUB[:40]):
        return True
    if _bot_already_sent(applicant, MSG_NEED_GITHUB_ACCOUNT[:40]):
        return True
    reply = (applicant.chat_reply or "").strip()
    return "GitHub username" in reply or reply.startswith(
        MSG_NEED_GITHUB_ACCOUNT[:30]
    )


def _looks_like_no_github_account(text: str) -> bool:
    """True when they say they have no GitHub / aren't a developer for this step."""
    low = " ".join((text or "").lower().split())
    if not low:
        return False
    cues = (
        "don't have",
        "do not have",
        "dont have",
        "don't have one",
        "do not have one",
        "no github",
        "without github",
        "not a developer",
        "i am not a developer",
        "i'm not a developer",
        "not developer",
        "product manager",
        "i'm a pm",
        "i am a pm",
        "create one",
        "as a zip",
        "send the assignment as a zip",
        "no account",
        "don't have an account",
        "do not have an account",
    )
    return any(c in low for c in cues)


def _candidate_text_after_bot_ask(applicant: Applicant, ask_needle: str) -> str:
    """
    All candidate text after the first time Oliver asked `ask_needle`.

    Used when a duplicate bot question sits between a real answer and a
    follow-up like "I just answered your question."
    """
    ask_needle = (ask_needle or "").strip()
    if not ask_needle:
        return ""
    started = False
    parts: list[str] = []
    for entry in _parse_log(applicant):
        text = (entry.get("text") or "").strip()
        role = entry.get("role")
        if role == "bot" and ask_needle in text:
            started = True
            continue
        if started and role == "candidate" and text:
            parts.append(text)
    return "\n".join(parts).strip()


def _bot_already_sent(applicant: Applicant, needle: str) -> bool:
    needle = (needle or "").strip()
    if not needle:
        return False
    for entry in _parse_log(applicant):
        if entry.get("role") != "bot":
            continue
        text = entry.get("text") or ""
        if needle in text:
            return True
    return False


def _last_bot_agreement_label(applicant: Applicant) -> str | None:
    """If Oliver's last message booked a slot, return its format_availability_when label."""
    for entry in reversed(_parse_log(applicant)):
        if entry.get("role") != "bot":
            continue
        text = entry.get("text") or ""
        low = text.lower()
        if "we'll begin around" not in low and "we will begin around" not in low:
            return None
        # "… around Wednesday, September 9 at 4:00 PM EDT."
        m = re.search(
            r"begin around\s+(.+?)(?:\s+EDT|\s+EST)?\.?\s*$",
            text.strip(),
            re.I | re.S,
        )
        if not m:
            return None
        return " ".join(m.group(1).split())
    return None


def _already_agreed_same_slot(applicant: Applicant, start_at: datetime) -> bool:
    """True when we already sent 'we'll begin around {same slot}'."""
    label = agreement_slot_label(start_at)
    last = _last_bot_agreement_label(applicant)
    if last and last.lower() == label.lower():
        return True
    # Also catch duplicates that only differ by wrong historical weekday/year text
    # by comparing month/day/hour from the sanitized slot.
    local = sanitize_scheduled_start(start_at).astimezone(EDT)
    needle = local.strftime("%B %-d at %-I:%M %p").replace("  ", " ")
    for entry in reversed(_parse_log(applicant)):
        if entry.get("role") != "bot":
            continue
        text = entry.get("text") or ""
        if "we'll begin around" in text.lower() and needle.lower() in text.lower():
            return True
        break
    return False


def _book_availability_slot(applicant: Applicant, start_at: datetime) -> ReplyAction | None:
    """
    Send (or skip) an availability agreement for start_at.
    Returns None when the same slot was already agreed — caller should stay quiet.
    """
    start_at = sanitize_scheduled_start(start_at)
    if _already_agreed_same_slot(applicant, start_at):
        applicant.stage = S.STAGE_WAITING_START
        applicant.next_action_at = start_at
        logger.info(
            "Applicant=%s already agreed slot %s — not re-sending",
            applicant.id,
            start_at.isoformat(),
        )
        return None
    text = _availability_agreement(start_at)
    applicant.stage = S.STAGE_WAITING_START
    applicant.next_action_at = start_at
    _append_log(applicant, "bot", text)
    return ReplyAction(text, S.CHANNEL_SLACK)


def _understand_slack(applicant: Applicant, answer: str):
    """DeepSeek/OpenRouter reads the Slack message before we decide the next act."""
    understood = understand_slack_message(
        stage=applicant.stage or "",
        bot_question=last_bot_question_from_log(applicant.conversation_log),
        candidate_message=answer,
        recent_log=recent_log_excerpt(applicant.conversation_log),
    )
    # Shown in confirm mode so you can see what DeepSeek recognized.
    applicant.ai_read_summary = (  # type: ignore[attr-defined]
        f"intent={understood.intent} answers={understood.answers_current_question}"
        f" — {understood.summary}"
    )
    return understood


def _classifier_failed(understood) -> bool:
    return (understood.summary or "") == "(classifier failed)"


def _deepseek_ready_now(understood, text: str = "") -> bool:
    """True when DeepSeek says they want to start now (not a later reschedule)."""
    if _classifier_failed(understood):
        return _looks_like_ready_yes(text)
    if understood.intent == "ready" and understood.answers_current_question:
        return True
    if understood.intent == "availability" and understood.availability_when == "now":
        return True
    if understood.intent == "ready":
        return True
    return False


def _deepseek_later_slot(understood, text: str) -> tuple[datetime | None, bool]:
    """
    Return (start_at, is_later) preferring DeepSeek.

    Heuristic parse only if the classifier failed.
    Confirmations never count as a new later slot.
    """
    if looks_like_slot_confirmation(text):
        return None, False
    if _classifier_failed(understood):
        start = parse_availability_start(text)
        return start, availability_is_later(start)
    start_at, is_later = resolve_availability_start(understood, text)
    if start_at is not None:
        start_at = sanitize_scheduled_start(start_at)
    if start_at is not None and not is_later and availability_is_later(start_at):
        is_later = True
    if understood.intent == "availability" and understood.availability_when == "later":
        is_later = True
    if understood.intent in ("ready", "already_answered"):
        is_later = False
    if looks_like_slot_confirmation(text):
        is_later = False
        start_at = None
    return start_at, is_later


def _tech_questions_asked_at(applicant: Applicant) -> datetime | None:
    """When Oliver last posted the take-home tech questions, if known."""
    needle = "Please answer within 15 mins"
    for entry in reversed(_parse_log(applicant)):
        if entry.get("role") != "bot":
            continue
        text = entry.get("text") or ""
        if needle not in text and MSG_TECH_QUESTIONS[:40] not in text:
            continue
        raw = entry.get("at")
        if not raw:
            return None
        try:
            dt = datetime.fromisoformat(str(raw).replace("Z", "+00:00"))
        except ValueError:
            return None
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt
    return None


def _looks_like_tech_answer(text: str) -> bool:
    """True when the candidate is answering the take-home tech questions."""
    if _looks_like_placeholder_reply(text):
        return False
    raw = (text or "").strip()
    if len(raw) < 120:
        return False
    low = raw.lower()
    signals = (
        "rls",
        "service-role",
        "service role",
        "middleware",
        "tenant",
        "permission",
        "sb-",
        "auth cookie",
        "in-memory",
        "cache",
        "revoke",
        "staleness",
        "fail closed",
        "fail open",
        "webhook",
    )
    hits = sum(1 for s in signals if s in low)
    return hits >= 2 or (hits >= 1 and len(raw) >= 400)


def _tech_answer_text(applicant: Applicant) -> str:
    """
    Best available tech-question answer from the log.

    Prefers text after the tech ask; falls back to recent candidate bubbles that
    look like tech answers (covers DB/log drift when Slack already moved on).
    """
    after = _candidate_text_after_bot_ask(applicant, "Please answer within 15 mins")
    # Drop pure placeholders like a lone "Sure".
    chunks = [c.strip() for c in after.split("\n") if c.strip()]
    real = [c for c in chunks if not _looks_like_placeholder_reply(c)]
    joined = "\n".join(real).strip()
    if _looks_like_tech_answer(joined):
        return joined

    # Fallback: last few candidate messages that look technical.
    parts: list[str] = []
    for entry in reversed(_parse_log(applicant)):
        if entry.get("role") == "bot":
            # Stop at a bot message unless it's only the time-passed nudge.
            text = entry.get("text") or ""
            if MSG_TECH_TIME_PASSED in text:
                continue
            break
        if entry.get("role") == "candidate":
            text = (entry.get("text") or "").strip()
            if text and not _looks_like_placeholder_reply(text):
                parts.append(text)
        if len(parts) >= 6:
            break
    joined = "\n".join(reversed(parts)).strip()
    if _looks_like_tech_answer(joined):
        return joined
    return joined if len(joined) >= 200 else ""


def _reply_tech_questions(applicant: Applicant) -> ReplyAction:
    """Post the tech questions once, then schedule the 15-minute answer check."""
    project = get_project_for_chat(applicant.freelancermap_project_id)

    def _advance_with_existing_answer(answer: str) -> ReplyAction:
        applicant.stage = S.STAGE_GEMINI_FOLLOWUP_1
        applicant.gemini_followup_count = 1
        applicant.next_action_at = None
        text = generate_tech_followup(
            TECH_QUESTIONS_TEXT,
            answer,
            1,
            project.proj_title if project else "",
            project.proj_description if project else "",
        )
        _append_log(applicant, "bot", text)
        return ReplyAction(text, S.CHANNEL_SLACK)

    # Already asked on Slack / in log — never re-send the same block.
    if _bot_already_sent(applicant, "Please answer within 15 mins"):
        existing = _tech_answer_text(applicant)
        if existing and _looks_like_tech_answer(existing):
            logger.info(
                "Applicant=%s tech questions already asked and answered — follow-up",
                applicant.id,
            )
            return _advance_with_existing_answer(existing)
        applicant.stage = S.STAGE_WAITING_TECH_ANSWERS
        asked_at = _tech_questions_asked_at(applicant)
        applicant.next_action_at = schedule_tech_answer_check(asked_at)
        logger.info(
            "Applicant=%s tech questions already asked — not re-sending, check at %s",
            applicant.id,
            applicant.next_action_at,
        )
        return ReplyAction("", S.CHANNEL_NONE)

    # Candidate already pasted tech answers while stage was stuck / log drifted.
    existing = _tech_answer_text(applicant)
    if existing and _looks_like_tech_answer(existing):
        if not _bot_already_sent(applicant, "Please answer within 15 mins"):
            _append_log(applicant, "bot", MSG_TECH_QUESTIONS)
        if not _bot_already_sent(applicant, MSG_TECH_TIME_PASSED):
            _append_log(applicant, "bot", MSG_TECH_TIME_PASSED)
        logger.info(
            "Applicant=%s already has tech answers — skipping re-ask, follow-up next",
            applicant.id,
        )
        return _advance_with_existing_answer(existing)

    applicant.stage = S.STAGE_WAITING_TECH_ANSWERS
    applicant.next_action_at = schedule_tech_answer_check()
    _append_log(applicant, "bot", MSG_TECH_QUESTIONS)
    logger.info(
        "Applicant=%s tech questions sent — checking answers at %s",
        applicant.id,
        applicant.next_action_at.isoformat() if applicant.next_action_at else "?",
    )
    return ReplyAction(MSG_TECH_QUESTIONS, S.CHANNEL_SLACK)


def _looks_like_substantive_intro(text: str) -> bool:
    """
    Candidate actually shared background — not only a promise to share later.

    Requires intro-ish signals (location / role / experience). A long project
    write-up alone must NOT count as an intro — that caused re-asking the
    recent-project question after Omer answered it.
    """
    if _looks_like_placeholder_reply(text):
        return False
    raw = (text or "").strip()
    low = raw.lower()
    if len(raw) < 60:
        return False
    signals = (
        "location",
        "based in",
        "located",
        "i'm from",
        "i am from",
        "live in",
        "living in",
        "years of experience",
        "years experience",
        "my experience",
        "i am a ",
        "i'm a ",
        "i work as",
        "working as",
        "work as",
        "my background",
        "my name",
        "fullstack",
        "full-stack",
        "full stack",
        "frontend",
        "front-end",
        "backend",
        "back-end",
        "engineer",
        "developer",
        "stacks:",
        "stack:",
    )
    return any(s in low for s in signals)


def _looks_like_substantive_project(text: str) -> bool:
    """Candidate described a real project, not only 'I'll share soon'."""
    if _looks_like_placeholder_reply(text):
        return False
    raw = (text or "").strip()
    if len(raw) < 80:
        return False
    low = raw.lower()
    signals = (
        "project",
        "built",
        "implemented",
        "developed",
        "worked on",
        "i worked",
        "we built",
        "system",
        "api",
        "app",
        "platform",
        "feature",
        "migrat",
        "integrat",
        "marketplace",
        "test suite",
        "payment",
    )
    if any(s in low for s in signals):
        return True
    return len(raw) >= 200


def on_candidate_message(applicant: Applicant, message_text: str, channel: str | None = None) -> None:
    applicant.last_candidate_message = message_text
    applicant.last_candidate_message_at = datetime.now(timezone.utc)
    _append_log(applicant, "candidate", message_text)
    if channel:
        applicant.reply_channel = channel

    if applicant.stage == S.STAGE_NEW:
        applicant.stage = S.STAGE_WAITING_REPLY_1

    # freelancermap: 5 / 10 minutes
    if applicant.stage in (
        S.STAGE_WAITING_REPLY_1,
        S.STAGE_WAITING_REPLY_2,
        S.STAGE_WAITING_REPLY_3,
    ):
        applicant.next_action_at = schedule_after_candidate_message(message_text)
        return

    if applicant.stage == S.STAGE_WAITING_SLACK_EMAIL:
        applicant.next_action_at = schedule_after_candidate_message(message_text)
        return

    # Slack: availability ask can fire anytime; assessment steps only inside
    # Mon–Fri 10:00–17:00 America/New_York.
    if applicant.stage in (S.STAGE_WAITING_JOIN, S.STAGE_WAITING_AVAILABILITY):
        applicant.next_action_at = schedule_slack_after_message(
            message_text, require_business_hours=False
        )
        return

    if applicant.stage == S.STAGE_WAITING_START:
        # DeepSeek decides: early ready vs reschedule vs small talk.
        fresh = _freshest_candidate_text(message_text)
        if looks_like_slot_confirmation(fresh or message_text):
            # Confirming the booked slot — stay quiet until next_action_at.
            logger.info(
                "Applicant=%s confirmed booked slot — keeping next_action_at",
                applicant.id,
            )
            return
        understood = _understand_slack(applicant, fresh or message_text)
        start_at, is_later = _deepseek_later_slot(understood, fresh or message_text)
        if is_later and start_at is not None and not _deepseek_ready_now(
            understood, fresh or message_text
        ):
            # Fire soon so build_reply can agree and book the new slot.
            applicant.next_action_at = schedule_slack_after_message(
                message_text, require_business_hours=False
            )
            logger.info(
                "Applicant=%s DeepSeek reschedule while waiting_start → %s (ack soon)",
                applicant.id,
                start_at.isoformat(),
            )
            return
        if _deepseek_ready_now(understood, fresh or message_text):
            applicant.next_action_at = schedule_slack_after_message(
                message_text, require_business_hours=True
            )
            logger.info(
                "Applicant=%s DeepSeek ready before booked slot — pulling start forward",
                applicant.id,
            )
        return

    if applicant.stage == S.STAGE_WAITING_READY:
        # They answered the ready check — reply after a short Slack delay.
        applicant.next_action_at = schedule_slack_after_message(
            message_text, require_business_hours=True
        )
        return

    if applicant.stage == S.STAGE_WAITING_TECH_ANSWERS:
        # During the 15-minute window, keep the scheduled check.
        # After the window (or after "time was passed"), process late answers soon.
        asked_at = _tech_questions_asked_at(applicant)
        now = datetime.now(timezone.utc)
        window_over = False
        if asked_at is not None:
            window_over = now >= schedule_tech_answer_check(asked_at)
        if window_over or _bot_already_sent(applicant, MSG_TECH_TIME_PASSED):
            understood = _understand_slack(applicant, message_text)
            is_tech = (
                understood.intent == "tech_answer"
                and not _classifier_failed(understood)
            ) or _looks_like_tech_answer(message_text) or _looks_like_tech_answer(
                _tech_answer_text(applicant)
            )
            if is_tech:
                applicant.next_action_at = now
            else:
                applicant.next_action_at = schedule_slack_after_message(
                    message_text, require_business_hours=True
                )
            return
        if asked_at is None:
            if applicant.next_action_at is None:
                applicant.next_action_at = schedule_tech_answer_check()
            return
        check_at = schedule_tech_answer_check(asked_at)
        due = applicant.next_action_at
        if due is not None and due.tzinfo is None:
            due = due.replace(tzinfo=timezone.utc)
        if due is None or due < check_at:
            applicant.next_action_at = check_at
        return

    if applicant.stage in SLACK_ASSESSMENT_STAGES:
        applicant.next_action_at = schedule_slack_after_message(
            message_text, require_business_hours=True
        )
        return

    if applicant.stage == S.STAGE_ASSIGNMENT_NOTIFIED:
        # Only treat a real fork/PR submission URL as done — not "I'll complete it".
        if _looks_like_assignment_done(message_text):
            applicant.stage = S.STAGE_ASSIGNMENT_SUBMITTED
            applicant.assignment_submitted_at = datetime.now(timezone.utc)
            applicant.next_action_at = datetime.now(timezone.utc)
            return
        # They proposed when they'll do the assignment — agree and book that slot.
        fresh = _freshest_candidate_text(message_text)
        understood = _understand_slack(applicant, fresh or message_text)
        start_at, is_later = _resolve_assignment_slot(understood, fresh or message_text)
        if is_later and start_at is not None:
            applicant.next_action_at = schedule_slack_after_message(
                message_text, require_business_hours=False
            )
            logger.info(
                "Applicant=%s proposed assignment slot %s — will agree soon",
                applicant.id,
                start_at.isoformat(),
            )
            return
        # Asking for an email to unlock the private repo — clarify fork is enough.
        if _looks_like_private_repo_access_ask(fresh or message_text):
            applicant.next_action_at = schedule_slack_after_message(
                message_text, require_business_hours=False
            )
            logger.info(
                "Applicant=%s asked for private-repo access email — will clarify fork",
                applicant.id,
            )
            return
        # Keep the 3-hour check; don't pull it forward on acknowledgments.
        return


# Future-tense / invite acks must not count as "assignment submitted".
_ASSIGNMENT_ACK_NOT_DONE = (
    "will check",
    "will make sure",
    "will complete",
    "will carefully",
    "i've received",
    "i have received",
    "received the assignment",
    "look forward",
    "looking forward",
    "in about three hours",
    "three hours later",
    "3 hours later",
)

# Require owner/repo (fork) or a PR path — not a bare profile link.
_GITHUB_SUBMISSION_URL = re.compile(
    r"github\.com/([A-Za-z0-9-]+)/([A-Za-z0-9._-]+)",
    re.I,
)


def _looks_like_assignment_done(text: str) -> bool:
    """
    True only when the candidate shared a completed-work link (fork/PR URL).

    "I've received the assignment and will complete it on time" is NOT done.
    """
    raw = (text or "").strip()
    if not raw:
        return False
    lower = raw.lower()

    m = _GITHUB_SUBMISSION_URL.search(raw)
    if not m:
        return False

    owner, repo = m.group(1), m.group(2)
    repo_l = repo.lower()
    # Ignore non-repo path segments if the URL is weird; allow /pull/N via text.
    if repo_l in {"pull", "pulls", "issues", "settings", "blob", "tree", "commit", "commits"}:
        return "/pull" in lower or "/pulls" in lower

    # Bare acknowledgments that happen to include an unrelated github link still
    # need to look like a submission, not "I received the invite".
    if any(p in lower for p in _ASSIGNMENT_ACK_NOT_DONE):
        # Exception: ack language PLUS an explicit completed/submitted claim + URL.
        if not any(
            p in lower
            for p in (
                "i've completed",
                "i have completed",
                "completed the assignment",
                "submitted",
                "here is the",
                "here's the",
                "fork:",
                "my fork",
                "please check",
            )
        ):
            return False

    return True


def _assignment_done_in_slack(applicant: Applicant) -> bool:
    if _looks_like_assignment_done(applicant.last_candidate_message or ""):
        return True
    if not applicant.conversation_log:
        return False
    try:
        log = json.loads(applicant.conversation_log)
    except json.JSONDecodeError:
        return False
    # Only count candidate messages after we invited them to GitHub.
    seen_invite = False
    for entry in log:
        text = entry.get("text") or ""
        role = entry.get("role")
        if role == "bot" and MSG_ASSIGNMENT_INVITED[:40] in text:
            seen_invite = True
            continue
        if seen_invite and role == "candidate" and _looks_like_assignment_done(text):
            return True
    return False


def _assignment_is_complete(applicant: Applicant) -> bool:
    # Prefer hard evidence: fork/PR URL in Slack, or GitHub API activity.
    if _assignment_done_in_slack(applicant):
        return True
    if applicant.github_username and candidate_submitted_assignment(applicant.github_username):
        return True
    return False


def on_resume_email(applicant: Applicant, message_text: str, from_email: str, message_id: str) -> None:
    applicant.email = from_email
    applicant.email_message_id = message_id
    applicant.last_candidate_message = message_text
    applicant.last_candidate_message_at = datetime.now(timezone.utc)
    applicant.reply_channel = S.CHANNEL_EMAIL
    _append_log(applicant, "candidate", message_text)
    if applicant.stage in (S.STAGE_WAITING_EMAIL_RESUME, S.STAGE_WAITING_REPLY_3):
        applicant.stage = S.STAGE_WAITING_EMAIL_PROCESS
        applicant.next_action_at = schedule_hours_later(3, 4)
        logger.info("Resume received from %s — process email in 3–4 hours", from_email)


def on_slack_email_reply(applicant: Applicant, message_text: str, from_email: str) -> None:
    applicant.last_candidate_message = message_text
    applicant.last_candidate_message_at = datetime.now(timezone.utc)
    applicant.reply_channel = S.CHANNEL_EMAIL
    _append_log(applicant, "candidate", message_text)
    slack_email = extract_candidate_slack_email(message_text, from_email)
    if slack_email:
        applicant.slack_invite_email = slack_email
    applicant.next_action_at = schedule_after_candidate_message(message_text)


def _looks_like_ready_yes(text: str) -> bool:
    low = " ".join((text or "").lower().split())
    if not low:
        return False
    if any(
        p in low
        for p in (
            "not ready",
            "not yet",
            "can't",
            "cannot",
            "busy",
            "later",
            "reschedule",
            "another time",
            "in an hour",
            "in a few",
            "give me",
            "need more time",
            "going offline",
            "offline for",
            "offline today",
            "tomorrow",
            "next week",
            "rather than waiting",
            "if that time works",
            "does that work",
            "let me know if",
        )
    ):
        return False
    # Explicit day/date proposals are reschedules, not "ready now".
    if re.search(
        r"\b(monday|tuesday|wednesday|thursday|friday|saturday|sunday|"
        r"september|october|november|december|january|february|march|april|"
        r"may|june|july|august)\b",
        low,
    ) and re.search(r"\b(\d{1,2}:\d{2}|\d{1,2}\s*(am|pm)|edt|est)\b", low):
        return False
    yes_hints = (
        "ready",
        "yes",
        "yeah",
        "yep",
        "sure",
        "ok",
        "okay",
        "let's go",
        "lets go",
        "lets start",
        "let's start",
        "i'm here",
        "im here",
        "available now",
        "available from now",
        "good to go",
        "let's begin",
        "lets begin",
        "we can start",
        "we can begin",
        "good to start",
    )
    return any(h in low for h in yes_hints)


def _freshest_candidate_text(text: str) -> str:
    """
    Prefer the latest bubble when Slack ingest joined several messages.

    Schedule updates usually arrive last; older "available from now" lines must
    not override a clear tomorrow/offline reschedule.
    """
    raw = (text or "").strip()
    if not raw:
        return ""
    parts = [p.strip() for p in re.split(r"\n{2,}", raw) if p.strip()]
    if len(parts) >= 2:
        return parts[-1]
    lines = [ln.strip() for ln in raw.splitlines() if ln.strip()]
    if len(lines) >= 3:
        # Last 4 lines often carry the update after older availability chatter.
        return "\n".join(lines[-4:])
    return raw


def _looks_like_assignment_deferral(text: str) -> bool:
    """True when they are pushing the take-home to a later time / not starting now."""
    low = " ".join((text or "").lower().split())
    if not low:
        return False
    cues = (
        "not available",
        "i'm not available",
        "i am not available",
        "unavailable",
        "tomorrow",
        "will start",
        "i will start",
        "i'll start",
        "didn't accept",
        "did not accept",
        "not accept",
        "for now",
        "later",
        "next steps",
        "wednesday",
        "thursday",
        "friday",
        "monday",
        "tuesday",
    )
    return any(c in low for c in cues)


def _looks_like_private_repo_access_ask(text: str) -> bool:
    """
    Candidate asks for an email/account to grant access because the assignment
    repo looks private — they should fork instead.
    """
    low = " ".join((text or "").lower().split())
    if not low:
        return False
    mentions_email = "email" in low or "e-mail" in low
    wants_access = any(
        p in low
        for p in (
            "give access",
            "grant access",
            "share access",
            "add access",
            "access to",
            "collaborator",
            "invite",
            "permission",
        )
    )
    repo_context = any(
        p in low
        for p in (
            "private",
            "repo",
            "repository",
            "github",
            "assignment",
        )
    )
    if mentions_email and wants_access and repo_context:
        return True
    # Common short form without the word "access".
    if mentions_email and "private" in low and ("repo" in low or "repository" in low or "github" in low):
        return True
    return False


def _assignment_check_after_clarification(applicant: Applicant) -> datetime:
    """Keep the original ~3h assignment check after answering a clarifying Q."""
    invited = applicant.github_invited_at
    if invited is not None:
        if invited.tzinfo is None:
            invited = invited.replace(tzinfo=timezone.utc)
        check_at = invited + timedelta(hours=3)
        if check_at > datetime.now(timezone.utc):
            return check_at
    return hours_later(3)


def _resolve_assignment_slot(understood, text: str) -> tuple[datetime | None, bool]:
    """
    Prefer DeepSeek, then local parse. Used so OpenRouter reasoning failures
    still agree on an explicit tomorrow/Wednesday slot.
    """
    start_at, is_later = _deepseek_later_slot(understood, text)
    if is_later and start_at is not None:
        return start_at, True
    parsed = parse_availability_start(text)
    if parsed is not None and availability_is_later(parsed):
        return parsed, True
    if _looks_like_assignment_deferral(text):
        parsed = parse_availability_start(text) or next_slack_business_open()
        return parsed, True
    return start_at, is_later


def _availability_agreement(start_at: datetime) -> str:
    """Short agreement that confirms the booked slot."""
    start_at = sanitize_scheduled_start(start_at)
    lead = random.choice(MSG_AVAILABILITY_AGREE_LEADS)
    when = format_availability_when(start_at)
    # format_availability_when is already America/New_York local clock.
    return f"{lead} — we'll begin around {when} EDT."


def build_reply(applicant: Applicant) -> ReplyAction:
    project = get_project_for_chat(applicant.freelancermap_project_id)
    if not project:
        applicant.stage = S.STAGE_FAILED
        applicant.status = "no_project"
        return ReplyAction("", S.CHANNEL_NONE)

    msg = applicant.last_candidate_message or applicant.message_preview or ""
    ctx = applicant.conversation_log or ""

    if applicant.stage == S.STAGE_WAITING_FINAL_REJECTION:
        # After the rejection message is sent, wait 48h then remove them and
        # delete/archive the private channel.
        applicant.stage = S.STAGE_WAITING_CHANNEL_CLEANUP
        applicant.next_action_at = hours_later(48)
        _append_log(applicant, "bot", MSG_FINAL_REJECTION)
        logger.info(
            "Rejection sent for applicant=%s — channel cleanup in 48 hours",
            applicant.id,
        )
        return ReplyAction(MSG_FINAL_REJECTION, S.CHANNEL_SLACK)

    if applicant.stage == S.STAGE_WAITING_CHANNEL_CLEANUP:
        channel_id = applicant.slack_channel_id
        if channel_id:
            ok = cleanup_candidate_channel(channel_id)
            if not ok:
                # Retry later without advancing — keep the channel id.
                applicant.next_action_at = hours_later(1)
                logger.warning(
                    "Slack channel cleanup failed for applicant=%s channel=%s — retry in 1h",
                    applicant.id,
                    channel_id,
                )
                return ReplyAction("", S.CHANNEL_NONE)
        applicant.stage = S.STAGE_COMPLETED
        applicant.status = "completed"
        applicant.next_action_at = None
        applicant.slack_channel_id = None
        logger.info("Applicant=%s completed — Slack channel cleaned up", applicant.id)
        return ReplyAction("", S.CHANNEL_NONE)

    if applicant.stage == S.STAGE_ASSIGNMENT_NOTIFIED:
        # 3 hours after invite (or earlier if they said "done" and we advanced).
        if _assignment_is_complete(applicant):
            applicant.stage = S.STAGE_WAITING_FINAL_REJECTION
            if applicant.assignment_submitted_at is None:
                applicant.assignment_submitted_at = datetime.now(timezone.utc)
            applicant.next_action_at = three_days_later()
            _append_log(applicant, "bot", MSG_ASSIGNMENT_RECEIVED)
            logger.info(
                "Assignment complete for applicant=%s github=%s — ack + 3-day review",
                applicant.id,
                applicant.github_username,
            )
            return ReplyAction(MSG_ASSIGNMENT_RECEIVED, S.CHANNEL_SLACK)

        last_bot = last_bot_question_from_log(applicant.conversation_log)
        already_agreed = "we'll begin around" in (last_bot or "").lower()

        # Prefer agreeing a proposed assignment window over a premature check-in.
        # Availability often lands in the same burst as the GitHub username and is
        # logged *before* the invite bot line — still honor it once.
        answer = _candidate_text_since_last_bot(applicant).strip()
        if not answer and not already_agreed:
            if MSG_ASSIGNMENT_INVITED[:40] in (last_bot or "") or MSG_ASSIGNMENT_INVITED[
                :40
            ] in (applicant.chat_reply or ""):
                answer = (applicant.last_candidate_message or "").strip()
        if not answer and not already_agreed:
            answer = (applicant.last_candidate_message or "").strip()

        if answer and not already_agreed:
            understood = _understand_slack(applicant, answer)
            start_at, is_later = _resolve_assignment_slot(understood, answer)
            if is_later and start_at is not None:
                text = _availability_agreement(start_at)
                # Follow up 3h after their planned start, not from invite send.
                applicant.next_action_at = start_at + timedelta(hours=3)
                _append_log(applicant, "bot", text)
                logger.info(
                    "Applicant=%s assignment window agreed for %s — check at %s",
                    applicant.id,
                    start_at.isoformat(),
                    applicant.next_action_at.isoformat(),
                )
                return ReplyAction(text, S.CHANNEL_SLACK)
            # Saying "not now / tomorrow" without a parseable clock still must
            # NOT fall through to "have you completed the assignment?".
            if _looks_like_assignment_deferral(answer):
                start_at = parse_availability_start(answer) or next_slack_business_open()
                text = _availability_agreement(start_at)
                applicant.next_action_at = start_at + timedelta(hours=3)
                _append_log(applicant, "bot", text)
                logger.info(
                    "Applicant=%s deferred assignment — agreed %s (heuristic)",
                    applicant.id,
                    start_at.isoformat(),
                )
                return ReplyAction(text, S.CHANNEL_SLACK)

        # "Need an email to grant access — repo is private" → fork is enough.
        if answer and _looks_like_private_repo_access_ask(answer):
            if _bot_already_sent(applicant, MSG_ASSIGNMENT_FORK_ACCESS[:40]):
                applicant.next_action_at = _assignment_check_after_clarification(applicant)
                return ReplyAction("", S.CHANNEL_NONE)
            text = MSG_ASSIGNMENT_FORK_ACCESS
            applicant.next_action_at = _assignment_check_after_clarification(applicant)
            _append_log(applicant, "bot", text)
            logger.info(
                "Applicant=%s private-repo access ask — clarified fork is enough",
                applicant.id,
            )
            return ReplyAction(text, S.CHANNEL_SLACK)

        # Timed check-in only — never as a fallback for misunderstood messages.
        if _bot_already_sent(applicant, MSG_ASSIGNMENT_CHECK_IN[:40]) and not answer:
            applicant.next_action_at = None
            return ReplyAction("", S.CHANNEL_NONE)

        applicant.next_action_at = None
        _append_log(applicant, "bot", MSG_ASSIGNMENT_CHECK_IN)
        logger.info(
            "Assignment not complete yet for applicant=%s github=%s — check-in",
            applicant.id,
            applicant.github_username,
        )
        return ReplyAction(MSG_ASSIGNMENT_CHECK_IN, S.CHANNEL_SLACK)

    if applicant.stage == S.STAGE_ASSIGNMENT_SUBMITTED and applicant.next_action_at:
        # Guard against false-positive stage advances (ack without a fork URL).
        if not _assignment_is_complete(applicant):
            logger.info(
                "Applicant=%s marked submitted without fork/PR evidence — reverting",
                applicant.id,
            )
            applicant.stage = S.STAGE_ASSIGNMENT_NOTIFIED
            applicant.assignment_submitted_at = None
            applicant.next_action_at = None
            return ReplyAction("", S.CHANNEL_NONE)
        applicant.stage = S.STAGE_WAITING_FINAL_REJECTION
        applicant.next_action_at = three_days_later()
        _append_log(applicant, "bot", MSG_ASSIGNMENT_RECEIVED)
        return ReplyAction(MSG_ASSIGNMENT_RECEIVED, S.CHANNEL_SLACK)

    if applicant.stage == S.STAGE_WAITING_REPLY_1:
        text = generate_first_reply(msg, project.proj_title, project.proj_description, applicant.name)
        applicant.bot_reply_count = 1
        applicant.stage = S.STAGE_WAITING_REPLY_2
        applicant.next_action_at = None
        applicant.reply_channel = S.CHANNEL_FREELANCERMAP
        _append_log(applicant, "bot", text)
        return ReplyAction(text, S.CHANNEL_FREELANCERMAP)

    if applicant.stage == S.STAGE_WAITING_REPLY_2:
        text = generate_second_reply(msg, project.proj_title, project.proj_description, ctx)
        applicant.bot_reply_count = 2
        applicant.stage = S.STAGE_WAITING_REPLY_3
        applicant.next_action_at = None
        _append_log(applicant, "bot", text)
        return ReplyAction(text, S.CHANNEL_FREELANCERMAP)

    if applicant.stage == S.STAGE_WAITING_REPLY_3:
        applicant.bot_reply_count = 3
        applicant.stage = S.STAGE_WAITING_EMAIL_RESUME
        applicant.next_action_at = None
        applicant.reply_channel = S.CHANNEL_EMAIL
        _append_log(applicant, "bot", MSG_FM_REPLY_3)
        return ReplyAction(MSG_FM_REPLY_3, S.CHANNEL_FREELANCERMAP)

    if applicant.stage == S.STAGE_WAITING_EMAIL_PROCESS:
        applicant.stage = S.STAGE_WAITING_SLACK_EMAIL
        applicant.next_action_at = None
        _append_log(applicant, "bot", MSG_EMAIL_PROCESS)
        return ReplyAction(MSG_EMAIL_PROCESS, S.CHANNEL_EMAIL)

    if applicant.stage == S.STAGE_WAITING_SLACK_EMAIL:
        # No AI here: use an address from their reply, else the address they
        # replied from.
        slack_email = (
            applicant.slack_invite_email
            or extract_candidate_slack_email(msg, applicant.email or "")
            or applicant.email
        )
        if not slack_email:
            text = (
                "Thanks for your reply. Please send the email address you would like us "
                "to use for the Slack invitation."
            )
            _append_log(applicant, "bot", text)
            applicant.next_action_at = None
            return ReplyAction(text, S.CHANNEL_EMAIL)
        applicant.slack_invite_email = slack_email
        logger.info(
            "Slack invite address for applicant=%s: %s", applicant.id, slack_email
        )
        result = invite_to_slack(slack_email, applicant.name)
        if result.channel_id:
            applicant.slack_channel_id = result.channel_id
        if result.added_to_channel:
            applicant.slack_invited_at = datetime.now(timezone.utc)
        applicant.stage = S.STAGE_WAITING_JOIN
        applicant.next_action_at = None
        applicant.reply_channel = S.CHANNEL_SLACK
        _append_log(applicant, "bot", MSG_SLACK_INVITED)
        return ReplyAction(MSG_SLACK_INVITED, S.CHANNEL_EMAIL)

    if applicant.stage == S.STAGE_WAITING_JOIN:
        applicant.stage = S.STAGE_WAITING_AVAILABILITY
        applicant.next_action_at = None
        _append_log(applicant, "bot", MSG_ASK_AVAILABILITY)
        return ReplyAction(MSG_ASK_AVAILABILITY, S.CHANNEL_SLACK)

    if applicant.stage == S.STAGE_WAITING_AVAILABILITY:
        answer = _candidate_text_since_last_bot(applicant) or msg
        understood = _understand_slack(applicant, answer)
        if _classifier_failed(understood):
            answered = _looks_like_availability_reply(answer)
        elif understood.intent in ("greeting", "placeholder", "already_answered"):
            answered = False
        elif understood.intent in ("availability", "ready"):
            # "I'm available now / ready to start" answers the availability ask.
            answered = True
        elif understood.availability_when in ("now", "later"):
            answered = True
        else:
            answered = understood.answers_current_question and understood.intent in (
                "availability",
                "ready",
                "other",
            )
        if not answered and _looks_like_availability_reply(answer):
            answered = True

        if not answered:
            logger.info(
                "Applicant=%s availability not answered yet (ai=%s) — waiting",
                applicant.id,
                understood.intent,
            )
            applicant.next_action_at = None
            return ReplyAction("", S.CHANNEL_NONE)

        start_at, is_later = resolve_availability_start(understood, answer)

        # Later slot (e.g. "Monday 10–3") — agree now, even outside business hours,
        # and open the intro when that slot arrives.
        if is_later and start_at is not None:
            booked = _book_availability_slot(applicant, start_at)
            if booked is None:
                return ReplyAction("", S.CHANNEL_NONE)
            logger.info(
                "Applicant=%s availability scheduled for %s — ack=%r, deferring intro",
                applicant.id,
                applicant.next_action_at.isoformat() if applicant.next_action_at else "?",
                booked.text,
            )
            return booked

        # Available now / soon — only start the intro inside Mon–Fri 10–17 EDT.
        if not is_slack_business_hours():
            text = random.choice(MSG_AVAILABILITY_AGREE_LEADS) + "."
            applicant.stage = S.STAGE_WAITING_START
            applicant.next_action_at = next_slack_business_open()
            _append_log(applicant, "bot", text)
            logger.info(
                "Applicant=%s available now but outside hours — agreed, intro at %s",
                applicant.id,
                applicant.next_action_at.isoformat() if applicant.next_action_at else "?",
            )
            return ReplyAction(text, S.CHANNEL_SLACK)

        applicant.stage = S.STAGE_WAITING_INTRO
        applicant.next_action_at = None
        text = f"{MSG_AVAILABILITY_ACK}\n\n{MSG_ASK_INTRO}"
        _append_log(applicant, "bot", text)
        return ReplyAction(text, S.CHANNEL_SLACK)

    if applicant.stage == S.STAGE_WAITING_START:
        # Booked slot arrived (or they said ready early / proposed a new time).
        if not is_slack_business_hours():
            applicant.next_action_at = next_slack_business_open()
            return ReplyAction("", S.CHANNEL_NONE)
        answer = _candidate_text_since_last_bot(applicant) or msg
        fresh = _freshest_candidate_text(answer)

        # Confirming the existing booking — do not re-send "we'll begin around…".
        if looks_like_slot_confirmation(fresh or answer):
            # If the booked time has arrived, continue into ready/intro below.
            due = applicant.next_action_at
            now = datetime.now(timezone.utc)
            if due is not None:
                if due.tzinfo is None:
                    due = due.replace(tzinfo=timezone.utc)
                if due > now + timedelta(minutes=2):
                    logger.info(
                        "Applicant=%s confirmed future slot — staying quiet",
                        applicant.id,
                    )
                    return ReplyAction("", S.CHANNEL_NONE)

        # One DeepSeek pass on the full joined text (prompt prefers the newest part).
        understood = _understand_slack(applicant, answer)
        start_at, is_later = _deepseek_later_slot(understood, fresh or answer)

        if is_later and start_at is not None and not _deepseek_ready_now(
            understood, fresh or answer
        ):
            booked = _book_availability_slot(applicant, start_at)
            if booked is None:
                return ReplyAction("", S.CHANNEL_NONE)
            logger.info(
                "Applicant=%s DeepSeek rescheduled waiting_start to %s",
                applicant.id,
                applicant.next_action_at.isoformat() if applicant.next_action_at else "?",
            )
            return booked

        if _deepseek_ready_now(understood, fresh or answer):
            applicant.stage = S.STAGE_WAITING_INTRO
            applicant.next_action_at = None
            _append_log(applicant, "bot", MSG_ASK_INTRO)
            logger.info(
                "Applicant=%s DeepSeek ready — starting intro",
                applicant.id,
            )
            return ReplyAction(MSG_ASK_INTRO, S.CHANNEL_SLACK)

        applicant.stage = S.STAGE_WAITING_READY
        applicant.next_action_at = None
        _append_log(applicant, "bot", MSG_ASK_READY)
        logger.info("Applicant=%s promised time reached — asking if ready", applicant.id)
        return ReplyAction(MSG_ASK_READY, S.CHANNEL_SLACK)

    if applicant.stage == S.STAGE_WAITING_READY:
        if not is_slack_business_hours():
            applicant.next_action_at = next_slack_business_open()
            return ReplyAction("", S.CHANNEL_NONE)
        answer = _candidate_text_since_last_bot(applicant) or msg
        fresh = _freshest_candidate_text(answer)
        understood = _understand_slack(applicant, answer)
        if looks_like_slot_confirmation(fresh or answer) and not _deepseek_ready_now(
            understood, fresh or answer
        ):
            # "Sure" without being ready-now — wait; don't re-book.
            logger.info(
                "Applicant=%s short confirm while waiting_ready — waiting",
                applicant.id,
            )
            applicant.next_action_at = None
            return ReplyAction("", S.CHANNEL_NONE)
        start_at, is_later = _deepseek_later_slot(understood, fresh or answer)

        if is_later and start_at is not None and not _deepseek_ready_now(
            understood, fresh or answer
        ):
            booked = _book_availability_slot(applicant, start_at)
            if booked is None:
                return ReplyAction("", S.CHANNEL_NONE)
            logger.info(
                "Applicant=%s DeepSeek not ready — rescheduled to %s",
                applicant.id,
                applicant.next_action_at.isoformat() if applicant.next_action_at else "?",
            )
            return booked

        if not _deepseek_ready_now(understood, fresh or answer):
            logger.info(
                "Applicant=%s DeepSeek: not ready yet (intent=%s) — waiting",
                applicant.id,
                understood.intent,
            )
            applicant.next_action_at = None
            return ReplyAction("", S.CHANNEL_NONE)

        applicant.stage = S.STAGE_WAITING_INTRO
        applicant.next_action_at = None
        _append_log(applicant, "bot", MSG_ASK_INTRO)
        return ReplyAction(MSG_ASK_INTRO, S.CHANNEL_SLACK)

    if applicant.stage == S.STAGE_WAITING_INTRO:
        if not is_slack_business_hours():
            applicant.next_action_at = next_slack_business_open()
            return ReplyAction("", S.CHANNEL_NONE)
        answer = _candidate_text_since_last_bot(applicant) or msg
        understood = _understand_slack(applicant, answer)

        # Candidate answered the project question while we were still on intro.
        project_ready = understood.intent == "project" and (
            understood.answers_current_question
            or _bot_already_sent(applicant, MSG_ASK_RECENT_PROJECT)
        )
        if _classifier_failed(understood) and _bot_already_sent(
            applicant, MSG_ASK_RECENT_PROJECT
        ):
            project_ready = _looks_like_substantive_project(answer)
        if project_ready and _bot_already_sent(applicant, MSG_ASK_RECENT_PROJECT):
            logger.info(
                "Applicant=%s answered project while on intro (ai=%s) — advancing to tech",
                applicant.id,
                understood.intent,
            )
            return _reply_tech_questions(applicant)

        if _classifier_failed(understood):
            intro_ok = _looks_like_substantive_intro(answer)
        elif understood.intent == "intro":
            intro_ok = True
        elif understood.intent in ("greeting", "placeholder", "other"):
            intro_ok = False
        else:
            intro_ok = bool(
                understood.answers_current_question and understood.intent == "intro"
            )

        if not intro_ok:
            logger.info(
                "Applicant=%s intro incomplete (ai=%s) — waiting",
                applicant.id,
                understood.intent,
            )
            applicant.next_action_at = None
            return ReplyAction("", S.CHANNEL_NONE)

        applicant.stage = S.STAGE_WAITING_RECENT_PROJECT
        applicant.next_action_at = None
        if _bot_already_sent(applicant, MSG_ASK_RECENT_PROJECT):
            prior = _candidate_text_after_bot_ask(applicant, MSG_ASK_RECENT_PROJECT)
            prior_ok = False
            if prior:
                prior_u = _understand_slack(applicant, prior)
                prior_ok = prior_u.intent == "project" or (
                    _classifier_failed(prior_u) and _looks_like_substantive_project(prior)
                )
            if prior_ok:
                return _reply_tech_questions(applicant)
            logger.info(
                "Applicant=%s already asked recent project — waiting for answer",
                applicant.id,
            )
            return ReplyAction("", S.CHANNEL_NONE)
        _append_log(applicant, "bot", MSG_ASK_RECENT_PROJECT)
        return ReplyAction(MSG_ASK_RECENT_PROJECT, S.CHANNEL_SLACK)

    if applicant.stage == S.STAGE_WAITING_RECENT_PROJECT:
        if not is_slack_business_hours():
            applicant.next_action_at = next_slack_business_open()
            return ReplyAction("", S.CHANNEL_NONE)
        answer = _candidate_text_since_last_bot(applicant) or msg
        understood = _understand_slack(applicant, answer)

        # Stage drift: tech Qs already happened on Slack and candidate answered them.
        if understood.intent == "tech_answer" or (
            _classifier_failed(understood)
            and (
                _looks_like_tech_answer(answer)
                or _looks_like_tech_answer(_tech_answer_text(applicant))
            )
        ):
            logger.info(
                "Applicant=%s tech answers while on recent_project — recovering",
                applicant.id,
            )
            return _reply_tech_questions(applicant)

        # Project already answered earlier; don't keep re-asking — move to tech Qs.
        prior_project = _candidate_text_after_bot_ask(applicant, MSG_ASK_RECENT_PROJECT)
        if prior_project and understood.intent in ("placeholder", "greeting", "tech_answer"):
            prior_u = _understand_slack(applicant, prior_project)
            if prior_u.intent == "project" or (
                _classifier_failed(prior_u)
                and _looks_like_substantive_project(prior_project)
            ):
                if understood.intent == "tech_answer" or _looks_like_placeholder_reply(answer):
                    return _reply_tech_questions(applicant)

        project_ok = understood.intent == "project" or (
            understood.answers_current_question and understood.intent == "project"
        )
        if understood.intent == "already_answered" or (
            not project_ok and understood.intent != "project"
        ):
            prior = _candidate_text_after_bot_ask(applicant, MSG_ASK_RECENT_PROJECT)
            if prior:
                prior_u = _understand_slack(applicant, prior)
                if prior_u.intent == "project" or (
                    _classifier_failed(prior_u) and _looks_like_substantive_project(prior)
                ):
                    answer = prior
                    project_ok = True
        if _classifier_failed(understood):
            if not _looks_like_substantive_project(answer):
                answer = _candidate_text_after_bot_ask(applicant, MSG_ASK_RECENT_PROJECT) or answer
            project_ok = _looks_like_substantive_project(answer)
        elif understood.intent in ("greeting", "placeholder"):
            project_ok = False
        elif understood.intent == "project":
            project_ok = True

        if not project_ok:
            logger.info(
                "Applicant=%s project incomplete (ai=%s) — waiting",
                applicant.id,
                understood.intent,
            )
            applicant.next_action_at = None
            return ReplyAction("", S.CHANNEL_NONE)
        return _reply_tech_questions(applicant)

    if applicant.stage == S.STAGE_WAITING_TECH_ANSWERS:
        # Fired 15 minutes after the tech questions (or later if they answered late).
        if not is_slack_business_hours():
            applicant.next_action_at = next_slack_business_open()
            return ReplyAction("", S.CHANNEL_NONE)

        time_passed_sent = _bot_already_sent(applicant, MSG_TECH_TIME_PASSED)
        asked_at = _tech_questions_asked_at(applicant)
        # Only enforce the 15-minute hold before the deadline notice.
        # After "The time was passed" (or once we're past the window), process
        # late answers immediately — do not keep pushing the check out.
        if asked_at is not None and not time_passed_sent:
            earliest = schedule_tech_answer_check(asked_at)
            now = datetime.now(timezone.utc)
            if now < earliest:
                applicant.next_action_at = earliest
                logger.info(
                    "Applicant=%s tech-answer check too early — waiting until %s",
                    applicant.id,
                    earliest.isoformat(),
                )
                return ReplyAction("", S.CHANNEL_NONE)

        answer = _tech_answer_text(applicant)
        if time_passed_sent:
            late = _candidate_text_since_last_bot(applicant).strip()
            if late:
                late_u = _understand_slack(applicant, late)
                if late_u.intent == "tech_answer" or (
                    _classifier_failed(late_u) and not _looks_like_placeholder_reply(late)
                ):
                    full = _candidate_text_after_bot_ask(
                        applicant, "Please answer within 15 mins"
                    ).strip()
                    real = "\n".join(
                        c
                        for c in full.split("\n")
                        if c.strip() and not _looks_like_placeholder_reply(c.strip())
                    ).strip()
                    if late_u.intent == "tech_answer":
                        answer = real or late
                    else:
                        answer = real if _looks_like_tech_answer(real) else (
                            late if _looks_like_tech_answer(late) else real or late
                        )

        has_answer = False
        if answer and not _looks_like_placeholder_reply(answer):
            ans_u = _understand_slack(applicant, answer)
            if _classifier_failed(ans_u):
                has_answer = _looks_like_tech_answer(answer)
            elif ans_u.intent == "tech_answer":
                has_answer = True
            elif ans_u.intent in ("placeholder", "greeting"):
                has_answer = False
            else:
                # Long substantive replies DeepSeek may label other — keep heuristic backup.
                has_answer = _looks_like_tech_answer(answer)

        if not has_answer:
            if time_passed_sent:
                logger.info(
                    "Applicant=%s still no tech answers after time-passed notice — waiting",
                    applicant.id,
                )
                applicant.next_action_at = None
                return ReplyAction("", S.CHANNEL_NONE)
            logger.info(
                "Applicant=%s no tech answers after 15 minutes — sending time-passed notice",
                applicant.id,
            )
            applicant.next_action_at = None
            _append_log(applicant, "bot", MSG_TECH_TIME_PASSED)
            return ReplyAction(MSG_TECH_TIME_PASSED, S.CHANNEL_SLACK)

        applicant.stage = S.STAGE_GEMINI_FOLLOWUP_1
        applicant.gemini_followup_count = 1
        applicant.next_action_at = None
        text = generate_tech_followup(
            TECH_QUESTIONS_TEXT, answer, 1, project.proj_title, project.proj_description
        )
        _append_log(applicant, "bot", text)
        return ReplyAction(text, S.CHANNEL_SLACK)

    if applicant.stage == S.STAGE_GEMINI_FOLLOWUP_1:
        if not is_slack_business_hours():
            applicant.next_action_at = next_slack_business_open()
            return ReplyAction("", S.CHANNEL_NONE)
        applicant.stage = S.STAGE_GEMINI_FOLLOWUP_2
        applicant.gemini_followup_count = 2
        applicant.next_action_at = None
        text = generate_tech_followup(
            TECH_QUESTIONS_TEXT, msg, 2, project.proj_title, project.proj_description
        )
        _append_log(applicant, "bot", text)
        return ReplyAction(text, S.CHANNEL_SLACK)

    if applicant.stage == S.STAGE_GEMINI_FOLLOWUP_2:
        if not is_slack_business_hours():
            applicant.next_action_at = next_slack_business_open()
            return ReplyAction("", S.CHANNEL_NONE)
        applicant.stage = S.STAGE_WAITING_GITHUB_ASK
        applicant.gemini_followup_count = 3
        applicant.next_action_at = None
        text = generate_tech_followup(
            TECH_QUESTIONS_TEXT, msg, 3, project.proj_title, project.proj_description
        )
        _append_log(applicant, "bot", text)
        return ReplyAction(text, S.CHANNEL_SLACK)

    if applicant.stage == S.STAGE_WAITING_GITHUB_ASK:
        if not is_slack_business_hours():
            applicant.next_action_at = next_slack_business_open()
            return ReplyAction("", S.CHANNEL_NONE)
        applicant.stage = S.STAGE_WAITING_GITHUB
        applicant.next_action_at = None
        _append_log(applicant, "bot", MSG_ASK_GITHUB)
        return ReplyAction(MSG_ASK_GITHUB, S.CHANNEL_SLACK)

    if applicant.stage == S.STAGE_WAITING_GITHUB:
        if not is_slack_business_hours():
            applicant.next_action_at = next_slack_business_open()
            return ReplyAction("", S.CHANNEL_NONE)

        # Only the candidate text AFTER our last bot message counts as a reply.
        answer = _candidate_text_since_last_bot(applicant).strip()
        if not answer:
            # Already asked (or nothing new) — wait; do not re-send every poll.
            applicant.next_action_at = None
            return ReplyAction("", S.CHANNEL_NONE)

        fresh = _freshest_candidate_text(answer)
        understood = _understand_slack(applicant, fresh or answer)

        no_account = (
            (understood.intent == "github" and not understood.answers_current_question)
            or _looks_like_no_github_account(fresh or answer)
            or _looks_like_no_github_account(answer)
        )
        if no_account:
            if _bot_already_sent(applicant, MSG_NEED_GITHUB_ACCOUNT[:40]) or (
                applicant.chat_reply or ""
            ).startswith(MSG_NEED_GITHUB_ACCOUNT[:30]):
                applicant.next_action_at = None
                logger.info(
                    "Applicant=%s already told they need a GitHub account — waiting",
                    applicant.id,
                )
                return ReplyAction("", S.CHANNEL_NONE)
            applicant.next_action_at = None
            _append_log(applicant, "bot", MSG_NEED_GITHUB_ACCOUNT)
            return ReplyAction(MSG_NEED_GITHUB_ACCOUNT, S.CHANNEL_SLACK)

        # Prefer the newest bubble so older Slack @mentions are not usernames.
        username = extract_github_username(fresh) or extract_github_username(answer)
        if (
            not username
            and understood.intent == "github"
            and understood.answers_current_question
        ):
            username = extract_github_username(_freshest_candidate_text(answer))
        if username:
            applicant.github_username = username
            applicant.stage = S.STAGE_GITHUB_INVITED
            start_at, is_later = _resolve_assignment_slot(understood, answer)
            text = MSG_ASSIGNMENT_INVITED
            if is_later and start_at is not None:
                agree = _availability_agreement(start_at)
                text = f"{MSG_ASSIGNMENT_INVITED}\n\n{agree}"
                # 3h check runs after their agreed start, not from invite send.
                applicant.next_action_at = start_at + timedelta(hours=3)
                logger.info(
                    "Applicant=%s github=%s + assignment slot %s — agree in invite",
                    applicant.id,
                    username,
                    start_at.isoformat(),
                )
            else:
                applicant.next_action_at = None
            _append_log(applicant, "bot", text)
            return ReplyAction(text, S.CHANNEL_SLACK)

        # Still no username — ask once, then wait for their next message.
        if _already_asked_github(applicant):
            applicant.next_action_at = None
            logger.info(
                "Applicant=%s already asked for GitHub — waiting (intent=%s)",
                applicant.id,
                understood.intent,
            )
            return ReplyAction("", S.CHANNEL_NONE)

        text = "Could you please share your GitHub username?"
        applicant.next_action_at = None
        _append_log(applicant, "bot", text)
        return ReplyAction(text, S.CHANNEL_SLACK)

    return ReplyAction("", S.CHANNEL_NONE)


def is_action_due(applicant: Applicant) -> bool:
    if applicant.stage in (S.STAGE_COMPLETED, S.STAGE_FAILED, S.STAGE_WAITING_EMAIL_RESUME):
        return False
    if applicant.next_action_at is None:
        return False
    due = applicant.next_action_at
    if due.tzinfo is None:
        due = due.replace(tzinfo=timezone.utc)
    if due > datetime.now(timezone.utc):
        return False

    # Don't send assessment replies outside Mon–Fri 10:00–17:00 America/New_York.
    if applicant.stage in SLACK_ASSESSMENT_STAGES | {S.STAGE_WAITING_AVAILABILITY}:
        if not is_slack_business_hours():
            applicant.next_action_at = next_slack_business_open()
            return False
    return True
