from __future__ import annotations

from typing import Any


DEFAULT_ACCEPT_LANGUAGE = "ko-KR,ko;q=0.9,en-US;q=0.8,en;q=0.7"
DEFAULT_TIMEZONE = "Asia/Seoul"
DEFAULT_VIEWPORT = {"width": 1365, "height": 900}


def browser_launch_options(*, headless: bool, proxy_url: str | None = None) -> dict[str, Any]:
    options: dict[str, Any] = {
        "headless": headless,
        "args": ["--disable-blink-features=AutomationControlled"],
    }
    if proxy_url:
        options["proxy"] = {"server": proxy_url}
    return options


async def new_desktop_context(
    browser: Any,
    *,
    user_agent: str | None = None,
) -> Any:
    resolved_user_agent = user_agent or _desktop_chrome_user_agent(browser)
    context = await browser.new_context(
        user_agent=resolved_user_agent,
        viewport=DEFAULT_VIEWPORT,
        locale="ko-KR",
        timezone_id=DEFAULT_TIMEZONE,
        color_scheme="light",
        device_scale_factor=1,
        extra_http_headers={
            "Accept-Language": DEFAULT_ACCEPT_LANGUAGE,
            "Upgrade-Insecure-Requests": "1",
        },
    )
    await context.add_init_script(
        """
        Object.defineProperty(navigator, 'webdriver', {
          get: () => undefined
        });
        Object.defineProperty(navigator, 'languages', {
          get: () => ['ko-KR', 'ko', 'en-US', 'en']
        });
        Object.defineProperty(navigator, 'platform', {
          get: () => 'MacIntel'
        });
        """
    )
    return context


def _desktop_chrome_user_agent(browser: Any) -> str:
    version = getattr(browser, "version", "")
    if callable(version):
        version = version()
    major_version = str(version).split(".", 1)[0]
    if not major_version.isdigit():
        major_version = "124"
    chrome_version = f"{major_version}.0.0.0"
    return (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        f"Chrome/{chrome_version} Safari/537.36"
    )
