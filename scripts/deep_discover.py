"""Deep discovery after login — find Postfach / inbox URL."""

import asyncio
import json
import logging
import sys

from src.config import DATA_DIR, FREELANCERMAP_BASE_URL
from src.freelancermap.browser import close_browser_context, get_browser_context
from src.freelancermap.login import dismiss_cookie_banner, login

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

OUT = DATA_DIR / "site_discovery.json"

EXTRA_PATHS = [
    "/postfach",
    "/inbox",
    "/dashboard",
    "/account",
    "/account/messages",
    "/account/nachrichten",
    "/unternehmen",
    "/unternehmen/nachrichten",
    "/projekte/meine-projekte",
    "/projekte/verwalten",
    "/recruiter",
    "/recruiter/dashboard",
    "/mein-freelancermap",
    "/projekt-erstellen",
    "/neues-projekt",
]


async def deep_discover(headless: bool = True) -> dict:
    context = await get_browser_context(headless=headless)
    results: dict = {"links": [], "pages": [], "recommended_messages_url": None}
    try:
        page = await login(context)
        results["after_login_url"] = page.url
        results["after_login_title"] = await page.title()

        hrefs = await page.eval_on_selector_all(
            "a[href]",
            "els => els.map(e => ({href: e.getAttribute('href'), text: (e.innerText||'').trim()}))",
        )
        seen = set()
        for item in hrefs:
            href = item.get("href") or ""
            text = item.get("text") or ""
            if not href or href in seen:
                continue
            if href.startswith("http") and FREELANCERMAP_BASE_URL not in href:
                continue
            seen.add(href)
            combined = (href + " " + text).lower()
            if any(kw in combined for kw in (
                "nachricht", "message", "chat", "bewerbung", "inbox", "postfach",
                "dashboard", "account", "projekt", "anfrage",
            )):
                results["links"].append({"href": href, "text": text})

        for path in EXTRA_PATHS:
            url = f"{FREELANCERMAP_BASE_URL}{path}"
            try:
                resp = await page.goto(url, wait_until="domcontentloaded", timeout=20000)
                await dismiss_cookie_banner(page)
                await page.wait_for_timeout(800)
                title = await page.title()
                body = await page.inner_text("body")
                if "404" in title or "seite nicht gefunden" in body.lower()[:300]:
                    continue
                looks_like_inbox = any(
                    kw in body.lower()
                    for kw in ("postfach", "nachricht", "konversation", "bewerbung", "inbox", "thread")
                )
                entry = {
                    "path": path,
                    "status": resp.status if resp else 0,
                    "final_url": page.url,
                    "title": title,
                    "preview": body[:350].replace("\n", " "),
                    "looks_like_inbox": looks_like_inbox,
                }
                results["pages"].append(entry)
                if looks_like_inbox and not results["recommended_messages_url"]:
                    results["recommended_messages_url"] = page.url
            except Exception as exc:
                logger.debug("Skip %s: %s", path, exc)

        if not results["recommended_messages_url"]:
            for link in results["links"]:
                combined = (link["href"] + " " + link["text"]).lower()
                if any(kw in combined for kw in ("postfach", "nachricht", "inbox", "message")):
                    href = link["href"]
                    if href.startswith("/"):
                        href = f"{FREELANCERMAP_BASE_URL}{href}"
                    results["recommended_messages_url"] = href
                    break

        OUT.write_text(json.dumps(results, indent=2, ensure_ascii=False), encoding="utf-8")
        logger.info("Saved %s", OUT)
        logger.info("After login: %s", results["after_login_url"])
        if results.get("recommended_messages_url"):
            logger.info("Recommended inbox URL: %s", results["recommended_messages_url"])
        for link in results["links"][:15]:
            logger.info("  LINK: [%s] %s", link["text"], link["href"])
        for p in results["pages"]:
            logger.info("  PAGE: %s inbox=%s — %s", p["path"], p["looks_like_inbox"], p["title"])
        return results
    finally:
        await close_browser_context(context)


if __name__ == "__main__":
    headless = "--headed" not in sys.argv
    asyncio.run(deep_discover(headless=headless))
