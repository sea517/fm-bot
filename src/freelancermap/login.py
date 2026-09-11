import logging
import re
from pathlib import Path

from playwright.async_api import BrowserContext, Page

from src.config import DATA_DIR, FREELANCERMAP_BASE_URL, FREELANCERMAP_EMAIL, FREELANCERMAP_PASSWORD

logger = logging.getLogger(__name__)

LOGIN_URL = f"{FREELANCERMAP_BASE_URL}/login"
LOGIN_DEBUG_SHOT = DATA_DIR / "login_debug.png"


async def dismiss_cookie_banner(page: Page) -> None:
    for selector in (
        "#onetrust-accept-btn-handler",
        "button:has-text('Alle Cookies akzeptieren')",
        "button:has-text('Alle zulassen')",
        "button:has-text('Accept All')",
        "button:has-text('Accept all')",
    ):
        btn = page.locator(selector).first
        if await btn.count() > 0:
            try:
                await btn.click(timeout=5000)
                await page.wait_for_timeout(800)
                return
            except Exception:
                pass


async def dismiss_session_conflict_modal(page: Page) -> bool:
    """
    Click 'Weiter' on: 'Ihre Zugangsdaten werden bereits verwendet'.

    freelancermap shows this when another browser session is already logged in.
    Without accepting it, login hangs until someone clicks manually.
    """
    markers = (
        "zugangsdaten werden bereits verwendet",
        "laufenden sitzungen beendet",
        "credentials are already in use",
    )
    try:
        body = (await page.inner_text("body")).lower()
    except Exception:
        return False
    if not any(m in body for m in markers):
        return False

    # Prefer exact "Weiter" — has-text('Weiter') also matches "Weitere Informationen".
    candidates = [
        page.get_by_role("button", name="Weiter", exact=True),
        page.locator("[role='dialog']").get_by_role("button", name="Weiter", exact=True),
        page.locator(".modal, [class*='modal'], [class*='dialog']").get_by_text(
            "Weiter", exact=True
        ),
        page.locator("button, [role='button'], a.fm-btn, div.fm-btn").filter(
            has_text=re.compile(r"^Weiter$")
        ),
        page.locator("[data-testid='confirmation-modal-confirm-btn']"),
        page.locator("[data-id*='right-button'], .right-button:has-text('Weiter')"),
        page.get_by_role("button", name="Continue", exact=True),
    ]

    for loc in candidates:
        try:
            btn = loc.first
            if await btn.count() == 0:
                continue
            try:
                await btn.scroll_into_view_if_needed(timeout=2000)
            except Exception:
                pass

            for method in ("click", "force", "js"):
                try:
                    if method == "click":
                        await btn.click(timeout=4000)
                    elif method == "force":
                        await btn.click(timeout=4000, force=True)
                    else:
                        await btn.evaluate("el => el.click()")
                    await page.wait_for_timeout(1500)
                    logger.info("Accepted session-conflict modal (Weiter via %s)", method)
                    return True
                except Exception as e:
                    logger.debug("Weiter %s failed: %s", method, e)
        except Exception:
            continue

    try:
        clicked = await page.evaluate(
            """() => {
                const nodes = [...document.querySelectorAll('button, [role="button"], a, div, span')];
                const matches = nodes.filter(el => {
                    const t = (el.innerText || el.textContent || '').trim();
                    if (t !== 'Weiter' && t !== 'Continue') return false;
                    const r = el.getBoundingClientRect();
                    return r.width > 0 && r.height > 0;
                });
                if (!matches.length) return false;
                matches.sort((a, b) => b.getBoundingClientRect().left - a.getBoundingClientRect().left);
                matches[0].click();
                return true;
            }"""
        )
        if clicked:
            await page.wait_for_timeout(1500)
            logger.info("Accepted session-conflict modal (Weiter via DOM scan)")
            return True
    except Exception as e:
        logger.debug("DOM Weiter scan failed: %s", e)

    try:
        found = await page.evaluate(
            """() => [...document.querySelectorAll('button, [role="button"], a, div.fm-btn')]
                .filter(el => /weiter/i.test(el.innerText || ''))
                .slice(0, 8)
                .map(el => ({
                    tag: el.tagName,
                    role: el.getAttribute('role'),
                    testid: el.getAttribute('data-testid'),
                    dataId: el.getAttribute('data-id'),
                    className: el.className,
                    text: (el.innerText || '').trim().slice(0, 60),
                    visible: !!(el.offsetWidth || el.offsetHeight),
                }))"""
        )
        logger.warning(
            "Session-conflict modal detected but Weiter not clickable; candidates=%s",
            found,
        )
    except Exception:
        logger.warning("Session-conflict modal detected but Weiter button not clickable")
    return False


async def is_login_page(page: Page) -> bool:
    if "login" not in page.url.lower():
        return False
    try:
        body = (await page.inner_text("body")).lower()
    except Exception:
        return True
    return (
        "willkommen zurück" in body
        or "e-mail-adresse oder benutzername" in body
        or "password" in body
        or "passwort" in body
        or await page.locator("#login, #password, input[type='password']").count() > 0
    )


async def is_logged_in(page: Page) -> bool:
    if await is_login_page(page):
        return False
    try:
        body = (await page.inner_text("body")).lower()
    except Exception:
        return False
    return any(
        kw in body
        for kw in ("abmelden", "postfach", "dashboard", "mein profil", "mein account")
    )


async def _save_login_debug(page: Page, reason: str) -> None:
    try:
        DATA_DIR.mkdir(parents=True, exist_ok=True)
        await page.screenshot(path=str(LOGIN_DEBUG_SHOT), full_page=True)
        logger.error(
            "Login debug (%s): url=%s title=%s screenshot=%s",
            reason,
            page.url,
            await page.title(),
            LOGIN_DEBUG_SHOT,
        )
    except Exception as e:
        logger.error("Could not save login debug screenshot: %s", e)


async def _visible(locator) -> bool:
    try:
        first = locator.first
        if await first.count() == 0:
            return False
        return await first.is_visible()
    except Exception:
        return False


async def _wait_for_login_form(page: Page, timeout_ms: int = 25000) -> None:
    """
    freelancermap has changed layouts over time:
      - single page with #login + #password
      - two-step with next-button after email
    Wait for any of those, not only next-button.
    """
    deadline = timeout_ms
    step = 500
    elapsed = 0
    while elapsed < deadline:
        await dismiss_cookie_banner(page)
        if await _visible(page.locator("#login")):
            return
        if await _visible(page.locator("#password")):
            return
        if await _visible(page.locator("button[data-testid='next-button']")):
            return
        if await _visible(page.locator("input[type='password']")):
            return
        if await is_logged_in(page):
            return
        await page.wait_for_timeout(step)
        elapsed += step
    await _save_login_debug(page, "login form not found")
    raise TimeoutError(
        "Login form not found (no #login / #password / next-button). "
        f"See {LOGIN_DEBUG_SHOT}"
    )


async def _click_next_if_present(page: Page) -> bool:
    next_btn = page.locator("button[data-testid='next-button']").first
    if not await _visible(next_btn):
        # Fallback labels used on some locales / redesigns.
        for sel in (
            "button:has-text('Weiter')",
            "button:has-text('Next')",
            "button[type='submit']",
        ):
            loc = page.locator(sel).first
            if await _visible(loc):
                next_btn = loc
                break
        else:
            return False
    try:
        await next_btn.click(timeout=5000)
        await page.wait_for_timeout(1000)
        return True
    except Exception:
        try:
            await next_btn.click(timeout=5000, force=True)
            await page.wait_for_timeout(1000)
            return True
        except Exception as e:
            logger.debug("next-button click failed: %s", e)
            return False


async def login(context: BrowserContext) -> Page:
    page = await context.new_page()
    await page.goto(LOGIN_URL, wait_until="domcontentloaded")
    try:
        await page.wait_for_load_state("networkidle", timeout=15000)
    except Exception:
        pass
    await dismiss_cookie_banner(page)

    if await is_logged_in(page):
        logger.info("Already logged in: %s", page.url)
        return page

    if not FREELANCERMAP_EMAIL or not FREELANCERMAP_PASSWORD:
        raise ValueError("FREELANCERMAP_EMAIL and FREELANCERMAP_PASSWORD required in .env")

    await _wait_for_login_form(page)

    if await is_logged_in(page):
        logger.info("Already logged in: %s", page.url)
        return page

    login_input = page.locator("#login").first
    if await _visible(login_input):
        await login_input.fill(FREELANCERMAP_EMAIL)
    else:
        # Some layouts use type=email / name=username without #login.
        alt = page.locator(
            "input[type='email'], input[name='username'], input[name='login'], "
            "input[autocomplete='username']"
        ).first
        if await _visible(alt):
            await alt.fill(FREELANCERMAP_EMAIL)
        else:
            await _save_login_debug(page, "email field missing")
            raise RuntimeError(f"Could not find email/login field. See {LOGIN_DEBUG_SHOT}")

    password_input = page.locator("#password").first
    if not await _visible(password_input):
        # Two-step: email first, then Weiter / next-button reveals password.
        await _click_next_if_present(page)
        await dismiss_cookie_banner(page)
        try:
            await page.locator("#password, input[type='password']").first.wait_for(
                state="visible", timeout=15000
            )
        except Exception:
            await _save_login_debug(page, "password field missing after next")
            raise RuntimeError(
                f"Password field not shown after email step. See {LOGIN_DEBUG_SHOT}"
            )
        password_input = page.locator("#password, input[type='password']").first

    await password_input.fill(FREELANCERMAP_PASSWORD)

    remember = page.locator("#_remember_me")
    if await remember.count() > 0:
        try:
            await remember.check()
        except Exception:
            pass

    await dismiss_cookie_banner(page)

    # Prefer explicit submit / next; Enter on password as fallback.
    submitted = await _click_next_if_present(page)
    if not submitted:
        submit = page.locator(
            "button[type='submit'], button[data-testid='login-button'], "
            "button:has-text('Anmelden'), button:has-text('Log in'), "
            "button:has-text('Login')"
        ).first
        if await _visible(submit):
            try:
                await submit.click(timeout=5000)
                submitted = True
            except Exception:
                pass
    if not submitted:
        await password_input.press("Enter")

    await page.wait_for_timeout(1500)
    await dismiss_session_conflict_modal(page)

    for _ in range(45):
        await page.wait_for_timeout(1000)
        await dismiss_cookie_banner(page)
        await dismiss_session_conflict_modal(page)
        if await is_logged_in(page):
            logger.info("Login successful: %s", page.url)
            return page
        if not await is_login_page(page):
            await dismiss_session_conflict_modal(page)
            if await is_logged_in(page) or "login" not in page.url.lower():
                logger.info("Login successful: %s", page.url)
                return page

    await _save_login_debug(page, "credentials submitted but not logged in")
    raise RuntimeError(
        "Login failed. Verify FREELANCERMAP_EMAIL and FREELANCERMAP_PASSWORD in .env. "
        f"See {LOGIN_DEBUG_SHOT}"
    )


async def find_postfach_link(page: Page) -> str | None:
    selectors = (
        "a:has-text('Postfach')",
        "a[href*='postfach']",
        "a[href*='inbox']",
        "a[href*='nachricht']",
    )
    for sel in selectors:
        link = page.locator(sel).first
        if await link.count() > 0:
            href = await link.get_attribute("href")
            if href:
                if href.startswith("/"):
                    return f"{FREELANCERMAP_BASE_URL}{href}"
                return href
    return None
