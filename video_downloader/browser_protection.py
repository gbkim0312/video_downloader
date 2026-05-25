from __future__ import annotations

import asyncio
from typing import Any
from urllib.parse import urlparse


AD_POPUP_HINTS = (
    "doubleclick",
    "googlesyndication",
    "googleads",
    "gampad",
    "imasdk",
    "pubads",
    "adsterra",
    "adnxs",
    "adform",
    "adsystem",
    "exoclick",
    "popads",
    "popcash",
    "propellerads",
    "onclickads",
    "trafficjunky",
    "taboola",
    "outbrain",
    "mgid",
    "revcontent",
)


def looks_like_ad_popup_url(url: str) -> bool:
    parsed = urlparse(url)
    haystack = " ".join(
        [
            parsed.hostname or "",
            parsed.path,
            parsed.query,
        ]
    ).lower()
    return any(hint in haystack for hint in AD_POPUP_HINTS)


async def install_popup_protection(context: Any, *, allow_popups: bool = False) -> None:
    if allow_popups:
        return

    async def close_ad_page(page: Any) -> None:
        try:
            await page.wait_for_load_state("domcontentloaded", timeout=3000)
        except Exception:
            pass
        try:
            if looks_like_ad_popup_url(page.url):
                await page.close()
        except Exception:
            pass

    async def on_frame_navigated(frame: Any) -> None:
        try:
            page = frame.page
            if frame == page.main_frame and looks_like_ad_popup_url(frame.url):
                await page.close()
        except Exception:
            pass

    def on_page(page: Any) -> None:
        page.on(
            "framenavigated",
            lambda frame: asyncio.create_task(on_frame_navigated(frame)),
        )
        asyncio.create_task(close_ad_page(page))

    async def route_handler(route: Any, request: Any) -> None:
        if looks_like_ad_popup_url(request.url):
            await route.abort()
            return
        await route.continue_()

    context.on("page", on_page)
    await context.route("**/*", route_handler)
