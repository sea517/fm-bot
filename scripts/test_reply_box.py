"""Check that the reply box can be focused and typed into. Never clicks Send.

  PYTHONPATH=. python3 scripts/test_reply_box.py 4774542
"""

import argparse
import asyncio
import logging

from src.freelancermap.browser import close_browser_context, get_browser_context
from src.freelancermap.login import login
from src.freelancermap.messages import (
    REPLY_INPUT,
    SEND_BUTTON,
    _focus_reply_field,
    _open_conversation,
    _type_reply,
    find_messages_url,
)

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
logger = logging.getLogger(__name__)

PROBE = "TEST DRAFT - not sent"


async def main(conversation_id: str, headless: bool) -> None:
    context = await get_browser_context(headless=headless)
    try:
        page = await login(context)
        url = await find_messages_url(page)
        if not url:
            raise SystemExit("Could not open the messages page")

        if not await _open_conversation(page, conversation_id, url):
            raise SystemExit(f"Could not open conversation {conversation_id}")
        print(f"opened conversation : {conversation_id}")

        field = await _focus_reply_field(page)
        if field is None:
            raise SystemExit("FAIL — reply field could not be focused")
        tag = await field.evaluate("el => el.tagName.toLowerCase()")
        print(f"reply field         : <{tag}>  (textarea = multi-line OK)")

        ok = await _type_reply(page, field, PROBE)
        value = await field.input_value()
        print(f"typing accepted     : {ok}")
        print(f"field now contains  : {value!r}")

        send = page.locator(SEND_BUTTON).first
        print(f"send button found   : {await send.count() > 0}")

        # leave the box clean
        try:
            await field.fill("")
        except Exception:
            await field.evaluate("el => { el.value = ''; }")
        print("cleared draft       : yes")

        if ok and value.strip():
            print("\nRESULT: PASS — the reply box works, send was not clicked")
        else:
            print("\nRESULT: FAIL — text did not land in the box")

        input("\nPress Enter to close the browser...")
    finally:
        await close_browser_context(context)


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("conversation_id")
    p.add_argument("--headless", action="store_true")
    args = p.parse_args()
    asyncio.run(main(args.conversation_id, headless=args.headless))
