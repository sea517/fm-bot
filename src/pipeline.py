import asyncio
import json
import logging
import re
import time
from datetime import datetime, timezone

from sqlalchemy import func, select

from src.conversation import stages as S
from src.conversation.handler import (
    EMAIL_STAGES,
    FM_STAGES,
    SLACK_STAGES,
    build_reply,
    is_action_due,
    on_candidate_message,
    on_resume_email,
    on_slack_email_reply,
)
from src.conversation.timing import (
    hours_later,
    schedule_after_candidate_message,
    schedule_slack_after_message,
)
from src.ai.llm import active_model
from src.config import DATA_DIR, POLL_INTERVAL_SECONDS, PROJECT_TITLE, active_ai_provider
from src.db.models import Applicant, SessionLocal, init_db
from src.db.projects import sync_active_project_from_env
from src.freelancermap.browser import close_browser_context, get_browser_context
from src.freelancermap.health import fm_record_failure, fm_record_success, fm_should_attempt
from src.freelancermap.login import login
from src.freelancermap.messages import (
    conversation_key,
    fetch_all_conversations,
    find_messages_url,
    scrape_conversation_rows,
    send_reply,
)
from src.mail.inbox import (
    fetch_new_emails,
    inbox_configured,
    is_system_notification,
    mark_processed,
    send_email_reply,
)
from src.slack.chat import (
    CHANNEL_GONE,
    candidate_has_joined_channel,
    clear_rate_limit_exhausted,
    is_marked_slack_channel,
    latest_human_message,
    post_slack_message,
    rate_limit_exhausted,
)
from src.supabase_contacts import sync_applicant_to_supabase, upsert_contacted_freelancer

logger = logging.getLogger(__name__)

LAST_FM_POLL_FILE = DATA_DIR / "last_fm_poll.txt"

# Still on freelancermap after reply 2 — resume/file shares may arrive already-read.
FM_THREAD_WATCH_STAGES = {
    S.STAGE_WAITING_REPLY_3,
}

ACTIVE_STAGES = (
    FM_STAGES
    | EMAIL_STAGES
    | SLACK_STAGES
    | {
        S.STAGE_NEW,
        S.STAGE_WAITING_EMAIL_RESUME,
        S.STAGE_WAITING_JOIN,
        S.STAGE_WAITING_AVAILABILITY,
        S.STAGE_WAITING_INTRO,
    }
)


def _fm_conversation_id(applicant: Applicant) -> str | None:
    mid = (applicant.freelancermap_message_id or "").strip()
    if mid.startswith("fm-"):
        return mid[3:]
    return None


def _read_last_fm_poll() -> datetime | None:
    try:
        raw = LAST_FM_POLL_FILE.read_text(encoding="utf-8").strip()
        if not raw:
            return None
        return datetime.fromisoformat(raw)
    except Exception:
        return None


def _write_last_fm_poll() -> None:
    LAST_FM_POLL_FILE.parent.mkdir(parents=True, exist_ok=True)
    LAST_FM_POLL_FILE.write_text(
        datetime.now(timezone.utc).isoformat(), encoding="utf-8"
    )


def _any_due_fm_sends(session) -> bool:
    applicants = session.execute(
        select(Applicant).where(
            Applicant.stage.in_(FM_STAGES | {S.STAGE_NEW}),
            Applicant.status != "paused",
        )
    ).scalars().all()
    return any(is_action_due(a) for a in applicants)


def should_poll_freelancermap(
    session,
    *,
    force: bool = False,
    only_id: int | None = None,
    only_name: str | None = None,
    only_conversation: str | None = None,
) -> bool:
    """
    Full freelancermap list scrape + selective thread opens.

    Do this when forced (chat-once), under a test filter, or when
    POLL_INTERVAL_SECONDS have passed since the last scrape.

    Pending FM *sends* (confirm prompts left unanswered) must NOT force a
    scrape every 60s — those only need a browser login to deliver.
    """
    if force or only_id is not None or only_name or only_conversation:
        return True
    last = _read_last_fm_poll()
    if last is None:
        return True
    if last.tzinfo is None:
        last = last.replace(tzinfo=timezone.utc)
    age = (datetime.now(timezone.utc) - last).total_seconds()
    return age >= POLL_INTERVAL_SECONDS


def select_fm_threads_to_open(session, rows: list[dict]) -> set[str]:
    """
    Only open threads that can have new work:
      - unread list rows,
      - conversations not yet in the DB (new applicants),
      - applicants still waiting on FM reply 2/3 (resume may arrive already-read).
    """
    existing = {
        mid
        for (mid,) in session.execute(
            select(Applicant.freelancermap_message_id)
        ).all()
        if mid
    }
    watch: set[str] = set()
    for applicant in session.execute(
        select(Applicant).where(
            Applicant.stage.in_(FM_THREAD_WATCH_STAGES),
            Applicant.status != "paused",
        )
    ).scalars():
        cid = _fm_conversation_id(applicant)
        if cid:
            watch.add(cid)

    to_open: set[str] = set()
    for item in rows:
        cid = (item.get("id") or "").strip()
        if not cid:
            continue
        key = conversation_key(cid)
        unread = bool(item.get("unread"))
        if unread or key not in existing or cid in watch:
            to_open.add(cid)
    return to_open


def _due_applicants(session) -> list[Applicant]:
    """Load active applicants and keep only those whose action is due now."""
    applicants = session.execute(
        select(Applicant).where(
            Applicant.stage.in_(ACTIVE_STAGES),
            Applicant.status != "paused",
            Applicant.next_action_at.is_not(None),
        )
    ).scalars().all()
    due: list[Applicant] = []
    for applicant in applicants:
        if is_action_due(applicant):
            due.append(applicant)
    return due


def _matches_only_filter(
    applicant: Applicant,
    only_id: int | None = None,
    only_name: str | None = None,
    only_conversation: str | None = None,
) -> bool:
    if only_id is None and not only_name and not only_conversation:
        return True
    if only_id is not None and applicant.id == only_id:
        return True
    if only_conversation and applicant.freelancermap_message_id == conversation_key(
        only_conversation
    ):
        return True
    if only_name and applicant.name and only_name.lower() in applicant.name.lower():
        return True
    return False


def _normalize_thread_text(text: str) -> str:
    return " ".join((text or "").replace("\xa0", " ").split()).strip().lower()


def _known_bot_texts(applicant: Applicant) -> list[str]:
    try:
        log = json.loads(applicant.conversation_log or "[]")
    except json.JSONDecodeError:
        return []
    return [
        (entry.get("text") or "").strip()
        for entry in log
        if entry.get("role") == "bot" and (entry.get("text") or "").strip()
    ]


def _strip_fm_chrome(norm_text: str) -> str:
    """Remove freelancermap UI crumbs that survive a thread re-scrape."""
    text = norm_text
    text = re.sub(r"\b\d{2}\.\d{2}\.\d{2}\s+\d{1,2}:\d{2}\s*uhr\b", " ", text)
    for token in (
        "ablehnen",
        "antworten",
        "antwort",
        "weiterleiten",
        "zurück",
        "mehr anzeigen",
        "übersetzen",
        "gesendete anhänge",
        "keine anhänge vorhanden",
    ):
        text = text.replace(token, " ")
    # Keep .pdf / .doc names — they are often the only signal of a resume share.
    return " ".join(text.split()).strip()


_RESUME_FILE_RE = re.compile(
    r"\b[\w.-]+\.(?:pdf|docx?|rtf|odt)\b",
    re.IGNORECASE,
)
_RESUME_WORD_RE = re.compile(
    r"\b(?:resume|cv|lebenslauf|curriculum)\b",
    re.IGNORECASE,
)


def _resume_files(text: str) -> set[str]:
    return {m.group(0).lower() for m in _RESUME_FILE_RE.finditer(text or "")}


def _looks_like_resume_share(text: str) -> bool:
    t = text or ""
    if _RESUME_FILE_RE.search(t):
        return True
    if _RESUME_WORD_RE.search(t) and len(t.strip()) < 400:
        return True
    return False


def _extract_text_after_last_bot(applicant: Applicant, preview: str) -> str:
    """
    Return the part of the scraped thread that comes after our last bot reply.
    That is where a real candidate follow-up lives.
    """
    text = (preview or "").strip()
    if not text:
        return ""
    bots = _known_bot_texts(applicant)
    if not bots:
        return text
    last_bot = bots[-1]
    idx = text.rfind(last_bot)
    if idx < 0:
        # Whitespace / nbsp differences — try normalized search via sliding.
        norm_bot = _normalize_thread_text(last_bot)
        norm_text = _normalize_thread_text(text)
        nidx = norm_text.rfind(norm_bot)
        if nidx < 0:
            return text
        # Approximate cut: keep the raw tail proportional to the normalized cut.
        ratio = (nidx + len(norm_bot)) / max(len(norm_text), 1)
        cut = min(len(text), int(len(text) * ratio))
        return text[cut:].strip()
    return text[idx + len(last_bot) :].strip()


def _is_genuine_new_candidate_message(applicant: Applicant, msg) -> tuple[bool, str]:
    """
    freelancermap thread scrapes re-read the whole conversation. Small DOM/text
    differences must not look like a new candidate reply — that was resetting
    the 10‑minute timers and inventing follow-ups when nobody answered.

    Returns (is_new, text_to_use). text_to_use is only the new candidate part
    when possible (after our last bot reply), not the whole thread.
    """
    empty = (False, "")
    # Still owe the first bot reply: never treat scrape churn as a new message.
    if applicant.stage in (S.STAGE_NEW, S.STAGE_WAITING_REPLY_1):
        return empty

    # Only these FM stages wait for the candidate to write again.
    if applicant.stage not in (S.STAGE_WAITING_REPLY_2, S.STAGE_WAITING_REPLY_3):
        return empty

    preview = (getattr(msg, "preview", None) or "").strip()
    if not preview:
        return empty

    after_bot = _extract_text_after_last_bot(applicant, preview)
    candidate_part = (after_bot or "").strip()
    if not candidate_part:
        return empty

    last = (applicant.last_candidate_message or "").strip()
    unread = bool(getattr(msg, "unread", False))

    # Resume / file share after our last reply — often short ("hier" + .pdf) and
    # may already be marked read by the time we poll.
    new_files = _resume_files(candidate_part) - _resume_files(last)
    if new_files or (
        _looks_like_resume_share(candidate_part)
        and _normalize_thread_text(candidate_part) != _normalize_thread_text(last)
    ):
        logger.info(
            "Detected FM resume/file share for id=%s files=%s",
            applicant.id,
            sorted(new_files) or ["keyword"],
        )
        return True, candidate_part

    # List row unread is the normal signal for a new free-text reply.
    if not unread:
        return empty

    norm_part = _strip_fm_chrome(_normalize_thread_text(candidate_part))
    if not norm_part:
        return empty

    norm_last = _strip_fm_chrome(_normalize_thread_text(last))
    if norm_part == norm_last:
        return empty
    if norm_last and norm_last in norm_part:
        extra = " ".join(norm_part.replace(norm_last, " ", 1).split()).strip()
        if len(extra) < 20:
            return empty
        return True, candidate_part

    if len(norm_part) >= 20:
        return True, candidate_part
    return empty


def get_or_create_applicant(
    session,
    msg,
    only_id: int | None = None,
    only_name: str | None = None,
    only_conversation: str | None = None,
) -> Applicant:
    existing = session.execute(
        select(Applicant).where(Applicant.freelancermap_message_id == msg.message_id)
    ).scalar_one_or_none()

    if existing:
        if (
            _matches_only_filter(existing, only_id, only_name, only_conversation)
            and existing.status != "paused"
            and msg.preview
        ):
            is_new, new_text = _is_genuine_new_candidate_message(existing, msg)
            if is_new and new_text:
                logger.info(
                    "Genuine new FM message from id=%s name=%s (%d chars)",
                    existing.id,
                    existing.name,
                    len(new_text),
                )
                on_candidate_message(existing, new_text, S.CHANNEL_FREELANCERMAP)
                existing.message_preview = msg.preview
            elif getattr(msg, "unread", False) and existing.stage in (
                S.STAGE_WAITING_REPLY_2,
                S.STAGE_WAITING_REPLY_3,
            ):
                logger.info(
                    "Unread FM conversation id=%s name=%s but no new candidate text "
                    "after our last reply — waiting",
                    existing.id,
                    existing.name,
                )
                existing.message_preview = msg.preview
            elif msg.preview != existing.message_preview:
                # Keep the latest scrape for debugging, but do not reschedule.
                existing.message_preview = msg.preview
        return existing

    applicant = Applicant(
        freelancermap_message_id=msg.message_id,
        freelancermap_project_id=msg.project_id,
        name=msg.sender_name,
        email=msg.email,
        message_preview=msg.preview,
        stage=S.STAGE_NEW,
        last_candidate_message=msg.preview,
        last_candidate_message_at=datetime.now(timezone.utc),
        next_action_at=schedule_after_candidate_message(msg.preview),
        reply_channel=S.CHANNEL_FREELANCERMAP,
    )
    session.add(applicant)
    session.flush()

    # Single-applicant test mode: store others but do not schedule replies
    if not _matches_only_filter(applicant, only_id, only_name, only_conversation):
        applicant.status = "paused"
        applicant.next_action_at = None
        logger.info("Paused applicant id=%s name=%s (test filter active)", applicant.id, applicant.name)
        return applicant

    on_candidate_message(applicant, msg.preview, S.CHANNEL_FREELANCERMAP)
    logger.info("New applicant id=%s", applicant.id)
    # Ledger: seen on freelancermap (contacted=False until we send a reply).
    upsert_contacted_freelancer(
        fm_conversation_id=applicant.freelancermap_message_id,
        display_name=applicant.name,
        email=applicant.email,
        project_title=getattr(msg, "project_title", None) or getattr(msg, "headline", None),
        local_applicant_id=applicant.id,
        stage=applicant.stage,
        contacted=False,
    )
    return applicant


def _match_applicant(session, from_email: str, from_name: str | None) -> Applicant | None:
    if from_email:
        found = session.execute(
            select(Applicant).where(func.lower(Applicant.email) == from_email.lower())
        ).scalars().all()
        if found:
            return _prefer_waiting(found)
        found = session.execute(
            select(Applicant).where(func.lower(Applicant.slack_invite_email) == from_email.lower())
        ).scalars().all()
        if found:
            return _prefer_waiting(found)
    if from_name:
        name = from_name.strip().lower()
        if name:
            candidates = session.execute(
                select(Applicant).where(Applicant.stage == S.STAGE_WAITING_EMAIL_RESUME)
            ).scalars().all()
            for a in candidates:
                if a.name and a.name.strip().lower() == name:
                    return a
            for a in candidates:
                if a.name and name in a.name.strip().lower():
                    return a
    waiting = session.execute(
        select(Applicant).where(Applicant.stage == S.STAGE_WAITING_EMAIL_RESUME)
        .order_by(Applicant.updated_at.desc())
    ).scalars().all()
    return waiting[0] if len(waiting) == 1 else None


def _prefer_waiting(applicants: list[Applicant]) -> Applicant:
    for stage in (
        S.STAGE_WAITING_SLACK_EMAIL,
        S.STAGE_WAITING_EMAIL_RESUME,
        S.STAGE_WAITING_EMAIL_PROCESS,
    ):
        for a in applicants:
            if a.stage == stage:
                return a
    return applicants[0]


def ingest_oliver_inbox(
    session,
    only_id: int | None = None,
    only_name: str | None = None,
    only_conversation: str | None = None,
) -> None:
    if not inbox_configured():
        logger.warning("Skipping Oliver inbox — set INBOX_EMAIL and INBOX_PASSWORD")
        return
    try:
        emails = fetch_new_emails()
    except Exception:
        logger.exception(
            "Oliver inbox fetch failed — continuing without new mail this cycle"
        )
        return
    logger.info("Oliver inbox: %d new messages", len(emails))
    for item in emails:
        if is_system_notification(item):
            logger.info(
                "Ignoring system email from %s subject=%r",
                item.from_email,
                (item.subject or "")[:80],
            )
            mark_processed(item.message_id)
            continue

        applicant = _match_applicant(session, item.from_email, item.from_name)
        if applicant and not _matches_only_filter(
            applicant, only_id, only_name, only_conversation
        ):
            logger.info("Skipping email from %s (test filter)", item.from_email)
            mark_processed(item.message_id)
            continue
        preview = (item.body or item.subject or "")[:800]

        if applicant and applicant.stage == S.STAGE_WAITING_SLACK_EMAIL:
            on_slack_email_reply(applicant, preview, item.from_email)
            applicant.email_message_id = item.message_id
            mark_processed(item.message_id)
            logger.info("Slack-email reply from %s applicant=%s", item.from_email, applicant.id)
            continue

        if item.has_resume or (applicant and applicant.stage == S.STAGE_WAITING_EMAIL_RESUME):
            if not applicant:
                if only_id is not None or only_name or only_conversation:
                    mark_processed(item.message_id)
                    continue
                # Never invent an applicant from noreply / empty real people
                from_addr = (item.from_email or "").lower()
                if (
                    not from_addr
                    or "noreply@" in from_addr
                    or "no-reply@" in from_addr
                    or "mailer-daemon@" in from_addr
                ):
                    logger.info("Ignoring resume-like mail from automated address %s", from_addr)
                    mark_processed(item.message_id)
                    continue
                applicant = Applicant(
                    freelancermap_message_id=f"email:{item.message_id}",
                    name=item.from_name,
                    email=item.from_email,
                    message_preview=preview,
                    stage=S.STAGE_WAITING_EMAIL_RESUME,
                    reply_channel=S.CHANNEL_EMAIL,
                )
                session.add(applicant)
                session.flush()
            on_resume_email(applicant, preview, item.from_email, item.message_id)
            mark_processed(item.message_id)
            continue

        mark_processed(item.message_id)


def ingest_slack_joins(session) -> None:
    """
    When a candidate accepts the Slack Connect invite and appears in the
    private channel, schedule Oliver's welcome / availability ask — do not
    wait for them to say hello first.
    """
    waiting = session.execute(
        select(Applicant).where(
            Applicant.stage == S.STAGE_WAITING_JOIN,
            Applicant.slack_channel_id.is_not(None),
            Applicant.status != "paused",
        )
    ).scalars().all()
    for applicant in waiting:
        if rate_limit_exhausted():
            break
        if applicant.next_action_at is not None:
            continue
        channel_id = applicant.slack_channel_id
        if is_marked_slack_channel(channel_id):
            logger.info(
                "Skipping Slack join watch for applicant=%s — channel marked",
                applicant.id,
            )
            continue
        if not candidate_has_joined_channel(channel_id):
            time.sleep(0.2)
            continue
        applicant.reply_channel = S.CHANNEL_SLACK
        # Short natural pause after join detection, then greet first.
        applicant.next_action_at = schedule_slack_after_message(
            "hi", require_business_hours=False
        )
        logger.info(
            "Candidate joined Slack channel %s — scheduling welcome for applicant=%s (%s)",
            channel_id,
            applicant.id,
            applicant.name,
        )
        time.sleep(0.2)


def _mark_slack_channel_gone(applicant: Applicant, reason: str = "channel_deleted") -> None:
    """Stop all Slack work for an applicant whose private channel is gone."""
    logger.info(
        "Closing Slack track for applicant=%s (%s) channel=%s — %s",
        applicant.id,
        applicant.name,
        applicant.slack_channel_id,
        reason,
    )
    applicant.stage = S.STAGE_COMPLETED
    applicant.status = reason
    applicant.next_action_at = None
    applicant.slack_channel_id = None


def ingest_slack_replies(
    session,
    only_id: int | None = None,
    only_name: str | None = None,
    only_conversation: str | None = None,
) -> None:
    clear_rate_limit_exhausted()
    ingest_slack_joins(session)

    applicants = session.execute(
        select(Applicant).where(
            Applicant.slack_channel_id.is_not(None),
            Applicant.stage.in_(SLACK_STAGES | {S.STAGE_WAITING_INTRO}),
        )
    ).scalars().all()
    for applicant in applicants:
        if rate_limit_exhausted():
            logger.warning(
                "Stopping Slack poll early — rate limit exhausted; remaining channels next cycle"
            )
            break
        if not _matches_only_filter(applicant, only_id, only_name, only_conversation):
            continue
        if is_marked_slack_channel(applicant.slack_channel_id):
            logger.info(
                "Skipping Slack poll for applicant=%s — channel marked",
                applicant.id,
            )
            continue
        text, ts = latest_human_message(applicant.slack_channel_id, applicant.slack_last_ts)
        if text == CHANNEL_GONE:
            _mark_slack_channel_gone(applicant)
            continue
        if not text or not ts:
            # Brief pause between channels to stay under Slack's request budget.
            time.sleep(0.25)
            continue
        if text == applicant.last_candidate_message:
            time.sleep(0.25)
            continue
        on_candidate_message(applicant, text, S.CHANNEL_SLACK)
        applicant.slack_last_ts = ts
        preview = text if len(text) <= 200 else text[:200] + "…"
        logger.info(
            "Slack reply applicant=%s stage=%s read %d chars:\n%s",
            applicant.id,
            applicant.stage,
            len(text),
            preview,
        )
        time.sleep(0.25)


def _replace_last_bot_log(applicant: Applicant, text: str) -> None:
    """build_reply logged the generated text; swap in what was actually approved."""
    try:
        log = json.loads(applicant.conversation_log or "[]")
    except json.JSONDecodeError:
        return
    for entry in reversed(log):
        if entry.get("role") == "bot":
            entry["text"] = text
            applicant.conversation_log = json.dumps(log)
            return


def _read_multiline(prompt: str) -> str:
    print(prompt)
    lines: list[str] = []
    while True:
        try:
            line = input()
        except EOFError:
            break
        if line.strip() == "END":
            break
        lines.append(line)
    return "\n".join(lines).strip()


async def _confirm_reply(action, applicant: Applicant) -> str | None:
    """
    Ask before sending. Returns the text to send, or None to skip.

    Runs input() on a worker thread so the Playwright event loop keeps its
    connection alive while waiting for you.
    """
    candidate_msg = (applicant.last_candidate_message or "").strip()
    if len(candidate_msg) > 500:
        candidate_preview = candidate_msg[:500] + "…"
    else:
        candidate_preview = candidate_msg or "(none yet)"
    ai_summary = getattr(applicant, "ai_read_summary", None)

    print("\n" + "=" * 70)
    print(f"REVIEW — {action.channel} reply")
    print(f"  applicant : id={applicant.id} {applicant.name or '-'}")
    print(f"  conv      : {applicant.freelancermap_message_id or '-'}")
    print(f"  stage     : {applicant.stage}")
    if applicant.stage == S.STAGE_GITHUB_INVITED and applicant.github_username:
        print(
            f"  github    : {applicant.github_username} "
            "(invite runs when you press y, before Slack send)"
        )
    print("-" * 70)
    print("Applicant message (what the bot read):")
    print(candidate_preview)
    if ai_summary:
        print("-" * 70)
        print("DeepSeek recognition:")
        print(ai_summary)
    print("-" * 70)
    print("Proposed bot reply:")
    print(action.text)
    print("=" * 70)

    while True:
        answer = (
            await asyncio.to_thread(
                input, "Send it? [y] send  [n] skip  [e] edit then send: "
            )
        ).strip().lower()

        if answer in ("y", "yes", ""):
            return action.text
        if answer in ("n", "no", "s", "skip"):
            return None
        if answer in ("e", "edit"):
            edited = await asyncio.to_thread(
                _read_multiline,
                "\nType the replacement message. Finish with a line containing only END:",
            )
            if not edited:
                print("Empty message — not sending.")
                return None
            print("\n--- will send ---")
            print(edited)
            print("--- end ---")
            return edited
        print("Please answer y, n, or e.")


async def process_due_applicant(
    session,
    page,
    messages_url: str,
    applicant: Applicant,
    confirm: bool = False,
) -> None:
    if applicant.status == "paused":
        return
    # Human-owned Slack channels (name contains -marked-) — never poll or reply.
    if applicant.slack_channel_id and is_marked_slack_channel(applicant.slack_channel_id):
        if applicant.next_action_at is not None:
            applicant.next_action_at = None
            session.commit()
        logger.info(
            "Skipping due action for applicant=%s — Slack channel marked",
            applicant.id,
        )
        return
    if not is_action_due(applicant):
        # is_action_due may push Slack work to the next Mon–Fri window
        session.commit()
        return

    try:
        action = build_reply(applicant)
    except Exception:
        # A Gemini/API failure must not abort the whole poll cycle. Leave the
        # applicant untouched so the same step is retried on the next poll.
        session.rollback()
        logger.exception(
            "Could not build reply for applicant=%s (%s) — will retry next poll",
            applicant.id,
            applicant.name,
        )
        return

    if not action.text or action.channel == S.CHANNEL_NONE:
        session.commit()
        return

    if confirm:
        approved = await _confirm_reply(action, applicant)
        if approved is None:
            # build_reply already advanced stage/next_action_at in memory; undo it
            # so the applicant is retried unchanged on the next poll.
            session.rollback()
            logger.info(
                "Skipped %s reply for applicant=%s (not approved)",
                action.channel,
                applicant.id,
            )
            return
        if approved != action.text:
            _replace_last_bot_log(applicant, approved)
        action.text = approved

    # Invite before Slack so "Invited you…" is true when the message goes out.
    # Confirm skip never reaches here, so a wrong parse cannot invite.
    if applicant.stage == S.STAGE_GITHUB_INVITED:
        from src.github.invite import extract_github_username, invite_to_repo

        username = (applicant.github_username or "").strip().lstrip("@")
        # Guard: never invite with a Slack member id / empty parse.
        if not username or extract_github_username(username) != username:
            logger.error(
                "GitHub invite skipped for applicant=%s — invalid username=%r; "
                "reverting to waiting_github",
                applicant.id,
                applicant.github_username,
            )
            applicant.github_username = None
            applicant.stage = S.STAGE_WAITING_GITHUB
            applicant.next_action_at = None
            session.commit()
            return
        if not invite_to_repo(username):
            # Do not spin forever on a bad username / API 404.
            bad = username
            session.rollback()
            applicant = session.get(Applicant, applicant.id)
            if applicant is None:
                return
            applicant.github_username = None
            applicant.stage = S.STAGE_WAITING_GITHUB
            applicant.next_action_at = None
            session.commit()
            logger.error(
                "GitHub invite failed for applicant=%s username=%s — "
                "reverted to waiting_github (will not retry invite automatically)",
                applicant.id,
                bad,
            )
            return
        applicant.github_invited_at = datetime.now(timezone.utc)

    logger.info(
        "Sending %s reply to applicant=%s name=%s conv=%s stage=%s:\n--- begin ---\n%s\n--- end ---",
        action.channel,
        applicant.id,
        applicant.name,
        applicant.freelancermap_message_id,
        applicant.stage,
        action.text,
    )

    ok = False
    if action.channel == S.CHANNEL_FREELANCERMAP:
        if page is None or not messages_url:
            logger.warning(
                "Freelancermap unavailable — deferring reply for applicant=%s",
                applicant.id,
            )
            session.rollback()
            return
        ok = await send_reply(page, messages_url, applicant.freelancermap_message_id, action.text)
        if ok:
            sync_applicant_to_supabase(applicant, contacted=True)
    elif action.channel == S.CHANNEL_EMAIL:
        if not applicant.email:
            logger.warning("No email for applicant=%s", applicant.id)
            return
        ok = send_email_reply(
            applicant.email,
            action.text,
            in_reply_to=applicant.email_message_id,
        )
    elif action.channel == S.CHANNEL_SLACK:
        if not applicant.slack_channel_id:
            logger.warning("No Slack channel for applicant=%s", applicant.id)
            return
        posted_ts = post_slack_message(applicant.slack_channel_id, action.text)
        if posted_ts == CHANNEL_GONE:
            _mark_slack_channel_gone(applicant)
            session.commit()
            return
        ok = bool(posted_ts)
        if posted_ts and posted_ts != "0":
            # Only read applicant messages after this bot reply on future polls.
            applicant.slack_last_ts = posted_ts

    if ok:
        applicant.chat_reply = action.text
        applicant.updated_at = datetime.now(timezone.utc)
        if applicant.stage == S.STAGE_GITHUB_INVITED:
            applicant.stage = S.STAGE_ASSIGNMENT_NOTIFIED
            # build_reply may already have deferred the check to an agreed slot.
            if applicant.next_action_at is None:
                applicant.next_action_at = hours_later(3)
            logger.info(
                "Assignment check scheduled for applicant=%s github=%s at %s",
                applicant.id,
                applicant.github_username,
                applicant.next_action_at.isoformat()
                if applicant.next_action_at
                else "?",
            )
        session.commit()
        logger.info("Sent %s reply stage=%s applicant=%s", action.channel, applicant.stage, applicant.id)
    else:
        # Undo the stage/schedule advance from build_reply — nothing was delivered,
        # so this step must be retried unchanged on the next poll.
        session.rollback()
        logger.warning(
            "Send failed channel=%s applicant=%s — rolled back, will retry next poll",
            action.channel,
            applicant.id,
        )


async def run_chat_pipeline(
    headless: bool = True,
    only_id: int | None = None,
    only_name: str | None = None,
    only_conversation: str | None = None,
    confirm: bool = False,
    force_fm_poll: bool | None = None,
    channels: str = "all",
) -> None:
    """
    channels:
      "all"   — freelancermap + Oliver email + Slack (normal 3h cycle)
      "slack" — Slack assessment only (60s cycle while tech process is active)
    """
    init_db()
    sync_active_project_from_env()
    if only_id or only_name or only_conversation:
        logger.info(
            "TEST MODE: only_id=%s only_name=%s only_conversation=%s",
            only_id, only_name, only_conversation,
        )
    if confirm:
        logger.info("CONFIRM MODE: every reply needs your approval before sending")
    logger.info(
        "AI provider: %s (%s) | channels=%s",
        active_ai_provider(),
        active_model(),
        channels,
    )

    slack_only = channels == "slack"

    # chat-once always hits freelancermap; chat-loop full cycles only when due.
    if force_fm_poll is None:
        force_fm_poll = not slack_only

    session = SessionLocal()
    context = None
    page = None
    messages_url = ""
    try:
        if slack_only:
            logger.info(
                "Slack check only — freelancermap and private email not touched this minute"
            )
        else:
            poll_fm = should_poll_freelancermap(
                session,
                force=force_fm_poll,
                only_id=only_id,
                only_name=only_name,
                only_conversation=only_conversation,
            )

            if poll_fm:
                if not fm_should_attempt():
                    page = None
                    messages_url = ""
                else:
                    try:
                        context = await get_browser_context(headless=headless)
                        page = await login(context)
                        messages_url = await find_messages_url(page)
                        if not messages_url:
                            raise RuntimeError(
                                "Set FREELANCERMAP_MESSAGES_URL or run discover --headed"
                            )

                        rows = await scrape_conversation_rows(page, messages_url)
                        to_open = select_fm_threads_to_open(session, rows)
                        if only_conversation:
                            to_open.add(only_conversation)
                        logger.info(
                            "Freelancermap: %d list rows, opening %d threads",
                            len(rows),
                            len(to_open),
                        )

                        conversations = await fetch_all_conversations(
                            page,
                            messages_url,
                            project_title=PROJECT_TITLE,
                            only_conversation_ids=(
                                {only_conversation} if only_conversation else None
                            ),
                            read_conversation_ids=to_open,
                            rows=rows,
                        )
                        logger.info(
                            "Polled %d freelancermap conversations", len(conversations)
                        )

                        for msg in conversations:
                            get_or_create_applicant(
                                session,
                                msg,
                                only_id=only_id,
                                only_name=only_name,
                                only_conversation=only_conversation,
                            )
                        _write_last_fm_poll()
                        fm_record_success()
                    except Exception as exc:
                        fm_record_failure(exc)
                        logger.exception(
                            "Freelancermap unavailable — continuing with email + Slack"
                        )
                        if context is not None:
                            try:
                                await close_browser_context(context)
                            except Exception:
                                logger.exception("Error closing browser after FM failure")
                            context = None
                        page = None
                        messages_url = ""
            else:
                logger.info(
                    "Skipping freelancermap scrape this cycle "
                    "(last scrape within %ss)",
                    POLL_INTERVAL_SECONDS,
                )

            # Full run (no --only-*): unpause anyone left paused from a previous
            # single-applicant test so they are included automatically.
            if not only_id and not only_name and not only_conversation:
                paused = session.execute(
                    select(Applicant).where(Applicant.status == "paused")
                ).scalars().all()
                for applicant in paused:
                    applicant.status = "active"
                    logger.info(
                        "Unpaused applicant id=%s name=%s",
                        applicant.id,
                        applicant.name,
                    )

            session.commit()

            ingest_oliver_inbox(
                session,
                only_id=only_id,
                only_name=only_name,
                only_conversation=only_conversation,
            )
            session.commit()

        ingest_slack_replies(
            session,
            only_id=only_id,
            only_name=only_name,
            only_conversation=only_conversation,
        )
        session.commit()

        due = _due_applicants(session)
        # is_action_due may push Slack work to the next business window.
        session.commit()

        if slack_only:
            due = [
                a
                for a in due
                if a.stage in SLACK_STAGES
                or a.reply_channel == S.CHANNEL_SLACK
                or a.stage
                in (
                    S.STAGE_WAITING_JOIN,
                    S.STAGE_WAITING_AVAILABILITY,
                    S.STAGE_WAITING_START,
                    S.STAGE_WAITING_READY,
                    S.STAGE_WAITING_INTRO,
                    S.STAGE_WAITING_CHANNEL_CLEANUP,
                )
            ]

        logger.info("Due applicants this cycle: %d", len(due))

        needs_browser_for_send = (not slack_only) and any(
            a.stage in (FM_STAGES | {S.STAGE_NEW})
            or (a.reply_channel == S.CHANNEL_FREELANCERMAP)
            for a in due
            if _matches_only_filter(a, only_id, only_name, only_conversation)
        )
        if needs_browser_for_send and page is None and fm_should_attempt():
            try:
                context = await get_browser_context(headless=headless)
                page = await login(context)
                messages_url = await find_messages_url(page) or ""
                if not messages_url:
                    raise RuntimeError(
                        "Set FREELANCERMAP_MESSAGES_URL or run discover --headed"
                    )
                fm_record_success()
            except Exception as exc:
                fm_record_failure(exc)
                logger.exception(
                    "Freelancermap login failed for due sends — "
                    "deferring FM replies, continuing Slack/email"
                )
                if context is not None:
                    try:
                        await close_browser_context(context)
                    except Exception:
                        logger.exception("Error closing browser after FM send-login failure")
                    context = None
                page = None
                messages_url = ""

        for applicant in due:
            if not _matches_only_filter(
                applicant, only_id, only_name, only_conversation
            ):
                continue
            # Without a browser, skip freelancermap-only due work this cycle.
            if page is None and (
                applicant.stage in (FM_STAGES | {S.STAGE_NEW})
                or applicant.reply_channel == S.CHANNEL_FREELANCERMAP
            ):
                logger.info(
                    "Deferring freelancermap due applicant=%s (no FM session)",
                    applicant.id,
                )
                continue
            await process_due_applicant(
                session, page, messages_url, applicant, confirm=confirm
            )
    finally:
        session.close()
        if context is not None:
            await close_browser_context(context)
