"""Structured FM assessment chat powered by OpenRouter."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any

from src.ai.llm import LLMError, generate
from src.ai.ste100 import with_ste100
from src.assessment.templates import (
    MSG_ASK_GITHUB,
    MSG_ASSIGNMENT_INVITED,
    MSG_ASSIGNMENT_RECEIVED,
    rejection_message,
)
from src.github.invite import extract_github_username, invite_to_repo

logger = logging.getLogger(__name__)

# Stages stored on fm_applicants.stage
STAGE_OUTREACH = "outreach_sent"
STAGE_CHATTING = "assessment_chat"
STAGE_ASK_GITHUB = "ask_github"
STAGE_WAITING_GITHUB = "waiting_github"
STAGE_ASSIGNMENT = "assignment_pending"
STAGE_RECEIVED = "assignment_received"
STAGE_REJECTION_DUE = "rejection_scheduled"
STAGE_REJECTED = "rejected"
STAGE_CLOSED = "closed"

ASSESSMENT_SYSTEM = with_ste100(
    """You are David, a recruiter and technical screener for Kontrora on freelancermap.
You write freelancermap chat messages after an outreach DM.

Write 2–5 short sentences, or a short numbered list.
Do not use markdown headings. Do not say you are an AI.
Do not invite to Slack or email. Stay on freelancermap chat.
Do not ask for a GitHub username yet — that is a fixed message later.
Do not send repository links.

Hiring structure (one clear ask per reply):
1) Initial screening — availability, location, contract preference, rate, stack, workload
2) Experience validation — 2–3 projects, duties, tech, team size, hard problem, result
3) Technical discussion — architecture, debug steps, frameworks, API/data flow, tests
   Prefer FastAPI / Next.js / PostgreSQL / Stripe / multi-tenant PSA billing when useful.
4) Practical scenario — one product problem; ask assumptions, steps, and tradeoffs
5) Collaboration — async work, code review, unclear requirements, deadlines, time zones

Rules:
- Give a short reply to their last answer, then ask the next question.
- Prefer one clear question per message (two only if tightly related).
- If they are a clear mismatch, be polite and stop the process.
- Never invent that you reviewed code or invited them to GitHub.
"""
)


@dataclass
class ChatResult:
    reply: str | None
    stage: str
    message_count: int
    github_unlocked: bool
    github_username: str | None = None
    invite_ok: bool | None = None
    rejection_due_at: str | None = None
    action: str | None = None  # ask_github | invited | received | rejected | chat


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _iso(dt: datetime) -> str:
    return dt.isoformat()


def _history_blob(messages: list[dict[str, Any]], limit: int = 24) -> str:
    lines: list[str] = []
    for m in messages[-limit:]:
        role = "Freelancer" if m.get("role") == "freelancer" else "David"
        body = (m.get("body") or "").strip()
        if body:
            lines.append(f"{role}: {body}")
    return "\n".join(lines) if lines else "(no prior messages)"


def _phase_hint(bot_messages: int, unlock_after: int) -> str:
    # Spread ~20–30 bot turns across phases before GitHub ask.
    progress = bot_messages / max(1, unlock_after)
    if progress < 0.2:
        return "Phase: initial screening. Ask the next screening question."
    if progress < 0.4:
        return "Phase: experience validation. Dig into real project ownership."
    if progress < 0.75:
        return "Phase: technical discussion. Ask a concrete technical question."
    if progress < 0.9:
        return "Phase: practical scenario / deeper reasoning."
    return "Phase: collaboration & working style. Almost done with chat assessment."


def generate_assessment_reply(
    *,
    display_name: str | None,
    messages: list[dict[str, Any]],
    latest_freelancer_message: str,
    bot_message_count: int,
    unlock_after: int,
) -> str:
    name = (display_name or "").strip().split()[0] if display_name else "there"
    remaining = max(0, unlock_after - bot_message_count)
    user = (
        f"Candidate first name: {name}\n"
        f"Bot assessment messages sent so far: {bot_message_count}\n"
        f"Target before take-home: ~{unlock_after} bot messages "
        f"({remaining} remaining after this reply).\n"
        f"{_phase_hint(bot_message_count, unlock_after)}\n\n"
        f"Conversation so far:\n{_history_blob(messages)}\n\n"
        f"Latest freelancer message:\n{latest_freelancer_message.strip()}\n\n"
        "Write David's next freelancermap reply only. Use ASD-STE100."
    )
    return generate(ASSESSMENT_SYSTEM, user, max_tokens=900).strip()


def looks_like_assignment_submitted(text: str) -> bool:
    low = (text or "").lower()
    keys = (
        "submitted",
        "i submitted",
        "pull request",
        "opened a pr",
        "created a pr",
        "finished the assignment",
        "completed the assignment",
        "done with the assignment",
        "pushed my",
        "forked",
    )
    return any(k in low for k in keys)


def process_freelancer_message(
    *,
    applicant: dict[str, Any],
    messages: list[dict[str, Any]],
    freelancer_text: str,
    unlock_after: int = 20,
    max_messages: int = 30,
) -> ChatResult:
    """Decide the next bot reply and stage transitions."""
    stage = applicant.get("stage") or STAGE_OUTREACH
    count = int(applicant.get("message_count") or 0)
    unlocked = bool(applicant.get("github_unlocked"))
    gh_user = applicant.get("github_username")
    name = applicant.get("display_name")
    text = (freelancer_text or "").strip()
    unlock_after = max(8, min(int(unlock_after or 20), int(max_messages or 30)))

    if stage in (STAGE_REJECTED, STAGE_CLOSED):
        return ChatResult(
            reply=None,
            stage=stage,
            message_count=count,
            github_unlocked=unlocked,
            github_username=gh_user,
            action="noop",
        )

    # Due rejection (extension/API polls with empty new message or cron)
    if stage == STAGE_REJECTION_DUE:
        due = applicant.get("rejection_due_at")
        if due:
            try:
                due_dt = datetime.fromisoformat(str(due).replace("Z", "+00:00"))
                if _now() < due_dt:
                    return ChatResult(
                        reply=None,
                        stage=stage,
                        message_count=count,
                        github_unlocked=unlocked,
                        github_username=gh_user,
                        rejection_due_at=str(due),
                        action="wait_rejection",
                    )
            except ValueError:
                pass
        reply = rejection_message(name)
        return ChatResult(
            reply=reply,
            stage=STAGE_REJECTED,
            message_count=count + 1,
            github_unlocked=unlocked,
            github_username=gh_user,
            action="rejected",
        )

    if not text and stage not in (STAGE_REJECTION_DUE,):
        return ChatResult(
            reply=None,
            stage=stage,
            message_count=count,
            github_unlocked=unlocked,
            github_username=gh_user,
            action="noop",
        )

    # Waiting for GitHub username
    if stage in (STAGE_ASK_GITHUB, STAGE_WAITING_GITHUB):
        username = extract_github_username(text)
        if not username:
            return ChatResult(
                reply=(
                    "Please reply with your GitHub username "
                    "(for example: octocat or https://github.com/octocat)."
                ),
                stage=STAGE_WAITING_GITHUB,
                message_count=count + 1,
                github_unlocked=True,
                github_username=None,
                action="need_github",
            )
        ok = False
        try:
            ok = invite_to_repo(username)
        except Exception as e:
            logger.exception("GitHub invite failed for %s: %s", username, e)
            ok = False
        if not ok:
            return ChatResult(
                reply=(
                    f"I couldn't invite `{username}` yet — please double-check the "
                    "username is correct and that the account exists, then send it again."
                ),
                stage=STAGE_WAITING_GITHUB,
                message_count=count + 1,
                github_unlocked=True,
                github_username=username,
                invite_ok=False,
                action="invite_failed",
            )
        return ChatResult(
            reply=MSG_ASSIGNMENT_INVITED,
            stage=STAGE_ASSIGNMENT,
            message_count=count + 1,
            github_unlocked=True,
            github_username=username,
            invite_ok=True,
            action="invited",
        )

    # Assignment pending — look for submission signal
    if stage == STAGE_ASSIGNMENT:
        if looks_like_assignment_submitted(text):
            due = _iso(_now() + timedelta(days=1, hours=12))  # ~1.5 days
            return ChatResult(
                reply=MSG_ASSIGNMENT_RECEIVED,
                stage=STAGE_REJECTION_DUE,
                message_count=count + 1,
                github_unlocked=True,
                github_username=gh_user,
                rejection_due_at=due,
                action="received",
            )
        # Soft ack; stay in assignment_pending
        return ChatResult(
            reply=(
                "Thanks — please follow the readme in the assignment repo and "
                "reply here once you've submitted."
            ),
            stage=STAGE_ASSIGNMENT,
            message_count=count + 1,
            github_unlocked=True,
            github_username=gh_user,
            action="assignment_nudge",
        )

    if stage == STAGE_RECEIVED:
        due = applicant.get("rejection_due_at") or _iso(_now() + timedelta(days=1, hours=12))
        return ChatResult(
            reply=None,
            stage=STAGE_REJECTION_DUE,
            message_count=count,
            github_unlocked=unlocked,
            github_username=gh_user,
            rejection_due_at=str(due),
            action="wait_rejection",
        )

    # Assessment chat (including first reply after outreach)
    if count >= unlock_after:
        return ChatResult(
            reply=MSG_ASK_GITHUB,
            stage=STAGE_WAITING_GITHUB,
            message_count=count + 1,
            github_unlocked=True,
            github_username=gh_user,
            action="ask_github",
        )

    try:
        reply = generate_assessment_reply(
            display_name=name,
            messages=messages,
            latest_freelancer_message=text,
            bot_message_count=count,
            unlock_after=unlock_after,
        )
    except LLMError as e:
        logger.error("assessment LLM failed: %s", e)
        reply = (
            "Thanks for your message — I hit a temporary issue generating the "
            "next question. Could you briefly restate your last answer while I continue?"
        )

    new_count = count + 1
    # Keep chatting until unlock_after bot messages; the next freelancer
    # turn then gets MSG_ASK_GITHUB alone (exact copy, not mixed with LLM text).
    return ChatResult(
        reply=reply,
        stage=STAGE_CHATTING,
        message_count=new_count,
        github_unlocked=False,
        github_username=gh_user,
        action="chat",
    )
