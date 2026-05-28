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

DEVTOOL_DETECTOR_HINTS = (
    "disable-devtool",
    "disable_devtool",
    "devtools-detector",
    "anti-devtool",
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


def looks_like_devtool_detector_url(url: str) -> bool:
    parsed = urlparse(url)
    haystack = " ".join(
        [
            parsed.hostname or "",
            parsed.path,
            parsed.query,
        ]
    ).lower()
    return any(hint in haystack for hint in DEVTOOL_DETECTOR_HINTS)


async def install_popup_protection(
    context: Any,
    *,
    allow_popups: bool = False,
    block_devtool_detectors: bool = False,
    debug_log: Any | None = None,
) -> None:
    if allow_popups:
        if debug_log:
            debug_log.event("popup_protection_disabled")
        return

    async def has_opener(page: Any) -> bool:
        try:
            return await page.opener() is not None
        except Exception:
            return False

    async def close_ad_page(page: Any) -> None:
        try:
            await page.wait_for_load_state("domcontentloaded", timeout=3000)
        except Exception:
            pass
        try:
            if await has_opener(page) and looks_like_ad_popup_url(page.url):
                if debug_log:
                    debug_log.event("popup_page_closed", url=page.url)
                await page.close()
        except Exception:
            pass

    async def on_frame_navigated(frame: Any) -> None:
        try:
            page = frame.page
            if (
                await has_opener(page)
                and frame == page.main_frame
                and looks_like_ad_popup_url(frame.url)
            ):
                if debug_log:
                    debug_log.event("popup_frame_closed", url=frame.url)
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
        if block_devtool_detectors and looks_like_devtool_detector_url(request.url):
            if debug_log:
                debug_log.event(
                    "request_aborted_as_devtool_detector",
                    url=request.url,
                    resource_type=request.resource_type,
                )
            await route.abort()
            return
        if looks_like_ad_popup_url(request.url):
            if debug_log:
                debug_log.event(
                    "request_aborted_as_ad",
                    url=request.url,
                    resource_type=request.resource_type,
                )
            await route.abort()
            return
        await route.continue_()

    context.on("page", on_page)
    await context.route("**/*", route_handler)
