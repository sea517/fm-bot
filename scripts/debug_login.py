import asyncio
import json

from playwright.async_api import async_playwright

from src.config import DATA_DIR, FREELANCERMAP_BASE_URL, FREELANCERMAP_EMAIL, FREELANCERMAP_PASSWORD
from src.freelancermap.login import dismiss_cookie_banner


async def debug() -> None:
    api_calls = []

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        page = await browser.new_page()

        async def on_response(response):
            if any(x in response.url for x in ("login", "auth", "session", "user")):
                try:
                    body = await response.text()
                except Exception:
                    body = ""
                api_calls.append({"url": response.url, "status": response.status, "body": body[:300]})

        page.on("response", on_response)

        await page.goto(f"{FREELANCERMAP_BASE_URL}/login", wait_until="networkidle")
        await dismiss_cookie_banner(page)
        await page.locator("#login").fill(FREELANCERMAP_EMAIL)
        await page.locator("button[data-testid='next-button']").click(force=True)

        for i in range(5):
            await page.wait_for_timeout(2000)
            inputs = await page.eval_on_selector_all(
                "input",
                "els => els.filter(e => e.offsetParent !== null).map(e => ({id:e.id,name:e.name,type:e.type}))",
            )
            print(f"--- after {i+1} --- url={page.url} inputs={inputs}")

        pwd = page.locator("#password:visible, input[type='password']:visible")
        if await pwd.count():
            await pwd.first.fill(FREELANCERMAP_PASSWORD)
            await page.locator("button[data-testid='next-button']:visible").click(force=True)
            await page.wait_for_timeout(5000)

        body = await page.inner_text("body")
        result = {
            "final_url": page.url,
            "logged_in": "abmelden" in body.lower(),
            "api_calls": api_calls,
            "body_preview": body[:500],
        }
        (DATA_DIR / "login_debug.json").write_text(json.dumps(result, indent=2, ensure_ascii=False))
        print(json.dumps(result, indent=2, ensure_ascii=False))
        await browser.close()


if __name__ == "__main__":
    asyncio.run(debug())
