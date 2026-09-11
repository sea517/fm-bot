"""Explore freelancermap site structure to find message URLs and DOM selectors."""

import asyncio
import logging

from src.freelancermap.browser import close_browser_context, get_browser_context
from src.freelancermap.login import login
from src.config import FREELANCERMAP_BASE_URL

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

CANDIDATE_PATHS = [
    "/nachrichten",
    "/messages",
    "/dashboard",
    "/mein-freelancermap",
    "/mein-freelancermap/nachrichten",
    "/projekte/meine-projekte",
    "/projekte",
]


async def explore(headless: bool = False) -> None:
    context = await get_browser_context(headless=headless)
    try:
        page = await login(context)
        logger.info("Logged in. Current URL: %s", page.url)

        for path in CANDIDATE_PATHS:
            url = f"{FREELANCERMAP_BASE_URL}{path}"
            await page.goto(url, wait_until="domcontentloaded")
            title = await page.title()
            logger.info("--- %s ---", url)
            logger.info("  Final URL: %s", page.url)
            logger.info("  Title: %s", title)
            body_preview = (await page.inner_text("body"))[:300].replace("\n", " ")
            logger.info("  Body preview: %s...", body_preview)

        # Dump nav links for manual inspection
        links = page.locator("a[href]")
        count = min(await links.count(), 100)
        seen = set()
        for i in range(count):
            href = await links.nth(i).get_attribute("href")
            text = (await links.nth(i).inner_text()).strip()
            if href and href not in seen and any(
                kw in (href + text).lower()
                for kw in ("nachricht", "message", "chat", "bewerbung", "inbox")
            ):
                seen.add(href)
                logger.info("  Nav link: %s -> %s", text, href)

        input("Press Enter to close browser...")
    finally:
        await close_browser_context(context)


if __name__ == "__main__":
    asyncio.run(explore(headless=False))
