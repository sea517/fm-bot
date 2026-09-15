"""Deterministic FM assessment chat (SPEC). LLM only supplies wording when authorised."""

from __future__ import annotations

import logging
import random
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any

from src.ai.llm import LLMError, generate
from src.assessment.detectors import (
    looks_like_call_confirm,
    looks_like_hard_handoff,
    looks_like_no_github,
    looks_like_opt_out,
    looks_like_submission,
    looks_like_wants_human,
)
from src.assessment.github_flow import (
    invite_collaborator,
    normalize_github_reference,
    scan_github_reference,
    validate_github_user,
)
from src.assessment.policy import PolicyLoadError, load_chat_policy
from src.assessment.stages import (
    FOLLOW_UP_LINE,
    HANDOFF_LINE,
    LEGACY_ASK_GITHUB,
    LEGACY_ASSIGNMENT,
    LEGACY_CHAT,
    LEGACY_OUTREACH,
    LEGACY_RECEIVED,
    LEGACY_REJECTION,
    LEGACY_WAITING_GITHUB,
    OPT_OUT_ACK,
    STAGE_0,
    STAGE_1,
    STAGE_2,
    STAGE_3,
    STAGE_4,
    STAGE_CLOSED,
    STAGE_HANDED_OFF,
    STAGE_OPTED_OUT,
    TERMINAL_STAGES,
)
from src.assessment.telegram_alert import send_handoff_alert
from src.assessment.templates import (
    MSG_ASK_GITHUB,
    MSG_ASSIGNMENT_INVITED,
    MSG_ASSIGNMENT_RECEIVED,
    MSG_INVALID_USERNAME,
    MSG_INVITE_HANDOFF_SOFT,
    MSG_ORG_CLARIFY,
)

logger = logging.getLogger(__name__)

_CHAT_TEMPERATURE = 0.2
# Reply delay band (seconds) — enforced for the client via send_after_sec
REPLY_DELAY_MIN_SEC = 120
REPLY_DELAY_MAX_SEC = 600


@dataclass
class ChatResult:
    reply: str | None
    stage: str
    message_count: int
    github_unlocked: bool
    github_username: str | None = None
    invite_ok: bool | None = None
    rejection_due_at: str | None = None
    action: str | None = None
    send_after_sec: int | None = None
    patch: dict[str, Any] = field(default_factory=dict)
    # Outbound gate: True only when deterministic layer authorises a send
    authorised: bool = False


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _delay_sec() -> int:
    return random.randint(REPLY_DELAY_MIN_SEC, REPLY_DELAY_MAX_SEC)


def _history_blob(messages: list[dict[str, Any]], limit: int = 24) -> str:
    lines: list[str] = []
    for m in messages[-limit:]:
        role = "Freelancer" if m.get("role") == "freelancer" else "Recruiter"
        body = (m.get("body") or "").strip()
        if body:
            lines.append(f"{role}: {body}")
    return "\n".join(lines) if lines else "(no prior messages)"


def _normalize_stage(stage: str | None) -> str:
    s = stage or LEGACY_OUTREACH
    mapping = {
        LEGACY_OUTREACH: STAGE_0,
        LEGACY_CHAT: STAGE_1,
        LEGACY_ASK_GITHUB: STAGE_2,
        LEGACY_WAITING_GITHUB: STAGE_3,
        LEGACY_ASSIGNMENT: STAGE_4,
        LEGACY_RECEIVED: STAGE_4,
        LEGACY_REJECTION: STAGE_CLOSED,
        "rejected": STAGE_CLOSED,
    }
    return mapping.get(s, s)


def _resolve_outreach(
    applicant: dict[str, Any], outreach_message: str | None
) -> str:
    for key in ("outreach_body", "outreach_message", "outreach_text"):
        raw = (applicant.get(key) or "").strip()
        if raw:
            subj = (applicant.get("outreach_subject") or "").strip()
            return f"Subject: {subj}\n\n{raw}" if subj else raw
    return (outreach_message or "").strip()


def _identity_keys(applicant: dict[str, Any]) -> list[str]:
    keys: list[str] = []
    for k in ("profile_key", "conversation_id"):
        v = (applicant.get(k) or "").strip()
        if v:
            keys.append(f"{k}:{v}")
    name = (applicant.get("display_name") or "").strip().lower()
    if name:
        keys.append(f"name:{name}")
    return keys


def generate_assessment_reply(
    *,
    display_name: str | None,
    messages: list[dict[str, Any]],
    latest_freelancer_message: str,
    stage: str,
    outreach_message: str,
) -> str:
    policy = load_chat_policy()
    name = (display_name or "").strip().split()[0] if display_name else "there"
    system = (
        f"{policy}\n\n"
        "---\n"
        "Deterministic context for this turn (not policy):\n"
        f"- Current stage: {stage}\n"
        f"- Candidate first name: {name}\n"
        "- Write only the next freelancermap chat message to the candidate.\n"
        "- Do not invent GitHub invites, reviews, or hiring outcomes.\n"
        "- Never request a GitHub username before Stage 2.\n"
    )
    user = (
        "[OUTREACH_MESSAGE] — exact text the candidate already received:\n"
        f"{outreach_message.strip()}\n\n"
        f"Conversation so far:\n{_history_blob(messages)}\n\n"
        f"Latest freelancer message:\n{latest_freelancer_message.strip()}\n\n"
        f"Current stage: {stage}\n\n"
        "Write the next recruiter reply only."
    )
    # SPEC: pinned OpenRouter model only — no Gemini fallback for this path.
    return generate(
        system,
        user,
        max_tokens=900,
        temperature=_CHAT_TEMPERATURE,
        provider="openrouter",
    ).strip()


def _authorised(
    reply: str,
    *,
    stage: str,
    count: int,
    action: str,
    patch: dict[str, Any] | None = None,
    github_username: str | None = None,
    invite_ok: bool | None = None,
    unlocked: bool = False,
) -> ChatResult:
    return ChatResult(
        reply=reply,
        stage=stage,
        message_count=count,
        github_unlocked=unlocked,
        github_username=github_username,
        invite_ok=invite_ok,
        action=action,
        send_after_sec=_delay_sec(),
        patch=patch or {},
        authorised=True,
    )


def _noop(
    applicant: dict[str, Any],
    *,
    action: str = "noop",
    stage: str | None = None,
    patch: dict[str, Any] | None = None,
) -> ChatResult:
    return ChatResult(
        reply=None,
        stage=stage or _normalize_stage(applicant.get("stage")),
        message_count=int(applicant.get("message_count") or 0),
        github_unlocked=bool(applicant.get("github_unlocked")),
        github_username=applicant.get("github_username"),
        action=action,
        patch=patch or {},
        authorised=False,
    )


def process_freelancer_message(
    *,
    applicant: dict[str, Any],
    messages: list[dict[str, Any]],
    freelancer_text: str,
    unlock_after: int = 20,
    max_messages: int = 30,
    outreach_message: str | None = None,
    opted_out_globally: bool = False,
    thread_link: str | None = None,
) -> ChatResult:
    """
    Deterministic turn. Generation runs only when authorised.
    unlock_after / max_messages kept for API compat; stage gates replace count unlock.
    """
    _ = unlock_after, max_messages
    stage = _normalize_stage(applicant.get("stage"))
    count = int(applicant.get("message_count") or 0)
    screening = int(applicant.get("screening_answers") or 0)
    gh_retries = int(applicant.get("github_invalid_retries") or 0)
    pending_github = applicant.get("pending_github")
    name = applicant.get("display_name")
    text = (freelancer_text or "").strip()
    patch: dict[str, Any] = {}

    if stage in TERMINAL_STAGES or applicant.get("handoff_reason") or applicant.get("opted_out"):
        return _noop(applicant, action="noop", stage=stage)

    if opted_out_globally:
        patch.update({"opted_out": True, "stage": STAGE_OPTED_OUT, "status": "closed"})
        return _noop(applicant, action="opted_out", stage=STAGE_OPTED_OUT, patch=patch)

    if not text:
        return _noop(applicant, stage=stage)

    # --- Capture GitHub on every inbound (model never triggers validation) ---
    scanned = scan_github_reference(text)
    if scanned and scanned.parsed:
        if stage in (STAGE_0, STAGE_1):
            patch["pending_github"] = scanned.parsed
            patch["raw_github_string"] = scanned.raw[:500]
            pending_github = scanned.parsed
        elif stage in (STAGE_2, STAGE_3):
            patch["raw_github_string"] = scanned.raw[:500]
            patch["parsed_username"] = scanned.parsed

    def handoff(trigger: str, *, final_line: str | None = HANDOFF_LINE) -> ChatResult:
        last_two = []
        for m in messages[-2:]:
            last_two.append(f"{m.get('role')}: {(m.get('body') or '')[:400]}")
        last_two.append(f"freelancer: {text[:400]}")
        send_handoff_alert(
            thread_link=thread_link
            or (
                f"https://www.freelancermap.com/app/pobox/main?id={applicant.get('conversation_id')}"
                if applicant.get("conversation_id")
                else None
            ),
            candidate_name=name,
            stage=stage,
            trigger=trigger,
            last_messages=last_two,
        )
        logger.warning(
            "handoff applicant=%s trigger=%s stage=%s",
            applicant.get("id"),
            trigger,
            stage,
        )
        hp = {
            **patch,
            "handoff_reason": trigger,
            "stage": STAGE_HANDED_OFF,
            "status": "closed",
        }
        if final_line:
            return _authorised(
                final_line,
                stage=STAGE_HANDED_OFF,
                count=count + 1,
                action="handoff",
                patch=hp,
                unlocked=bool(applicant.get("github_unlocked")),
                github_username=applicant.get("github_username"),
            )
        return _noop(applicant, action="handoff", stage=STAGE_HANDED_OFF, patch=hp)

    # --- Opt-out ---
    if looks_like_opt_out(text):
        patch.update(
            {
                "opted_out": True,
                "stage": STAGE_OPTED_OUT,
                "status": "closed",
                "handoff_reason": "opt_out",
            }
        )
        return _authorised(
            OPT_OUT_ACK,
            stage=STAGE_OPTED_OUT,
            count=count + 1,
            action="opt_out",
            patch=patch,
        )

    hard = looks_like_hard_handoff(text)
    if hard:
        return handoff(hard)

    if looks_like_no_github(text) and stage in (STAGE_2, STAGE_3):
        return handoff("no_github_account")

    if looks_like_call_confirm(text):
        return handoff("call_confirmed")

    # Human ask: first offer can be LLM; if they already asked and confirm — handoff.
    if looks_like_wants_human(text) and applicant.get("human_offer_sent"):
        return handoff("human_confirmed")

    # --- Stage 3: validate + invite (deterministic) ---
    if stage == STAGE_3:
        raw_ref = text
        parsed = normalize_github_reference(text)
        if not parsed.parsed and pending_github:
            parsed = normalize_github_reference(pending_github)
            raw_ref = str(pending_github)
        if not parsed.parsed:
            gh_retries += 1
            patch["github_invalid_retries"] = gh_retries
            if gh_retries >= 2:
                return handoff("github_invalid_twice")
            return _authorised(
                MSG_INVALID_USERNAME,
                stage=STAGE_3,
                count=count + 1,
                action="github_invalid",
                patch=patch,
                unlocked=True,
            )

        patch["parsed_username"] = parsed.parsed
        patch["raw_github_string"] = (raw_ref or "")[:500]
        validation = validate_github_user(parsed.parsed)
        patch["validation_result"] = validation.kind

        if validation.kind == "organization":
            return _authorised(
                MSG_ORG_CLARIFY,
                stage=STAGE_3,
                count=count + 1,
                action="github_org",
                patch=patch,
                unlocked=True,
            )
        if validation.kind == "invalid":
            gh_retries += 1
            patch["github_invalid_retries"] = gh_retries
            if gh_retries >= 2:
                return handoff("github_invalid_twice")
            return _authorised(
                MSG_INVALID_USERNAME,
                stage=STAGE_3,
                count=count + 1,
                action="github_invalid",
                patch=patch,
                unlocked=True,
            )
        if validation.kind == "ambiguous":
            return handoff(
                "github_api_ambiguous",
                final_line=MSG_INVITE_HANDOFF_SOFT + "\n\n" + HANDOFF_LINE,
            )

        invite = invite_collaborator(parsed.parsed)
        patch["invite_result"] = invite.kind
        if invite.kind != "ok":
            return handoff(
                "github_invite_ambiguous",
                final_line=MSG_INVITE_HANDOFF_SOFT + "\n\n" + HANDOFF_LINE,
            )
        patch.update(
            {
                "github_username": parsed.parsed,
                "github_unlocked": True,
                "stage": STAGE_4,
                "pending_github": None,
            }
        )
        return _authorised(
            MSG_ASSIGNMENT_INVITED,
            stage=STAGE_4,
            count=count + 1,
            action="invited",
            patch=patch,
            github_username=parsed.parsed,
            invite_ok=True,
            unlocked=True,
        )

    # --- Stage 4: after submission ---
    if stage == STAGE_4:
        if looks_like_submission(text):
            return _authorised(
                MSG_ASSIGNMENT_RECEIVED,
                stage=STAGE_4,
                count=count + 1,
                action="received",
                patch={**patch, "stage": STAGE_4},
                unlocked=True,
                github_username=applicant.get("github_username"),
            )
        # Answer follow-ups only via LLM (no proactive)
        outreach = _resolve_outreach(applicant, outreach_message)
        if not outreach:
            return handoff("outreach_missing")
        try:
            reply = generate_assessment_reply(
                display_name=name,
                messages=messages,
                latest_freelancer_message=text,
                stage=STAGE_4,
                outreach_message=outreach,
            )
        except (LLMError, PolicyLoadError) as e:
            logger.error("assessment LLM failed: %s", e)
            # Capture-only patch OK; never advance stage on inference failure
            capture_only = {
                k: v
                for k, v in patch.items()
                if k in ("pending_github", "raw_github_string", "parsed_username")
            }
            return _noop(applicant, action="error", stage=stage, patch=capture_only)
        if not reply:
            capture_only = {
                k: v
                for k, v in patch.items()
                if k in ("pending_github", "raw_github_string", "parsed_username")
            }
            return _noop(applicant, action="error", stage=stage, patch=capture_only)
        return _authorised(
            reply,
            stage=STAGE_4,
            count=count + 1,
            action="chat",
            patch=patch,
            unlocked=True,
            github_username=applicant.get("github_username"),
        )

    # --- Stage 2: assessment offer — ask for GitHub (code may send fixed ask) ---
    if stage == STAGE_2:
        # If they already pasted github, go validate next
        if pending_github or (scanned and scanned.parsed):
            user = pending_github or (scanned.parsed if scanned else None)
            patch.update(
                {
                    "stage": STAGE_3,
                    "pending_github": user,
                    "github_unlocked": True,
                }
            )
            # Fall through to treat as stage 3 on same turn
            applicant = {**applicant, **patch, "stage": STAGE_3}
            return process_freelancer_message(
                applicant=applicant,
                messages=messages,
                freelancer_text=text,
                outreach_message=outreach_message,
                opted_out_globally=False,
                thread_link=thread_link,
            )
        patch["stage"] = STAGE_3
        patch["github_unlocked"] = True
        return _authorised(
            MSG_ASK_GITHUB,
            stage=STAGE_3,
            count=count + 1,
            action="ask_github",
            patch=patch,
            unlocked=True,
        )

    # --- Stages 0–1: screening via LLM; never ask GitHub here ---
    outreach = _resolve_outreach(applicant, outreach_message)
    if not outreach:
        return handoff("outreach_missing")

    # Count a screening answer when we are already in stage 1
    if stage == STAGE_1 and len(text) > 15:
        screening += 1
        patch["screening_answers"] = screening

    # Advance to stage 2 after at least one screening answer (SPEC)
    next_stage = stage
    if stage == STAGE_0:
        next_stage = STAGE_1
    elif stage == STAGE_1 and screening >= 1:
        # After this reply, next inbound can be stage 2 — or move now if enough
        if screening >= 2:
            next_stage = STAGE_2

    if looks_like_wants_human(text):
        patch["human_offer_sent"] = True

    try:
        reply = generate_assessment_reply(
            display_name=name,
            messages=messages,
            latest_freelancer_message=text,
            stage=stage if stage in (STAGE_0, STAGE_1) else STAGE_1,
            outreach_message=outreach,
        )
    except (LLMError, PolicyLoadError) as e:
        logger.error("assessment LLM failed: %s", e)
        capture_only = {
            k: v
            for k, v in patch.items()
            if k in ("pending_github", "raw_github_string", "parsed_username")
        }
        return _noop(applicant, action="error", stage=stage, patch=capture_only)

    if not reply:
        capture_only = {
            k: v
            for k, v in patch.items()
            if k in ("pending_github", "raw_github_string", "parsed_username")
        }
        return _noop(applicant, action="error", stage=stage, patch=capture_only)

    # Safety: strip accidental GitHub asks before screening answer exists
    if screening < 1 and stage in (STAGE_0, STAGE_1):
        low = reply.lower()
        if "github" in low and (
            "username" in low or "profile link" in low or "send your github" in low
        ):
            logger.warning("stripped GitHub ask before screening answer")
            # Force a safe screening question instead of leaking a GitHub request
            reply = (
                "Thanks for the reply. "
                "Have you built production APIs with Python and FastAPI, "
                "or mainly other stacks?"
            )

    patch["stage"] = next_stage
    if next_stage == STAGE_2 and stage == STAGE_1 and screening >= 2:
        # Include assessment offer transition on the following turn only;
        # stay at stage_2 marker so next inbound uses stage 2 ask.
        pass

    return _authorised(
        reply,
        stage=next_stage,
        count=count + 1,
        action="chat",
        patch=patch,
    )


def build_follow_up(applicant: dict[str, Any]) -> ChatResult | None:
    """
    Single 48h follow-up after at least one candidate reply. Never for unanswered outreach.
    """
    stage = _normalize_stage(applicant.get("stage"))
    if stage in TERMINAL_STAGES or applicant.get("follow_up_sent") or applicant.get("opted_out"):
        return None
    if applicant.get("handoff_reason"):
        return None
    if int(applicant.get("message_count") or 0) < 1:
        return None
    last_in = applicant.get("last_freelancer_message_at")
    last_out = applicant.get("last_bot_message_at")
    if not last_in:
        return None
    try:
        last_in_dt = datetime.fromisoformat(str(last_in).replace("Z", "+00:00"))
    except ValueError:
        return None
    if _now() - last_in_dt < timedelta(hours=48):
        return None
    # Only if we already replied at least once and candidate has not written since
    if last_out:
        try:
            last_out_dt = datetime.fromisoformat(str(last_out).replace("Z", "+00:00"))
            if last_in_dt > last_out_dt:
                # They wrote after our last message — not an unanswered thread
                return None
        except ValueError:
            pass
    return _authorised(
        FOLLOW_UP_LINE,
        stage=stage,
        count=int(applicant.get("message_count") or 0) + 1,
        action="follow_up",
        patch={"follow_up_sent": True},
        unlocked=bool(applicant.get("github_unlocked")),
        github_username=applicant.get("github_username"),
    )
