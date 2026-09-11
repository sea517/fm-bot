"""Dump the freelancermap Postfach DOM so we can target real conversation rows.

Run:  PYTHONPATH=. python3 scripts/inspect_pobox.py
Writes: data/pobox_dom.html  +  data/pobox_probe.json
"""

import asyncio
import json
import logging

from src.config import DATA_DIR, FREELANCERMAP_MESSAGES_URL
from src.freelancermap.browser import close_browser_context, get_browser_context
from src.freelancermap.login import login

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
logger = logging.getLogger(__name__)

PROBES = [
    "[data-conversation-id]",
    "[data-message-id]",
    "[data-thread-id]",
    "[data-id]",
    "a[href*='pobox']",
    "a[href*='conversation']",
    "a[href*='thread']",
    "li[class*='conversation']",
    "div[class*='conversation']",
    "[class*='pobox-list'] li",
    "[class*='pobox-list'] > div",
    "[class*='message-list'] li",
    "[class*='thread']",
    "[role='listitem']",
    "table tbody tr",
]


async def main() -> None:
    context = await get_browser_context(headless=False)
    try:
        page = await login(context)
        url = FREELANCERMAP_MESSAGES_URL
        logger.info("Opening %s", url)
        await page.goto(url, wait_until="domcontentloaded")
        await page.wait_for_timeout(4000)

        html = await page.content()
        (DATA_DIR / "pobox_dom.html").write_text(html, encoding="utf-8")
        logger.info("Saved DOM (%d chars) to data/pobox_dom.html", len(html))

        report: dict = {"url": page.url, "probes": {}}
        for sel in PROBES:
            try:
                loc = page.locator(sel)
                count = await loc.count()
            except Exception as e:
                report["probes"][sel] = {"error": str(e)[:120]}
                continue
            samples = []
            for i in range(min(count, 4)):
                item = loc.nth(i)
                try:
                    text = (await item.inner_text())[:160].replace("\n", " | ")
                    attrs = await item.evaluate(
                        "el => Object.fromEntries("
                        "[...el.attributes].map(a => [a.name, a.value]))"
                    )
                except Exception:
                    continue
                samples.append({"text": text, "attrs": attrs})
            report["probes"][sel] = {"count": count, "samples": samples}
            logger.info("%-40s count=%s", sel, count)

        (DATA_DIR / "pobox_probe.json").write_text(
            json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8"
        )
        logger.info("Saved probe report to data/pobox_probe.json")
        input("Inspect the browser, then press Enter to close...")
    finally:
        await close_browser_context(context)


if __name__ == "__main__":
    asyncio.run(main())
