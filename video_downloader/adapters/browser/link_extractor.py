from __future__ import annotations

import asyncio

from .context import (
    browser_launch_options,
    desktop_context_options,
    harden_context,
    new_desktop_context,
)
from .protection import install_popup_protection, looks_like_ad_popup_url
from video_downloader.domain.models import LinkCandidate, ProxySettings
from video_downloader.domain.scoring import dedupe_links, score_link


async def _collect_link_rows(page) -> list[dict]:
    return await page.locator("a[href]").evaluate_all(
        """anchors => anchors.map(anchor => {
            const text = (anchor.innerText || anchor.getAttribute('aria-label') || anchor.title || '').trim();
            const image = anchor.querySelector('img, picture, video, [style*="background-image"]');
            const nearbyImage = anchor.closest('article, li, div')?.querySelector('img, picture, video, [style*="background-image"]');
            return {
                url: anchor.href,
                text,
                hasThumbnail: Boolean(image || nearbyImage),
            };
        })"""
    )


async def _is_disabled(item) -> bool:
    aria_disabled = await item.get_attribute("aria-disabled")
    disabled = await item.get_attribute("disabled")
    class_name = (await item.get_attribute("class")) or ""
    return (
        aria_disabled == "true"
        or disabled is not None
        or "disabled" in class_name.lower()
    )


async def _click_page_number(page, page_number: int) -> bool:
    page_text = str(page_number)
    locator = page.locator("a, button, [role=button], [role=link]").filter(
        has_text=page_text
    )
    count = await locator.count()
    for index in range(count):
        item = locator.nth(index)
        try:
            text = (await item.inner_text(timeout=1000)).strip()
            if text != page_text or not await item.is_visible():
                continue
            if await _is_disabled(item):
                continue
            await item.scroll_into_view_if_needed(timeout=2000)
            await item.click(timeout=5000)
            return True
        except Exception:
            continue
    return False


async def _click_next_page(page) -> bool:
    next_labels = ("next", "다음", ">", "›", "»")
    locator = page.locator("a, button, [role=button], [role=link]")
    count = await locator.count()
    for index in range(count):
        item = locator.nth(index)
        try:
            if not await item.is_visible() or await _is_disabled(item):
                continue
            text = (await item.inner_text(timeout=1000)).strip()
            aria_label = (await item.get_attribute("aria-label")) or ""
            title = (await item.get_attribute("title")) or ""
            label = " ".join([text, aria_label, title]).strip().lower()
            if not any(next_label in label for next_label in next_labels):
                continue
            await item.scroll_into_view_if_needed(timeout=2000)
            await item.click(timeout=5000)
            return True
        except Exception:
            continue
    return False


async def _collect_paginated_link_rows(
    page,
    *,
    page_start: int,
    page_end: int,
    wait_seconds: float,
) -> list[dict]:
    rows: list[dict] = []
    if page_start > 1:
        if not await _click_page_number(page, page_start):
            raise ValueError(f"could not find clickable page number: {page_start}")
        await page.wait_for_timeout(int(wait_seconds * 1000))

    for page_number in range(page_start, page_end + 1):
        rows.extend(await _collect_link_rows(page))
        if page_number < page_end:
            if not await _click_page_number(page, page_number + 1):
                if not await _click_next_page(page):
                    break
            await page.wait_for_timeout(int(wait_seconds * 1000))
    return rows


async def _extract_links(
    url: str,
    *,
    headless: bool,
    user_agent: str | None,
    min_score: int,
    wait_seconds: float,
    allow_popups: bool,
    proxy_settings: ProxySettings | None,
    user_data_dir: str | None,
    browser_channel: str | None,
    spoof_browser: bool,
    block_devtool_detectors: bool,
    page_start: int | None,
    page_end: int | None,
) -> list[LinkCandidate]:
    try:
        from playwright.async_api import async_playwright
    except ModuleNotFoundError as exc:
        raise RuntimeError(
            "Missing dependency: playwright. Install with `pip install -e .` "
            "and run `python -m playwright install chromium`."
        ) from exc

    async with async_playwright() as p:
        proxy_url = proxy_settings.proxy_url if proxy_settings else None
        browser = None
        if user_data_dir:
            context = await p.chromium.launch_persistent_context(
                user_data_dir,
                **browser_launch_options(
                    headless=headless,
                    proxy_url=proxy_url,
                    browser_channel=browser_channel,
                    spoof_browser=spoof_browser,
                ),
                **desktop_context_options(
                    user_agent=user_agent,
                    spoof_browser=spoof_browser,
                ),
            )
            if spoof_browser:
                await harden_context(context)
        else:
            browser = await p.chromium.launch(
                **browser_launch_options(
                    headless=headless,
                    proxy_url=proxy_url,
                    browser_channel=browser_channel,
                    spoof_browser=spoof_browser,
                )
            )
            context = await new_desktop_context(
                browser,
                user_agent=user_agent,
                spoof_browser=spoof_browser,
            )
        await install_popup_protection(
            context,
            allow_popups=allow_popups,
            block_devtool_detectors=block_devtool_detectors,
        )
        page = (
            context.pages[0]
            if user_data_dir and context.pages
            else await context.new_page()
        )
        try:
            await page.goto(url, wait_until="domcontentloaded", timeout=60000)
            await page.wait_for_timeout(int(wait_seconds * 1000))
            if page_start is not None and page_end is not None:
                rows = await _collect_paginated_link_rows(
                    page,
                    page_start=page_start,
                    page_end=page_end,
                    wait_seconds=wait_seconds,
                )
            else:
                rows = await _collect_link_rows(page)
        finally:
            await context.close()
            if browser is not None:
                await browser.close()

    candidates = []
    for row in rows:
        link_url = row.get("url", "")
        if looks_like_ad_popup_url(link_url):
            continue
        text = row.get("text", "")
        has_thumbnail = bool(row.get("hasThumbnail"))
        score = score_link(link_url, text, has_thumbnail, is_ad_url=False)
        if score >= min_score:
            candidates.append(
                LinkCandidate(
                    url=link_url,
                    text=text,
                    has_thumbnail=has_thumbnail,
                    score=score,
                )
            )
    return dedupe_links(candidates)


def extract_video_links(
    url: str,
    *,
    headless: bool = True,
    user_agent: str | None = None,
    min_score: int = 6,
    wait_seconds: float = 3,
    allow_popups: bool = False,
    proxy_settings: ProxySettings | None = None,
    user_data_dir: str | None = None,
    browser_channel: str | None = None,
    spoof_browser: bool = False,
    block_devtool_detectors: bool = False,
    page_start: int | None = None,
    page_end: int | None = None,
) -> list[LinkCandidate]:
    return asyncio.run(
        _extract_links(
            url,
            headless=headless,
            user_agent=user_agent,
            min_score=min_score,
            wait_seconds=wait_seconds,
            allow_popups=allow_popups,
            proxy_settings=proxy_settings,
            user_data_dir=user_data_dir,
            browser_channel=browser_channel,
            spoof_browser=spoof_browser,
            block_devtool_detectors=block_devtool_detectors,
            page_start=page_start,
            page_end=page_end,
        )
    )


class PlaywrightLinkExtractor:
    def extract_video_links(
        self,
        url: str,
        *,
        headless: bool = True,
        user_agent: str | None = None,
        min_score: int = 6,
        wait_seconds: float = 3,
        allow_popups: bool = False,
        proxy_settings: ProxySettings | None = None,
        user_data_dir: str | None = None,
        browser_channel: str | None = None,
        spoof_browser: bool = False,
        block_devtool_detectors: bool = False,
        page_start: int | None = None,
        page_end: int | None = None,
    ) -> list[LinkCandidate]:
        return extract_video_links(
            url,
            headless=headless,
            user_agent=user_agent,
            min_score=min_score,
            wait_seconds=wait_seconds,
            allow_popups=allow_popups,
            proxy_settings=proxy_settings,
            user_data_dir=user_data_dir,
            browser_channel=browser_channel,
            spoof_browser=spoof_browser,
            block_devtool_detectors=block_devtool_detectors,
            page_start=page_start,
            page_end=page_end,
        )
