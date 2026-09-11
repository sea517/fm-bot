import logging
import re
from dataclasses import dataclass
from pathlib import Path
import json

from playwright.async_api import Page

from src.config import DATA_DIR, FREELANCERMAP_BASE_URL, FREELANCERMAP_MESSAGES_URL

logger = logging.getLogger(__name__)

DISCOVERY_FILE = DATA_DIR / "site_discovery.json"

MESSAGE_URL_CANDIDATES = [
    url
    for url in [
        FREELANCERMAP_MESSAGES_URL,
        f"{FREELANCERMAP_BASE_URL}/postfach",
        f"{FREELANCERMAP_BASE_URL}/account/messages",
        f"{FREELANCERMAP_BASE_URL}/account/nachrichten",
        f"{FREELANCERMAP_BASE_URL}/inbox",
    ]
    if url
]

EMAIL_RE = re.compile(r"[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}")


# Real Postfach markup (verified against data/pobox_dom.html):
#   row     : div.pobox-message-preview[data-conversation-id="4774053"]
#   project : .headline          time : .created-date
#   name    : .image-row span    role : .end-content .pobox-ellipsis
#   unread  : class contains "unread"
ROW_SELECTOR = "div.pobox-message-preview[data-conversation-id]"
REPLY_INPUT = "#pobox-reply-footer-textarea"
SEND_BUTTON = "[data-id='pobox-message-footer-reply-send']"
THREAD_CONTAINER = ".pobox-message-body .items-container"


def conversation_key(conversation_id: str) -> str:
    """Stable DB key for a freelancermap conversation, e.g. 'fm-4774053'."""
    return f"fm-{str(conversation_id).strip()}"


@dataclass
class ChatMessage:
    message_id: str
    sender_name: str | None
    email: str | None
    preview: str
    project_id: str | None
    is_from_candidate: bool
    conversation_id: str | None = None
    project_title: str | None = None
    headline: str | None = None
    sent_at: str | None = None
    unread: bool = False


def _load_discovered_url() -> str | None:
    if not DISCOVERY_FILE.exists():
        return None
    try:
        data = json.loads(DISCOVERY_FILE.read_text(encoding="utf-8"))
        url = data.get("recommended_messages_url")
        if url and "kontakt.html" not in url and "404" not in url:
            return url
    except Exception:
        pass
    return None


async def find_messages_url(page: Page) -> str | None:
    # Prefer explicit .env URL — do not reject for keyword mismatch (EN/DE UI differ)
    if FREELANCERMAP_MESSAGES_URL:
        try:
            await page.goto(FREELANCERMAP_MESSAGES_URL, wait_until="domcontentloaded", timeout=20000)
            await page.wait_for_timeout(1000)
            if "login" not in page.url.lower() and "404" not in (await page.title()):
                logger.info("Messages page (from .env): %s", page.url)
                return page.url
            logger.warning(
                "FREELANCERMAP_MESSAGES_URL did not open inbox (url=%s title=%s)",
                page.url,
                await page.title(),
            )
        except Exception as e:
            logger.warning("Failed to open FREELANCERMAP_MESSAGES_URL: %s", e)

    candidates = [u for u in MESSAGE_URL_CANDIDATES if u != FREELANCERMAP_MESSAGES_URL]
    discovered = _load_discovered_url()
    if discovered and discovered not in candidates:
        candidates.insert(0, discovered)

    keywords = (
        "postfach",
        "nachricht",
        "bewerbung",
        "konversation",
        "inbox",
        "pobox",
        "message",
        "mailbox",
        "conversation",
    )
    for url in candidates:
        try:
            await page.goto(url, wait_until="domcontentloaded", timeout=20000)
            await page.wait_for_timeout(1000)
        except Exception:
            continue
        if "login" in page.url.lower():
            continue
        title = await page.title()
        if "404" in title:
            continue
        body = await page.inner_text("body")
        if any(kw in body.lower() for kw in keywords):
            logger.info("Messages page: %s", page.url)
            return page.url
    return None


async def _text_of(row, selector: str) -> str | None:
    node = row.locator(selector).first
    try:
        if await node.count() == 0:
            return None
        value = (await node.inner_text()).strip()
        return value or None
    except Exception:
        return None


async def _scroll_conversation_list(page: Page, max_rounds: int = 12) -> None:
    """Postfach lazy-loads rows; scroll the last row into view until the count settles."""
    previous = -1
    for _ in range(max_rounds):
        count = await page.locator(ROW_SELECTOR).count()
        if count == previous:
            return
        previous = count
        try:
            await page.locator(ROW_SELECTOR).last.scroll_into_view_if_needed(timeout=4000)
        except Exception:
            return
        await page.wait_for_timeout(700)


ROW_EXTRACT_JS = """
(selector) => [...document.querySelectorAll(selector)].map(el => {
    const t = (sel) => {
        const n = el.querySelector(sel);
        return n && n.innerText ? n.innerText.trim() : null;
    };
    return {
        id: el.getAttribute("data-conversation-id"),
        name: t(".image-row span"),
        project: t(".headline"),
        role: t(".end-content .pobox-ellipsis"),
        time: t(".created-date"),
        unread: (el.className || "").includes("unread"),
    };
})
"""


async def scrape_conversation_rows(page: Page, messages_url: str) -> list[dict]:
    """
    Read every row's metadata in one DOM pass.

    Opening a conversation hides the list, which invalidates row locators, so all
    metadata is collected up front instead of per-row while navigating.
    """
    await page.goto(messages_url, wait_until="domcontentloaded")
    try:
        await page.wait_for_selector(ROW_SELECTOR, timeout=15000)
    except Exception:
        logger.warning("No conversation rows (%s) on %s", ROW_SELECTOR, page.url)
        return []
    await _scroll_conversation_list(page)

    raw = await page.evaluate(ROW_EXTRACT_JS, ROW_SELECTOR)

    rows: list[dict] = []
    seen: set[str] = set()
    for item in raw:
        cid = (item.get("id") or "").strip()
        if not cid or cid in seen:
            continue
        seen.add(cid)
        rows.append(item)

    logger.info("Found %d conversation rows", len(rows))
    return rows


async def fetch_all_conversations(
    page: Page,
    messages_url: str,
    project_title: str | None = None,
    read_threads: bool = True,
    only_conversation_ids: set[str] | None = None,
    read_conversation_ids: set[str] | None = None,
    rows: list[dict] | None = None,
) -> list[ChatMessage]:
    """
    Scrape the Postfach list, then open only the threads we need.

    read_conversation_ids:
      If set, only those conversation ids are opened (even when read_threads=True).
      Pass an empty set to skip all thread opens after the list scrape.
      If None, every matched row is opened (legacy / test behaviour).

    rows:
      Optional pre-scraped list rows (avoids a second list scrape).
    """
    if rows is None:
        rows = await scrape_conversation_rows(page, messages_url)

    messages: list[ChatMessage] = []
    to_read: list[ChatMessage] = []

    for item in rows:
        cid = (item.get("id") or "").strip()
        name = item.get("name")
        proj = item.get("project")

        if not name:
            logger.debug("Row %s has no sender name, skipping", cid)
            continue

        if only_conversation_ids is not None and cid not in only_conversation_ids:
            logger.info("Skipping conversation %s (%s) — not the test target", cid, name)
            continue

        if project_title and proj and not _same_project(proj, project_title):
            logger.info(
                "Skipping conversation %s (%s) — project %r != %r",
                cid, name, proj, project_title,
            )
            continue

        msg = ChatMessage(
            message_id=conversation_key(cid),
            conversation_id=cid,
            sender_name=name[:100],
            email=None,
            preview=item.get("role") or "",
            project_id=None,
            project_title=proj,
            headline=item.get("role"),
            sent_at=item.get("time"),
            unread=bool(item.get("unread")),
            is_from_candidate=True,
        )
        messages.append(msg)
        if read_threads and (
            read_conversation_ids is None or cid in read_conversation_ids
        ):
            to_read.append(msg)
        logger.info(
            "Conversation id=%s name=%s unread=%s open=%s",
            cid,
            name,
            msg.unread,
            msg in to_read,
        )

    if to_read:
        logger.info(
            "Opening %d / %d freelancermap threads",
            len(to_read),
            len(messages),
        )
    else:
        logger.info(
            "List scrape only — not opening any of %d freelancermap threads",
            len(messages),
        )

    for msg in to_read:
        body = await read_conversation(page, msg.conversation_id, messages_url)
        if body:
            msg.preview = body
        found = EMAIL_RE.search(msg.preview or "")
        msg.email = found.group(0) if found else None

    return messages


def _same_project(row_title: str, project_title: str) -> bool:
    def norm(value: str) -> str:
        return re.sub(r"[^a-z0-9]+", "", value.lower())

    a, b = norm(row_title), norm(project_title)
    if not a or not b:
        return True
    return a in b or b in a


async def _open_conversation(
    page: Page,
    conversation_id: str,
    messages_url: str | None = None,
) -> bool:
    selector = f"{ROW_SELECTOR}[data-conversation-id='{conversation_id}']"

    for attempt in (1, 2):
        row = page.locator(selector).first
        visible = False
        if await row.count() > 0:
            try:
                visible = await row.is_visible()
            except Exception:
                visible = False

        # A previously opened conversation leaves the list hidden; reload to restore it.
        if not visible and messages_url and attempt == 1:
            logger.debug("Row %s hidden, reloading list", conversation_id)
            await page.goto(messages_url, wait_until="domcontentloaded")
            try:
                await page.wait_for_selector(ROW_SELECTOR, timeout=15000)
            except Exception:
                pass
            await _scroll_conversation_list(page)
            continue

        if not visible:
            logger.warning("Conversation row %s not visible", conversation_id)
            return False

        try:
            await row.scroll_into_view_if_needed(timeout=5000)
            await row.click(timeout=8000)
        except Exception as e:
            logger.warning("Could not open conversation %s: %s", conversation_id, e)
            return False

        try:
            await page.wait_for_selector(THREAD_CONTAINER, timeout=10000)
        except Exception:
            pass
        await page.wait_for_timeout(1200)
        return True

    return False


async def read_conversation(
    page: Page,
    conversation_id: str,
    messages_url: str | None = None,
) -> str | None:
    """Open a conversation and return the newest message text from the thread."""
    if not await _open_conversation(page, conversation_id, messages_url):
        return None

    container = page.locator(THREAD_CONTAINER).first
    if await container.count() == 0:
        return None
    try:
        text = (await container.inner_text()).strip()
    except Exception:
        return None
    if not text:
        return None

    noise_contains = (
        "möchten sie die konversation",
        "in den papierkorb",
        "gesendete anhänge",
        "keine anhänge vorhanden",
        "keine konversation ausgewählt",
        "wählen sie eine konversation",
    )
    # Action buttons rendered inside the thread; they are not part of the message.
    noise_exact = (
        "ablehnen",
        "antworten",
        "antwort",
        "weiterleiten",
        "zurück",
        "mehr anzeigen",
        "übersetzen",
    )

    lines = []
    for raw in text.splitlines():
        line = raw.strip()
        if not line:
            continue
        low = line.lower()
        if any(n in low for n in noise_contains):
            continue
        if low in noise_exact:
            continue
        lines.append(line)

    joined = "\n".join(lines)
    if not joined:
        return None
    # Keep the newest end of long threads — truncating from the start drops
    # the candidate's latest reply after our bot message.
    max_chars = 12000
    if len(joined) > max_chars:
        joined = joined[-max_chars:]
    return joined


async def send_reply(page: Page, messages_url: str, message_id: str, text: str) -> bool:
    """message_id is the DB key ('fm-4774053') or a raw conversation id."""
    conversation_id = str(message_id).replace("fm-", "").strip()
    if not conversation_id.isdigit():
        logger.warning("Refusing to reply — %r is not a conversation id", message_id)
        return False

    if not await _open_conversation(page, conversation_id, messages_url):
        return False

    field = await _focus_reply_field(page)
    if field is None:
        logger.warning("No reply input for conversation %s", conversation_id)
        return False

    if not await _type_reply(page, field, text):
        logger.warning("Could not enter reply text for conversation %s", conversation_id)
        return False

    send_btn = page.locator(SEND_BUTTON).first
    if await send_btn.count() == 0:
        send_btn = page.locator(
            "div[role='button']:has-text('Senden'), button:has-text('Senden')"
        ).first
    if await send_btn.count() == 0:
        logger.warning("No send button for conversation %s", conversation_id)
        return False

    if not await _click_send(send_btn):
        logger.warning("Could not click send for conversation %s", conversation_id)
        return False

    await page.wait_for_timeout(2500)

    # The footer clears on a successful send; a still-populated field means it failed.
    try:
        leftover = (await field.input_value()).strip()
    except Exception:
        leftover = ""
    if leftover:
        logger.warning(
            "Reply box still holds text after send for conversation %s", conversation_id
        )
        return False

    logger.info("Reply sent to conversation %s (%d chars)", conversation_id, len(text))
    return True


async def _type_reply(page: Page, field, text: str) -> bool:
    """
    Put text into the reply field.

    A single-line <input> silently drops newlines, so collapse them for that case.
    fill() is tried first; if the overlay blocks it, the value is set via JS with
    a native setter so React's onChange still fires.
    """
    is_textarea = False
    try:
        is_textarea = (
            await field.evaluate("el => el.tagName.toLowerCase()")
        ) == "textarea"
    except Exception:
        pass

    payload = text
    if not is_textarea and "\n" in text:
        logger.warning("Reply field is single-line — collapsing %d newlines", text.count("\n"))
        payload = " ".join(line.strip() for line in text.splitlines() if line.strip())

    try:
        await field.fill(payload, timeout=8000)
    except Exception as e:
        logger.debug("fill() failed (%s), setting value via JS", e)
        try:
            await field.evaluate(
                """(el, value) => {
                    const proto = el instanceof HTMLTextAreaElement
                        ? HTMLTextAreaElement.prototype
                        : HTMLInputElement.prototype;
                    const setter = Object.getOwnPropertyDescriptor(proto, 'value').set;
                    setter.call(el, value);
                    el.dispatchEvent(new Event('input', { bubbles: true }));
                    el.dispatchEvent(new Event('change', { bubbles: true }));
                }""",
                payload,
            )
        except Exception as e2:
            logger.warning("Could not set reply text: %s", e2)
            return False

    await page.wait_for_timeout(500)
    try:
        current = (await field.input_value()).strip()
    except Exception:
        return True
    if not current:
        logger.warning("Reply field is empty after typing")
        return False
    return True


async def _click_send(send_btn) -> bool:
    """.send-reply is a div; the message body can overlay it, so escalate."""
    for method in ("normal", "force", "dispatch"):
        try:
            if method == "normal":
                await send_btn.click(timeout=6000)
            elif method == "force":
                await send_btn.click(timeout=6000, force=True)
            else:
                await send_btn.dispatch_event("click")
            return True
        except Exception as e:
            logger.debug("send click (%s) failed: %s", method, e)
    return False


async def _focus_reply_field(page: Page):
    """
    Expand and focus the reply footer, then return the field to type into.

    The collapsed footer holds <input id=pobox-reply-footer-textarea>. A plain
    click() is unreliable because .pobox-message-body overlays the footer and
    intercepts pointer events, so focus()/JS focus is used instead — neither
    performs a pointer hit-test. Focusing expands the footer into a real
    textarea, which is preferred because <input> cannot carry newlines.
    """
    reply_input = page.locator(REPLY_INPUT).first
    if await reply_input.count() == 0:
        logger.warning("Reply input %s not present", REPLY_INPUT)
        return None

    focused = False
    try:
        await reply_input.focus(timeout=5000)
        focused = True
    except Exception as e:
        logger.debug("focus() failed (%s), trying JS focus", e)

    if not focused:
        try:
            await reply_input.evaluate("el => el.focus()")
            focused = True
        except Exception as e:
            logger.debug("JS focus failed (%s), trying forced click", e)

    if not focused:
        try:
            await reply_input.click(timeout=5000, force=True)
            focused = True
        except Exception as e:
            logger.warning("Could not focus reply input: %s", e)
            return None

    await page.wait_for_timeout(900)

    textarea = page.locator(
        ".pobox-message-footer textarea, textarea#pobox-reply-footer-textarea"
    ).first
    try:
        if await textarea.count() > 0 and await textarea.is_visible():
            return textarea
    except Exception:
        pass

    return reply_input
