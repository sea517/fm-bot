import json
import logging
import re

from src.config import DATA_DIR, FREELANCERMAP_BASE_URL, ROOT_DIR
from src.freelancermap.browser import close_browser_context, get_browser_context
from src.freelancermap.login import dismiss_cookie_banner, find_postfach_link, login

logger = logging.getLogger(__name__)

DISCOVERY_FILE = DATA_DIR / "site_discovery.json"
ENV_FILE = ROOT_DIR / ".env"

EXTRA_PATHS = [
    "/postfach",
    "/inbox",
    "/dashboard",
    "/account",
    "/account/messages",
    "/account/nachrichten",
]


def apply_messages_url_to_env(url: str) -> None:
    if not ENV_FILE.exists():
        return
    text = ENV_FILE.read_text(encoding="utf-8")
    if re.search(r"^FREELANCERMAP_MESSAGES_URL=.*$", text, flags=re.MULTILINE):
        text = re.sub(
            r"^FREELANCERMAP_MESSAGES_URL=.*$",
            f"FREELANCERMAP_MESSAGES_URL={url}",
            text,
            flags=re.MULTILINE,
        )
    else:
        text += f"\nFREELANCERMAP_MESSAGES_URL={url}\n"
    ENV_FILE.write_text(text, encoding="utf-8")
    logger.info("Updated .env FREELANCERMAP_MESSAGES_URL=%s", url)


async def discover(headless: bool = True) -> dict:
    results: dict = {"pages": [], "recommended_messages_url": None}
    context = await get_browser_context(headless=headless)
    try:
        page = await login(context)
        results["after_login_url"] = page.url

        postfach = await find_postfach_link(page)
        if postfach:
            results["recommended_messages_url"] = postfach
            results["source"] = "nav_link"

        for path in EXTRA_PATHS:
            url = f"{FREELANCERMAP_BASE_URL}{path}"
            try:
                await page.goto(url, wait_until="domcontentloaded", timeout=20000)
                await dismiss_cookie_banner(page)
                await page.wait_for_timeout(800)
                title = await page.title()
                body = await page.inner_text("body")
                if "404" in title or "seite nicht gefunden" in body.lower()[:300]:
                    continue
                looks_like_inbox = any(
                    kw in body.lower()
                    for kw in ("postfach", "nachricht", "konversation", "bewerbung", "inbox")
                )
                entry = {
                    "path": path,
                    "final_url": page.url,
                    "title": title,
                    "looks_like_inbox": looks_like_inbox,
                }
                results["pages"].append(entry)
                if looks_like_inbox and not results["recommended_messages_url"]:
                    results["recommended_messages_url"] = page.url
                    results["source"] = "path_probe"
            except Exception as exc:
                logger.debug("Skip %s: %s", path, exc)

        DISCOVERY_FILE.write_text(json.dumps(results, indent=2, ensure_ascii=False), encoding="utf-8")
        if results.get("recommended_messages_url"):
            apply_messages_url_to_env(results["recommended_messages_url"])
            logger.info("Inbox URL: %s", results["recommended_messages_url"])
        else:
            logger.warning(
                "No inbox URL found. Log in manually, open Postfach, copy the URL to "
                "FREELANCERMAP_MESSAGES_URL in .env"
            )
        return results
    finally:
        await close_browser_context(context)
