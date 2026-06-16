from __future__ import annotations

from typing import Any


DEFAULT_ACCEPT_LANGUAGE = "ko-KR,ko;q=0.9,en-US;q=0.8,en;q=0.7"
DEFAULT_TIMEZONE = "Asia/Seoul"
DEFAULT_VIEWPORT = {"width": 1365, "height": 900}


def browser_launch_options(
    *,
    headless: bool,
    proxy_url: str | None = None,
    browser_channel: str | None = None,
    spoof_browser: bool = False,
) -> dict[str, Any]:
    options: dict[str, Any] = {"headless": headless}
    if spoof_browser:
        options["args"] = ["--disable-blink-features=AutomationControlled"]
    if proxy_url:
        options["proxy"] = {"server": proxy_url}
    if browser_channel:
        options["channel"] = browser_channel
    return options


def desktop_context_options(
    *,
    user_agent: str | None = None,
    browser: Any | None = None,
    spoof_browser: bool = False,
) -> dict[str, Any]:
    options: dict[str, Any] = {"viewport": DEFAULT_VIEWPORT}
    if user_agent:
        options["user_agent"] = user_agent
    elif spoof_browser:
        options["user_agent"] = _desktop_chrome_user_agent(browser)

    if spoof_browser:
        options.update(
            {
                "locale": "ko-KR",
                "timezone_id": DEFAULT_TIMEZONE,
                "color_scheme": "light",
                "device_scale_factor": 1,
                "extra_http_headers": {
                    "Accept-Language": DEFAULT_ACCEPT_LANGUAGE,
                    "Upgrade-Insecure-Requests": "1",
                },
            }
        )
    return options


async def new_desktop_context(
    browser: Any,
    *,
    user_agent: str | None = None,
    spoof_browser: bool = False,
) -> Any:
    context = await browser.new_context(
        **desktop_context_options(
            user_agent=user_agent,
            browser=browser,
            spoof_browser=spoof_browser,
        )
    )
    if spoof_browser:
        await harden_context(context)
    return context


async def harden_context(context: Any) -> None:
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


def _desktop_chrome_user_agent(browser: Any | None) -> str:
    version = getattr(browser, "version", "") if browser is not None else ""
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
