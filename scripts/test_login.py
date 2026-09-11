import asyncio
import json

from src.config import DATA_DIR, FREELANCERMAP_BASE_URL, FREELANCERMAP_EMAIL, FREELANCERMAP_PASSWORD
from src.freelancermap.browser import close_browser_context, get_browser_context
from src.freelancermap.login import dismiss_cookie_banner


async def test() -> None:
    context = await get_browser_context(headless=True)
    try:
        page = await context.new_page()
        await page.goto(f"{FREELANCERMAP_BASE_URL}/login", wait_until="networkidle")
        await dismiss_cookie_banner(page)

        await page.locator("#login").fill(FREELANCERMAP_EMAIL)
        pwd_visible_before = await page.locator("#password").is_visible()
        await page.locator("#password").fill(FREELANCERMAP_PASSWORD)
        remember = page.locator("#_remember_me")
        if await remember.count():
            await remember.check()

        await dismiss_cookie_banner(page)

        async with page.expect_navigation(timeout=45000, wait_until="networkidle"):
            await page.locator("button[data-testid='next-button']").click(force=True)

        body = await page.inner_text("body")
        result = {
            "pwd_visible_before": pwd_visible_before,
            "final_url": page.url,
            "final_title": await page.title(),
            "still_login": "willkommen zurück" in body.lower(),
            "body_preview": body[:1200],
        }
        (DATA_DIR / "login_result.json").write_text(json.dumps(result, indent=2, ensure_ascii=False))
        print(json.dumps(result, indent=2, ensure_ascii=False))
    finally:
        await close_browser_context(context)


if __name__ == "__main__":
    asyncio.run(test())
