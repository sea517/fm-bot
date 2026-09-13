"""Use the configured LLM (DeepSeek via OpenRouter) to understand Slack replies."""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from datetime import datetime, timezone

from src.ai.llm import LLMError, generate
from src.ai.ste100 import with_ste100
from src.conversation.timing import (
    availability_is_later,
    format_availability_when,
    parse_availability_start,
    sanitize_scheduled_start,
)

logger = logging.getLogger(__name__)

_SYSTEM = with_ste100(
    """You are Oliver's recruiting assistant on Slack.
READ the candidate's latest message in context and classify it.
Do NOT write a reply to the candidate.

Assessment questions in order:
1) availability (Mon–Fri 10:00–17:00 America/New_York)
2) brief intro (location, experience, background)
3) most recent project / what they implemented
4) technical questions
5) GitHub username / ready to start the booked slot

Rules:
- If several messages are pasted together, prefer the NEWEST / last update.
- Greetings / name-only ("Hi, I am Nick") do NOT answer availability.
- "Sure, I will share…" / "give me a moment" is PLACEHOLDER — not an answer yet.
- "I just answered" / "I already told you" is ALREADY_ANSWERED.
- A long project write-up is PROJECT, not INTRO.
- Going offline today / proposing a future day or time (e.g. "tomorrow 10am EDT",
  "Wednesday 10–12") is AVAILABILITY with AVAILABILITY_WHEN=later — NOT ready.
- "I'm ready" / "let's start now" / "good to begin" while waiting for a booked
  slot is READY (ANSWERS=yes, AVAILABILITY_WHEN=now).
- Confirming Oliver's already-proposed slot ("Sure, that works", "Looking forward
  to it", "Perfect") with NO new day/time is ALREADY_ANSWERED (or OTHER),
  AVAILABILITY_WHEN=none, AVAILABILITY_START_ISO=none — NOT a reschedule.
- If free now / today soon / within about an hour and not proposing a later day,
  AVAILABILITY_WHEN=now (or INTENT=ready when Oliver asked if they are ready).
- If they name a future day/time for availability, AVAILABILITY_WHEN=later and
  fill AVAILABILITY_START_ISO in UTC when possible. Use the CURRENT calendar year.
  Never invent a past year. Convert IST/other zones to UTC correctly.
- No GitHub yet / will create an account / offer ZIP instead → INTENT=github,
  ANSWERS=no.
- Sharing a GitHub username or profile URL → INTENT=github, ANSWERS=yes.

Output EXACTLY these lines (no markdown, no extra commentary):
INTENT: greeting|availability|intro|project|tech_answer|github|ready|placeholder|already_answered|other
ANSWERS: yes|no
AVAILABILITY_WHEN: now|later|none
AVAILABILITY_START_ISO: <UTC ISO-8601 or none>
SUMMARY: <one short ASD-STE100 sentence>
"""
)


@dataclass
class SlackUnderstanding:
    intent: str
    answers_current_question: bool
    availability_when: str | None
    availability_start_iso: str | None
    summary: str
    raw: dict

    @property
    def start_at(self) -> datetime | None:
        if not self.availability_start_iso:
            return None
        try:
            raw = self.availability_start_iso.strip().replace("Z", "+00:00")
            dt = datetime.fromisoformat(raw)
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            return sanitize_scheduled_start(dt.astimezone(timezone.utc))
        except ValueError:
            return None


def _parse_line_result(text: str) -> dict:
    text = (text or "").strip()
    if not text:
        raise LLMError("empty classifier output")
    # Drop accidental fences / leading narration.
    lines = []
    for line in text.splitlines():
        s = line.strip()
        if not s or s.startswith("```"):
            continue
        lines.append(s)
    blob = "\n".join(lines)

    def field(name: str) -> str | None:
        m = re.search(
            rf"^{re.escape(name)}\s*:\s*(.+)$",
            blob,
            re.IGNORECASE | re.MULTILINE,
        )
        if not m:
            return None
        return m.group(1).strip().strip('"').strip("'")

    intent = (field("INTENT") or "other").lower()
    answers_raw = (field("ANSWERS") or "no").lower()
    when_raw = (field("AVAILABILITY_WHEN") or "none").lower()
    iso = field("AVAILABILITY_START_ISO")
    summary = field("SUMMARY") or intent

    if intent not in {
        "greeting",
        "availability",
        "intro",
        "project",
        "tech_answer",
        "github",
        "ready",
        "placeholder",
        "already_answered",
        "other",
    }:
        intent = "other"

    answers = answers_raw in ("yes", "y", "true")
    when: str | None
    if when_raw in ("now", "later"):
        when = when_raw
    else:
        when = None
    if iso and iso.lower() in ("none", "null", "n/a", "na", "-"):
        iso = None

    return {
        "intent": intent,
        "answers_current_question": answers,
        "availability_when": when,
        "availability_start_iso": iso,
        "summary": summary,
    }


def understand_slack_message(
    *,
    stage: str,
    bot_question: str,
    candidate_message: str,
    recent_log: str = "",
) -> SlackUnderstanding:
    """
    Ask DeepSeek/OpenRouter what the candidate meant, then return a structured result.
    """
    user = f"""Current stage: {stage}

Oliver's last question / prompt the candidate should answer:
{bot_question or "(none)"}

Recent conversation (oldest → newest, truncated):
{recent_log or "(none)"}

Candidate message to classify:
{candidate_message}
"""

    try:
        raw_text = generate(_SYSTEM, user, max_tokens=2500)
        data = _parse_line_result(raw_text)
    except Exception:
        logger.exception("Slack message understanding failed — using empty result")
        return SlackUnderstanding(
            intent="other",
            answers_current_question=False,
            availability_when=None,
            availability_start_iso=None,
            summary="(classifier failed)",
            raw={},
        )

    understanding = SlackUnderstanding(
        intent=str(data["intent"]),
        answers_current_question=bool(data["answers_current_question"]),
        availability_when=data.get("availability_when"),
        availability_start_iso=data.get("availability_start_iso"),
        summary=str(data.get("summary") or ""),
        raw=data,
    )
    logger.info(
        "Slack understand stage=%s intent=%s answers=%s when=%s summary=%s",
        stage,
        understanding.intent,
        understanding.answers_current_question,
        understanding.availability_when,
        understanding.summary[:160],
    )
    return understanding


def resolve_availability_start(
    understanding: SlackUnderstanding, candidate_message: str
) -> tuple[datetime | None, bool]:
    """
    Return (start_at_utc, is_later).

    Prefers the model ISO when present; falls back to local parse of the message.
    Confirmations of an already-booked slot are never treated as a new later slot.
    """
    from src.conversation.timing import looks_like_slot_confirmation

    if looks_like_slot_confirmation(candidate_message):
        return None, False

    start = understanding.start_at
    if start is None:
        start = parse_availability_start(candidate_message)
    elif understanding.availability_start_iso:
        # Prefer local parse when the model invents a clock that disagrees with
        # an explicit candidate timezone range (e.g. IST hours misread as EDT).
        parsed = parse_availability_start(candidate_message)
        if parsed is not None:
            start = parsed

    if start is not None:
        start = sanitize_scheduled_start(start)

    if understanding.availability_when == "later":
        if start is None:
            from src.conversation.timing import next_slack_business_open

            start = next_slack_business_open()
        return start, True

    if understanding.availability_when == "now":
        return start, False

    later = availability_is_later(start)
    return start, later


def last_bot_question_from_log(conversation_log: str | None) -> str:
    if not conversation_log:
        return ""
    try:
        import json

        log = json.loads(conversation_log)
    except Exception:
        return ""
    for entry in reversed(log):
        if entry.get("role") == "bot":
            return (entry.get("text") or "").strip()
    return ""


def recent_log_excerpt(conversation_log: str | None, max_entries: int = 8) -> str:
    if not conversation_log:
        return ""
    try:
        import json

        log = json.loads(conversation_log)
    except Exception:
        return ""
    parts: list[str] = []
    for entry in log[-max_entries:]:
        role = entry.get("role") or "?"
        text = (entry.get("text") or "").strip().replace("\n", " ")
        if len(text) > 240:
            text = text[:240] + "…"
        parts.append(f"{role}: {text}")
    return "\n".join(parts)


def scheduled_when_label(start_at: datetime) -> str:
    return format_availability_when(start_at)
