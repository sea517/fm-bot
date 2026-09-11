from playwright.async_api import BrowserContext, async_playwright

from src.config import SESSION_DIR


async def get_browser_context(headless: bool = True) -> BrowserContext:
    SESSION_DIR.mkdir(parents=True, exist_ok=True)
    playwright = await async_playwright().start()
    context = await playwright.chromium.launch_persistent_context(
        user_data_dir=str(SESSION_DIR),
        headless=headless,
        locale="de-DE",
        viewport={"width": 1280, "height": 900},
        user_agent=(
            "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
            "(KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36"
        ),
        ignore_default_args=["--enable-automation"],
        args=["--disable-blink-features=AutomationControlled"],
    )
    await context.add_init_script(
        "Object.defineProperty(navigator, 'webdriver', {get: () => undefined});"
    )
    context._playwright = playwright  # type: ignore[attr-defined]
    return context


async def close_browser_context(context: BrowserContext) -> None:
    playwright = getattr(context, "_playwright", None)
    await context.close()
    if playwright:
        await playwright.stop()
