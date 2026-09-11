import argparse
import asyncio
import logging
import time
from pathlib import Path

from src.config import (
    CONTROL_HOST,
    CONTROL_PORT,
    FEED_PUBLIC_URL,
    FREELANCERMAP_EMAIL,
    GEMINI_API_KEY,
    INBOX_EMAIL,
    INBOX_PASSWORD,
    OPENROUTER_API_KEY,
    POLL_INTERVAL_SECONDS,
    SLACK_BOT_TOKEN,
    SLACK_OFF_HOURS_POLL_SECONDS,
    SLACK_POLL_INTERVAL_SECONDS,
    SLACK_TEAM_ID,
    SLACK_USER_TOKEN,
    active_ai_provider,
)
from src.ai.llm import active_model
from sqlalchemy import select

from src.db.models import Applicant, SessionLocal, init_db
from src.feed.add_job import add_job, read_description
from src.feed.generator import generate_feed
from src.feed.server import main as run_feed_server
from src.feed.post_from_env import post_project_from_env
from src.pipeline import run_chat_pipeline

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)


def cmd_feed_generate() -> None:
    path = generate_feed()
    logger.info("Feed written to %s", path)


def cmd_feed_serve() -> None:
    run_feed_server()


def cmd_control_serve() -> None:
    import uvicorn

    from src.control.app import app

    logger.info("Control dashboard on http://%s:%s/", CONTROL_HOST, CONTROL_PORT)
    uvicorn.run(app, host=CONTROL_HOST, port=CONTROL_PORT, log_level="info")


def cmd_post_project() -> None:
    post_project_from_env()
    logger.info("Project posted from .env → jobs.json + feed.xml")


def cmd_post_job(args) -> None:
    description = read_description(args.description, args.description_file)
    job = add_job(
        title=args.title,
        description=description,
        proj_type=args.type,
        proj_duration=args.duration,
        proj_country=args.country,
        proj_city=args.city,
        contact_email=args.contact_email or FREELANCERMAP_EMAIL,
        contact_forename=args.contact_forename,
        contact_lastname=args.contact_lastname,
        external_id=args.external_id,
    )
    logger.info("Job added: %s — %s", job.external_id, job.proj_title)


def cmd_discover(args) -> None:
    from src.freelancermap.discover import discover

    asyncio.run(discover(headless=not args.headed))


def cmd_sync_supabase() -> None:
    from src.config import SUPABASE_URL
    from src.supabase_contacts import supabase_configured, sync_all_contacted_from_db

    init_db()
    if not supabase_configured():
        logger.error(
            "Set SUPABASE_URL and SUPABASE_SERVICE_ROLE_KEY in .env, "
            "and run supabase/contacted_freelancers.sql first."
        )
        return
    session = SessionLocal()
    try:
        ok, skipped = sync_all_contacted_from_db(session)
        logger.info(
            "Supabase sync done — upserted=%s skipped=%s (url=%s)",
            ok,
            skipped,
            SUPABASE_URL,
        )
    finally:
        session.close()


def cmd_outreach(args) -> None:
    from src.freelancermap.outreach import run_outreach_session

    asyncio.run(
        run_outreach_session(
            headless=not args.headed,
            limit=args.limit,
            dry_run=args.dry_run,
            project_name=args.project or "",
        )
    )


def cmd_status() -> None:
    init_db()
    session = SessionLocal()
    try:
        applicants = session.execute(
            select(Applicant).order_by(Applicant.created_at.desc()).limit(20)
        ).scalars().all()
        print("\n=== Bot status ===")
        print(f"freelancermap email: {'set' if FREELANCERMAP_EMAIL else 'MISSING'}")
        print(f"Oliver inbox:        {'set' if INBOX_EMAIL and INBOX_PASSWORD else 'MISSING'}")
        provider = active_ai_provider()
        key_ok = bool(OPENROUTER_API_KEY) if provider == "openrouter" else bool(GEMINI_API_KEY)
        print(f"AI provider:         {provider} ({active_model()})")
        print(f"AI key:              {'set' if key_ok else 'MISSING'}")
        slack_ok = (SLACK_BOT_TOKEN or SLACK_USER_TOKEN) and SLACK_TEAM_ID
        print(f"Slack configured:    {'yes' if slack_ok else 'no'}")
        if slack_ok:
            print(
                f"Slack posts as:      "
                f"{'Oliver (user token)' if SLACK_USER_TOKEN else 'Kontrora app — set SLACK_USER_TOKEN'}"
            )
        print(f"Feed public URL:     {FEED_PUBLIC_URL or 'not set'}")
        print(f"\nRecent applicants ({len(applicants)}):")
        for a in applicants:
            due = a.next_action_at.isoformat() if a.next_action_at else "-"
            print(
                f"  id={a.id} [{a.status}] conv={a.freelancermap_message_id or '-'} "
                f"stage={a.stage} name={a.name or '-'} email={a.email or '-'} due={due}"
            )
        if not applicants:
            print("  (none yet)")
        print("\nTest all applicants (ask before every send):")
        print("  PYTHONPATH=. python3 -m src.main chat-loop --headed --confirm")
        print("\nLive (no prompts):")
        print("  PYTHONPATH=. python3 -m src.main chat-loop --headed")
        print("\nTest one applicant:")
        print("  PYTHONPATH=. python3 -m src.main chat-loop --headed --confirm --only-conversation 4774542")
    finally:
        session.close()


def cmd_history(args) -> None:
    """Print the stored message-by-message log for one applicant."""
    import json

    init_db()
    session = SessionLocal()
    try:
        query = select(Applicant)
        if args.conversation:
            query = query.where(
                Applicant.freelancermap_message_id == f"fm-{args.conversation}"
            )
        elif args.id:
            query = query.where(Applicant.id == args.id)
        applicants = session.execute(query).scalars().all()

        if not applicants:
            print("No matching applicant.")
            return

        for a in applicants:
            print(f"\n=== id={a.id} conv={a.freelancermap_message_id} {a.name or '-'} ===")
            print(f"stage={a.stage} status={a.status} channel={a.reply_channel or '-'}")
            print(f"next_action_at={a.next_action_at or '-'}")
            try:
                log = json.loads(a.conversation_log or "[]")
            except json.JSONDecodeError:
                log = []
            if not log:
                print("(no messages logged yet)")
            for entry in log:
                who = "CANDIDATE" if entry.get("role") == "candidate" else "BOT"
                print(f"\n[{entry.get('at', '?')}] {who}:")
                print(entry.get("text", ""))
    finally:
        session.close()


def cmd_cleanup(args) -> None:
    """Delete rows that are not real conversations (UI elements scraped by mistake)."""
    init_db()
    session = SessionLocal()
    try:
        applicants = session.execute(select(Applicant)).scalars().all()
        junk = [
            a
            for a in applicants
            if not (a.freelancermap_message_id or "").startswith(("fm-", "email:"))
        ]
        if not junk:
            print("No junk applicants found.")
            return
        print(f"Junk applicants ({len(junk)}):")
        for a in junk:
            print(f"  id={a.id} key={a.freelancermap_message_id} name={a.name}")
        if not args.yes:
            print("\nRe-run with --yes to delete these rows.")
            return
        for a in junk:
            session.delete(a)
        session.commit()
        print(f"\nDeleted {len(junk)} junk rows.")
    finally:
        session.close()


async def cmd_login(headless: bool) -> None:
    from src.freelancermap.browser import close_browser_context, get_browser_context
    from src.freelancermap.login import login

    context = await get_browser_context(headless=headless)
    try:
        page = await login(context)
        logger.info("Logged in at: %s", page.url)
    finally:
        await close_browser_context(context)


async def cmd_chat_once(
    headless: bool,
    only_id: int | None = None,
    only_name: str | None = None,
    only_conversation: str | None = None,
    confirm: bool = False,
) -> None:
    await run_chat_pipeline(
        headless=headless,
        only_id=only_id,
        only_name=only_name,
        only_conversation=only_conversation,
        confirm=confirm,
        force_fm_poll=True,
    )


async def cmd_chat_loop(
    headless: bool,
    only_id: int | None = None,
    only_name: str | None = None,
    only_conversation: str | None = None,
    confirm: bool = False,
) -> None:
    """
    Poll cadence:
      - Mon–Fri 10:00–17:00 America/New_York: Slack every SLACK_POLL_INTERVAL_SECONDS
        (20s) while assessments are active; freelancermap+email every POLL_INTERVAL
      - Outside that window: Slack + freelancermap + email every
        SLACK_OFF_HOURS_POLL_SECONDS (default 3h) — no 20s Slack loop overnight
    """
    from src.conversation.timing import is_slack_business_hours

    last_full_poll_at = 0.0

    while True:
        in_hours = is_slack_business_hours()
        slack_active = _has_active_slack_applicants()
        email_due_in = _seconds_until_next_email_action()
        email_due_now = email_due_in is not None and email_due_in <= 0
        now = time.time()
        full_interval = (
            POLL_INTERVAL_SECONDS if in_hours else SLACK_OFF_HOURS_POLL_SECONDS
        )
        full_due = (now - last_full_poll_at) >= full_interval

        try:
            # Fast Slack-only polls only during the EDT assessment window.
            # If an Oliver-email Slack invite is due, run a full cycle instead.
            if in_hours and slack_active and not full_due and not email_due_now:
                logger.info(
                    "Slack check (10:00–17:00 EDT) — "
                    "skipping freelancermap and private email"
                )
                await run_chat_pipeline(
                    headless=headless,
                    only_id=only_id,
                    only_name=only_name,
                    only_conversation=only_conversation,
                    confirm=confirm,
                    force_fm_poll=False,
                    channels="slack",
                )
            else:
                if email_due_now:
                    logger.info(
                        "Email action due — freelancermap + private email + Slack"
                    )
                elif in_hours:
                    logger.info(
                        "Full check — freelancermap + private email + Slack"
                    )
                else:
                    logger.info(
                        "Off-hours check (outside 10:00–17:00 EDT) — "
                        "freelancermap + private email + Slack"
                    )
                await run_chat_pipeline(
                    headless=headless,
                    only_id=only_id,
                    only_name=only_name,
                    only_conversation=only_conversation,
                    confirm=confirm,
                    force_fm_poll=False,
                    channels="all",
                )
                last_full_poll_at = time.time()
        except Exception:
            logger.exception("Chat pipeline error")

        in_hours = is_slack_business_hours()
        slack_active = _has_active_slack_applicants()
        email_due_in = _seconds_until_next_email_action()
        if in_hours and slack_active:
            sleep_for = SLACK_POLL_INTERVAL_SECONDS
            next_full_in = max(
                0, int(POLL_INTERVAL_SECONDS - (time.time() - last_full_poll_at))
            )
            logger.info(
                "Sleeping %ds until next Slack check "
                "(next freelancermap+email check in %ds)",
                sleep_for,
                next_full_in,
            )
        elif in_hours:
            sleep_for = POLL_INTERVAL_SECONDS
            logger.info(
                "Sleeping %ds until next full check "
                "(no active Slack assessments)",
                sleep_for,
            )
        else:
            sleep_for = SLACK_OFF_HOURS_POLL_SECONDS
            logger.info(
                "Sleeping %ds until next off-hours check "
                "(Slack only every 3h outside 10:00–17:00 EDT)",
                sleep_for,
            )

        # Don't strand Slack-invite emails behind the long off-hours sleep.
        if email_due_in is not None:
            wake = 20 if email_due_in <= 0 else max(20, int(email_due_in) + 5)
            if wake < sleep_for:
                logger.info(
                    "Email action due in %.0fs — shortening sleep to %ds",
                    email_due_in,
                    wake,
                )
                sleep_for = wake

        time.sleep(sleep_for)


def _has_active_slack_applicants() -> bool:
    from src.conversation.handler import SLACK_STAGES
    from src.conversation import stages as S

    session = SessionLocal()
    try:
        # Cleanup only needs the normal poll cadence (48h timer) — not 60s.
        stages = list(
            (SLACK_STAGES | {S.STAGE_WAITING_JOIN, S.STAGE_WAITING_AVAILABILITY})
            - {S.STAGE_WAITING_CHANNEL_CLEANUP}
        )
        row = session.execute(
            select(Applicant.id).where(
                Applicant.status == "active",
                Applicant.stage.in_(stages),
                Applicant.slack_channel_id.is_not(None),
            ).limit(1)
        ).first()
        return row is not None
    finally:
        session.close()


def _seconds_until_next_email_action() -> float | None:
    """
    Seconds until the next due Oliver-email action (resume process / Slack invite).

    Used so off-hours 1h sleeps do not strand waiting_slack_email for an hour.
    """
    from datetime import datetime, timezone

    from src.conversation.handler import EMAIL_STAGES

    session = SessionLocal()
    try:
        rows = session.execute(
            select(Applicant.next_action_at).where(
                Applicant.status == "active",
                Applicant.stage.in_(list(EMAIL_STAGES)),
                Applicant.next_action_at.is_not(None),
            )
        ).all()
        now = datetime.now(timezone.utc)
        soonest: float | None = None
        for (due,) in rows:
            if due is None:
                continue
            if due.tzinfo is None:
                due = due.replace(tzinfo=timezone.utc)
            secs = (due - now).total_seconds()
            if soonest is None or secs < soonest:
                soonest = secs
        return soonest
    finally:
        session.close()


def main() -> None:
    parser = argparse.ArgumentParser(description="freelancermap bot")
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("feed-generate", help="Generate XML feed from data/jobs.json")
    sub.add_parser("feed-serve", help="Serve XML feed for freelancermap Enterprise import")
    sub.add_parser(
        "control-serve",
        help="Serve dashboard + Control API for Bot 1/2/3 (extensions claim jobs)",
    )
    sub.add_parser("post-project", help="Post job from PROJECT_* settings in .env")
    sub.add_parser("status", help="Show configuration and recent applicants")
    sub.add_parser(
        "sync-supabase",
        help="Push freelancermap applicants from local DB into Supabase contacted_freelancers",
    )

    cleanup = sub.add_parser("cleanup", help="Remove applicants that are not real conversations")
    cleanup.add_argument("--yes", action="store_true", help="Actually delete")

    history = sub.add_parser("history", help="Show the full message log for an applicant")
    history.add_argument("--id", type=int, help="DB applicant id")
    history.add_argument("--conversation", help="freelancermap conversation id, e.g. 4774542")

    discover = sub.add_parser("discover", help="Find freelancermap messages page URL")
    discover.add_argument("--headed", action="store_true", help="Show browser window")

    post_job = sub.add_parser("post-job", help="Add job with description to feed")
    post_job.add_argument("--title", required=True)
    post_job.add_argument("--description")
    post_job.add_argument("--description-file", type=Path)
    post_job.add_argument("--external-id")
    post_job.add_argument("--type", default="Remote", choices=["Remote", "Contract", "Permanent"])
    post_job.add_argument("--duration", type=int, default=1)
    post_job.add_argument("--country", default="Germany")
    post_job.add_argument("--city", default="Remote")
    post_job.add_argument("--contact-email", default="")
    post_job.add_argument("--contact-forename", default="Oliver")
    post_job.add_argument("--contact-lastname", default="")

    login_cmd = sub.add_parser("login", help="Log in to freelancermap")
    login_cmd.add_argument("--headless", action="store_true", help="Run without visible browser")

    outreach = sub.add_parser(
        "outreach-dm",
        help="Send outbound DMs from freelancermap.com/freelancer search",
    )
    outreach.add_argument(
        "--headed",
        action="store_true",
        help="Show the browser (recommended for first runs)",
    )
    outreach.add_argument(
        "--limit",
        type=int,
        default=5,
        help="Max freelancers to contact this run (default 5)",
    )
    outreach.add_argument(
        "--dry-run",
        action="store_true",
        help="Open Contact form and fill fields but do not click Send",
    )
    outreach.add_argument(
        "--project",
        default="",
        help="Project label in the Contact form dropdown (or set OUTREACH_PROJECT_NAME)",
    )

    for name in ("chat-once", "chat-loop"):
        cmd = sub.add_parser(
            name,
            help="Run one chat poll cycle" if name == "chat-once" else "Poll chat on interval",
        )
        cmd.add_argument("--headed", action="store_true")
        cmd.add_argument("--only-id", type=int, help="Process only this DB applicant id (small number from `status`)")
        cmd.add_argument(
            "--only-conversation",
            help="Process only this freelancermap conversation id (e.g. 4774542)",
        )
        cmd.add_argument("--only-name", help="Process only applicants whose name contains this")
        cmd.add_argument(
            "--confirm",
            action="store_true",
            help="Show each reply and ask for approval before sending",
        )

    args = parser.parse_args()

    if args.command == "login":
        asyncio.run(cmd_login(headless=getattr(args, "headless", False)))
        return

    if args.command == "outreach-dm":
        cmd_outreach(args)
        return

    headless = not getattr(args, "headed", False)
    only_id = getattr(args, "only_id", None)
    only_name = getattr(args, "only_name", None)
    only_conversation = getattr(args, "only_conversation", None)

    # freelancermap conversation ids are 7 digits; DB applicant ids are small counters.
    if only_id is not None and only_id >= 100000:
        logger.warning(
            "--only-id %s looks like a freelancermap conversation id, "
            "treating it as --only-conversation %s",
            only_id, only_id,
        )
        only_conversation = str(only_id)
        only_id = None

    if args.command == "feed-generate":
        cmd_feed_generate()
    elif args.command == "feed-serve":
        cmd_feed_serve()
    elif args.command == "control-serve":
        cmd_control_serve()
    elif args.command == "post-project":
        cmd_post_project()
    elif args.command == "post-job":
        cmd_post_job(args)
    elif args.command == "discover":
        cmd_discover(args)
    elif args.command == "cleanup":
        cmd_cleanup(args)
    elif args.command == "history":
        cmd_history(args)
    elif args.command == "status":
        cmd_status()
    elif args.command == "sync-supabase":
        cmd_sync_supabase()
    elif args.command == "chat-once":
        asyncio.run(
            cmd_chat_once(
                headless=headless,
                only_id=only_id,
                only_name=only_name,
                only_conversation=only_conversation,
                confirm=getattr(args, "confirm", False),
            )
        )
    elif args.command == "chat-loop":
        asyncio.run(
            cmd_chat_loop(
                headless=headless,
                only_id=only_id,
                only_name=only_name,
                only_conversation=only_conversation,
                confirm=getattr(args, "confirm", False),
            )
        )


if __name__ == "__main__":
    main()
