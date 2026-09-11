"""Scrape the Postfach and print what the bot sees. Never writes to the DB, never replies.

  PYTHONPATH=. python3 scripts/list_conversations.py              # list rows only
  PYTHONPATH=. python3 scripts/list_conversations.py --read       # also open each thread
  PYTHONPATH=. python3 scripts/list_conversations.py --all        # ignore project filter
"""

import argparse
import asyncio
import logging

from src.config import PROJECT_TITLE
from src.freelancermap.browser import close_browser_context, get_browser_context
from src.freelancermap.login import login
from src.freelancermap.messages import fetch_all_conversations, find_messages_url

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")


async def main(read: bool, all_projects: bool, headless: bool) -> None:
    context = await get_browser_context(headless=headless)
    try:
        page = await login(context)
        url = await find_messages_url(page)
        if not url:
            raise SystemExit("Could not open the messages page")

        rows = await fetch_all_conversations(
            page,
            url,
            project_title=None if all_projects else PROJECT_TITLE,
            read_threads=read,
        )

        print(f"\n=== {len(rows)} conversations ===")
        for r in rows:
            print(f"\nkey={r.message_id}  conversation_id={r.conversation_id}")
            print(f"  name    : {r.sender_name}")
            print(f"  project : {r.project_title}")
            print(f"  role    : {r.headline}")
            print(f"  time    : {r.sent_at}   unread={r.unread}")
            if read:
                body = (r.preview or "").replace("\n", "\n            ")
                print(f"  message : {body[:600]}")
        if not rows:
            print("(nothing matched — try --all to ignore the project filter)")
    finally:
        await close_browser_context(context)


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("--read", action="store_true", help="Open each thread (marks as read)")
    p.add_argument("--all", action="store_true", help="Ignore the project title filter")
    p.add_argument("--headless", action="store_true")
    args = p.parse_args()
    asyncio.run(main(read=args.read, all_projects=args.all, headless=args.headless))
