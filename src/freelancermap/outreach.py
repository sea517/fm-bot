"""
Outbound DMs on freelancermap.com freelancer search.

Flow:
  1) Open /freelancer search
  2) Open a profile card → click Contact
  3) Select project, fill subject + message, Send message
  4) Record contact in Supabase (skip if already contacted by name/id)
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass

from playwright.async_api import Locator, Page

from src.config import (
    DATA_DIR,
    OUTREACH_PROJECT_NAME,
    OUTREACH_SEARCH_URL,
)
from src.freelancermap.login import dismiss_cookie_banner, login
from src.freelancermap.outreach_templates import (
    OUTREACH_SUBJECT,
    render_outreach_body,
)
from src.supabase_contacts import (
    upsert_contacted_freelancer,
    was_contacted_before,
)

logger = logging.getLogger(__name__)

OUTREACH_DEBUG = DATA_DIR / "outreach_debug.png"


@dataclass
class OutreachResult:
    sent: int = 0
    skipped: int = 0
    failed: int = 0


def _outreach_id(profile_key: str) -> str:
    """Stable ledger key when Postfach conversation id is not known yet."""
    slug = re.sub(r"[^a-z0-9]+", "-", (profile_key or "").lower()).strip("-")
    return f"outreach-{slug[:80] or 'unknown'}"


def _pick_detail(title: str, location: str, extras: str) -> str:
    title = (title or "").strip()
    location = (location or "").strip()
    extras = (extras or "").strip()
    if title and location:
        return f"your work as {title} ({location})"
    if title:
        return f"your focus on {title}"
    if location:
        return f"your profile based in {location}"
    if extras:
        return extras[:120]
    return "your experience"


async def _visible(locator: Locator) -> bool:
    try:
        return await locator.count() > 0 and await locator.first.is_visible()
    except Exception:
        return False


async def _click_first(page: Page, selectors: list[str], *, timeout: int = 5000) -> bool:
    for sel in selectors:
        loc = page.locator(sel).first
        if not await _visible(loc):
            continue
        try:
            await loc.click(timeout=timeout)
            return True
        except Exception:
            try:
                await loc.click(timeout=timeout, force=True)
                return True
            except Exception:
                continue
    return False


async def _save_debug(page: Page, reason: str) -> None:
    try:
        DATA_DIR.mkdir(parents=True, exist_ok=True)
        await page.screenshot(path=str(OUTREACH_DEBUG), full_page=True)
        logger.error("Outreach debug (%s): screenshot=%s url=%s", reason, OUTREACH_DEBUG, page.url)
    except Exception:
        logger.exception("Could not save outreach debug screenshot")


async def _profile_cards(page: Page) -> Locator:
    # freelancermap.com search result cards — try several layouts.
    for sel in (
        "[data-testid='freelancer-card']",
        "article a[href*='/freelancer/']",
        "a[href*='/freelancer/']:has(h2), a[href*='/freelancer/']:has(h3)",
        ".freelancer-card, .profile-card, [class*='FreelancerCard']",
        "div[class*='result'] a[href*='/freelancer/']",
    ):
        loc = page.locator(sel)
        if await loc.count() > 0:
            return loc
    return page.locator("a[href*='/freelancer/']")


async def _read_open_profile(page: Page) -> dict[str, str]:
    """Read name/title/location from the open profile modal or page."""
    name = ""
    title = ""
    location = ""
    profile_key = page.url

    # Prefer modal dialog content.
    root = page.locator("[role='dialog'], .modal, [class*='Modal'], [class*='profile']").first
    scope = root if await _visible(root) else page

    for sel in ("h1", "h2", "[data-testid='freelancer-name']", ".profile-name"):
        loc = scope.locator(sel).first
        if await _visible(loc):
            text = (await loc.inner_text()).strip()
            if text and len(text) < 120:
                name = text
                break

    # Title often directly under the name.
    if name:
        try:
            body = await scope.inner_text()
        except Exception:
            body = ""
        lines = [ln.strip() for ln in body.splitlines() if ln.strip()]
        # Find name line then take following non-badge lines as title/location.
        for i, ln in enumerate(lines):
            if name.split()[0].lower() in ln.lower() and name.split()[-1].lower() in ln.lower():
                after = lines[i + 1 : i + 8]
                for cand in after:
                    low = cand.lower()
                    if low in {"verified", "premium member", "contact", "show contact details"}:
                        continue
                    if re.search(r"€\s*\d|/\$|\d+\s*%", cand):
                        continue
                    if re.search(r"updated|watchlist|add note", low):
                        continue
                    if not title and len(cand) > 8:
                        title = cand
                        continue
                    if title and not location and (
                        "," in cand
                        or any(
                            c in low
                            for c in (
                                "germany",
                                "hungary",
                                "spain",
                                "india",
                                "uk",
                                "united",
                                "france",
                                "italy",
                                "remote",
                            )
                        )
                    ):
                        location = cand
                        break
                break

    m = re.search(r"/freelancer/([^/?#]+)", page.url)
    if m:
        profile_key = m.group(1)

    return {
        "name": name,
        "title": title,
        "location": location,
        "profile_key": profile_key,
    }


async def _select_project(page: Page, project_name: str) -> bool:
    """Choose the outreach project in the Contact form dropdown."""
    # Native <select>
    native = page.locator("select").filter(
        has_text=re.compile(r"project|projekt", re.I)
    ).first
    if not await _visible(native):
        native = page.locator("form select, [role='dialog'] select").first
    if await _visible(native):
        try:
            if project_name:
                await native.select_option(label=re.compile(re.escape(project_name), re.I))
            else:
                # First real option after placeholder
                options = native.locator("option")
                count = await options.count()
                for i in range(count):
                    val = await options.nth(i).get_attribute("value")
                    label = (await options.nth(i).inner_text()).strip()
                    if val and label and "select" not in label.lower():
                        await native.select_option(index=i)
                        return True
            return True
        except Exception:
            logger.exception("Native project <select> failed")

    # Custom dropdown: click "Select project" then an option
    opened = await _click_first(
        page,
        [
            "text=Select project",
            "button:has-text('Select project')",
            "[aria-label*='project' i]",
            "div:has-text('Select project')",
        ],
    )
    if not opened:
        logger.warning("Could not open project dropdown")
        return False
    await page.wait_for_timeout(500)

    if project_name:
        opt = page.get_by_role("option", name=re.compile(re.escape(project_name), re.I))
        if await opt.count() == 0:
            opt = page.locator(f"text={project_name}").first
        if await _visible(opt):
            await opt.click()
            return True

    # Fall back: first option in listbox
    for sel in ("[role='option']", "li[role='option']", ".dropdown-item", "[class*='option']"):
        opts = page.locator(sel)
        if await opts.count() == 0:
            continue
        for i in range(min(await opts.count(), 8)):
            text = (await opts.nth(i).inner_text()).strip().lower()
            if not text or "select project" in text:
                continue
            await opts.nth(i).click()
            return True
    return False


async def _fill_contact_form(page: Page, *, subject: str, body: str) -> bool:
    # Subject
    subject_input = page.locator(
        "input[placeholder*='Subject' i], input[name*='subject' i], "
        "label:has-text('Subject') >> xpath=following::input[1]"
    ).first
    if not await _visible(subject_input):
        subject_input = page.get_by_label(re.compile(r"subject", re.I))
    try:
        await subject_input.fill(subject)
    except Exception:
        logger.exception("Could not fill subject")
        return False

    # Message body — label in UI is oddly also "Contact form"
    body_area = page.locator(
        "textarea[placeholder*='message' i], textarea[name*='message' i], "
        "textarea[name*='body' i], form textarea, [role='dialog'] textarea"
    ).first
    if not await _visible(body_area):
        body_area = page.get_by_label(re.compile(r"contact form|message", re.I))
    try:
        await body_area.fill(body)
    except Exception:
        logger.exception("Could not fill message body")
        return False
    return True


async def _send_message(page: Page) -> bool:
    return await _click_first(
        page,
        [
            "button:has-text('Send message')",
            "button:has-text('Nachricht senden')",
            "[type='submit']:has-text('Send')",
            "button:has-text('Send')",
        ],
    )


async def contact_open_profile(
    page: Page,
    *,
    dry_run: bool = False,
    project_name: str = "",
) -> bool:
    """
    Assumes a freelancer profile modal/page is open.
    Clicks Contact, fills the form, sends (unless dry_run).
    """
    info = await _read_open_profile(page)
    name = info.get("name") or ""
    if not name:
        logger.warning("Open profile has no readable name — skipping")
        await _save_debug(page, "no_name")
        return False

    profile_key = info.get("profile_key") or name
    ledger_id = _outreach_id(profile_key)

    if was_contacted_before(fm_conversation_id=ledger_id) or _name_contacted(name):
        logger.info("Skip already contacted: %s (%s)", name, ledger_id)
        return False

    clicked = await _click_first(
        page,
        [
            "button:has-text('Contact')",
            "[role='dialog'] button:has-text('Contact')",
            "a:has-text('Contact')",
            "button:has-text('Kontaktieren')",
            "button:has-text('Kontakt')",
        ],
    )
    if not clicked:
        await _save_debug(page, "contact_button_missing")
        logger.error("Contact button not found for %s", name)
        return False

    await page.wait_for_timeout(1200)

    # Wait for contact form
    try:
        await page.locator("textarea").first.wait_for(state="visible", timeout=15000)
    except Exception:
        await _save_debug(page, "contact_form_missing")
        logger.error("Contact form did not open for %s", name)
        return False

    project = project_name or OUTREACH_PROJECT_NAME
    if not await _select_project(page, project):
        await _save_debug(page, "project_select_failed")
        logger.error("Could not select project for %s (wanted=%r)", name, project)
        return False

    detail = _pick_detail(info.get("title") or "", info.get("location") or "", "")
    body = render_outreach_body(full_name=name, detail=detail)
    if not await _fill_contact_form(page, subject=OUTREACH_SUBJECT, body=body):
        await _save_debug(page, "fill_failed")
        return False

    if dry_run:
        logger.info(
            "DRY-RUN would send to %s detail=%r subject=%r",
            name,
            detail,
            OUTREACH_SUBJECT,
        )
        upsert_contacted_freelancer(
            fm_conversation_id=ledger_id,
            display_name=name,
            project_title=project or None,
            stage="outreach_dry_run",
            contacted=False,
        )
        # Close form without sending if possible
        await _click_first(page, ["button:has-text('Cancel')", "[aria-label='Close']", "button:has-text('×')"])
        return True

    if not await _send_message(page):
        await _save_debug(page, "send_failed")
        logger.error("Send message failed for %s", name)
        return False

    await page.wait_for_timeout(1500)
    upsert_contacted_freelancer(
        fm_conversation_id=ledger_id,
        display_name=name,
        project_title=project or None,
        stage="outreach_dm_sent",
        contacted=True,
    )
    logger.info("Outreach DM sent to %s (%s)", name, ledger_id)
    return True


def _name_contacted(name: str) -> bool:
    """Supabase lookup by display_name (case-insensitive)."""
    from src.config import SUPABASE_SERVICE_ROLE_KEY, SUPABASE_URL
    from src.supabase_contacts import supabase_configured
    import requests

    if not supabase_configured() or not (name or "").strip():
        return False
    try:
        response = requests.get(
            f"{SUPABASE_URL.rstrip('/')}/rest/v1/contacted_freelancers",
            headers={
                "apikey": SUPABASE_SERVICE_ROLE_KEY,
                "Authorization": f"Bearer {SUPABASE_SERVICE_ROLE_KEY}",
            },
            params={
                "display_name": f"ilike.{name.strip()}",
                "select": "id,first_contacted_at,last_contacted_at",
                "limit": "1",
            },
            timeout=20,
        )
        if not response.ok:
            return False
        rows = response.json()
        return bool(
            rows
            and (rows[0].get("first_contacted_at") or rows[0].get("last_contacted_at"))
        )
    except Exception:
        logger.exception("Name contacted lookup failed")
        return False


async def run_outreach(
    page: Page,
    *,
    limit: int = 5,
    dry_run: bool = False,
    project_name: str = "",
) -> OutreachResult:
    """
    From an already-logged-in page, run outbound DMs on the freelancer search.
    """
    result = OutreachResult()
    url = OUTREACH_SEARCH_URL
    logger.info("Opening outreach search: %s", url)
    await page.goto(url, wait_until="domcontentloaded", timeout=60000)
    await dismiss_cookie_banner(page)
    await page.wait_for_timeout(2000)

    cards = await _profile_cards(page)
    total = await cards.count()
    if total == 0:
        await _save_debug(page, "no_cards")
        logger.error("No freelancer cards found on %s", url)
        return result

    logger.info("Found %d freelancer link/card candidates", total)
    seen: set[str] = set()
    idx = 0
    while result.sent + result.failed < limit and idx < min(total, limit * 4):
        card = cards.nth(idx)
        idx += 1
        try:
            href = await card.get_attribute("href") or ""
            key = href or str(idx)
            if key in seen:
                result.skipped += 1
                continue
            seen.add(key)

            await card.scroll_into_view_if_needed()
            await card.click(timeout=8000)
            await page.wait_for_timeout(1500)

            ok = await contact_open_profile(
                page, dry_run=dry_run, project_name=project_name
            )
            if ok:
                result.sent += 1
            else:
                # Skipped (already contacted) vs failed — treat no-name/skip as skip
                result.skipped += 1

            # Close modal / go back to search
            closed = await _click_first(
                page,
                [
                    "[aria-label='Close']",
                    "button:has-text('Close')",
                    "[role='dialog'] button:has-text('×')",
                    "button.close",
                ],
            )
            if not closed:
                await page.keyboard.press("Escape")
            await page.wait_for_timeout(800)
            if "freelancer" not in page.url:
                await page.goto(url, wait_until="domcontentloaded", timeout=60000)
                await page.wait_for_timeout(1500)
                cards = await _profile_cards(page)
                total = await cards.count()
        except Exception:
            result.failed += 1
            logger.exception("Outreach card %s failed", idx)
            await _save_debug(page, f"card_{idx}_error")
            try:
                await page.keyboard.press("Escape")
            except Exception:
                pass

    logger.info(
        "Outreach finished sent=%s skipped=%s failed=%s",
        result.sent,
        result.skipped,
        result.failed,
    )
    return result


async def run_outreach_session(
    *,
    headless: bool = False,
    limit: int = 5,
    dry_run: bool = False,
    project_name: str = "",
) -> OutreachResult:
    from urllib.parse import urlparse

    from src.config import FREELANCERMAP_EMAIL, FREELANCERMAP_PASSWORD
    from src.freelancermap.browser import close_browser_context, get_browser_context
    from src.freelancermap.login import (
        dismiss_cookie_banner,
        dismiss_session_conflict_modal,
        is_logged_in,
        login,
    )

    context = await get_browser_context(headless=headless)
    try:
        # Prefer a session on freelancermap.com (search/DM UI), not only .de.
        origin = f"{urlparse(OUTREACH_SEARCH_URL).scheme}://{urlparse(OUTREACH_SEARCH_URL).netloc}"
        page = await context.new_page()
        await page.goto(f"{origin}/login", wait_until="domcontentloaded", timeout=60000)
        await dismiss_cookie_banner(page)
        await dismiss_session_conflict_modal(page)
        if not await is_logged_in(page):
            # Reuse the same credentials; login() targets FREELANCERMAP_BASE_URL (.de).
            # If .com still needs auth, fill the .com login form directly.
            if FREELANCERMAP_EMAIL and FREELANCERMAP_PASSWORD:
                for sel in ("#login", "input[type='email']", "input[name='username']"):
                    loc = page.locator(sel).first
                    if await loc.count():
                        await loc.fill(FREELANCERMAP_EMAIL)
                        break
                pw = page.locator("#password, input[type='password']").first
                if await pw.count():
                    await pw.fill(FREELANCERMAP_PASSWORD)
                for sel in (
                    "button[type='submit']",
                    "button:has-text('Log in')",
                    "button:has-text('Login')",
                    "button:has-text('Anmelden')",
                ):
                    btn = page.locator(sel).first
                    if await btn.count():
                        await btn.click()
                        break
                await page.wait_for_timeout(3000)
                await dismiss_session_conflict_modal(page)
            if not await is_logged_in(page):
                # Fall back to existing .de login helper, then hop to .com
                page = await login(context)
                await page.goto(origin, wait_until="domcontentloaded", timeout=60000)

        return await run_outreach(
            page,
            limit=limit,
            dry_run=dry_run,
            project_name=project_name,
        )
    finally:
        await close_browser_context(context)
