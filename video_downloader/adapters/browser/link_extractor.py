from __future__ import annotations

import asyncio
import sys

from .context import (
    browser_launch_options,
    desktop_context_options,
    harden_context,
    new_desktop_context,
)
from .protection import install_popup_protection, looks_like_ad_popup_url
from video_downloader.domain.models import LinkCandidate, ProxySettings
from video_downloader.domain.scoring import dedupe_links, score_link


LINK_POLL_INTERVAL_SECONDS = 0.25
SIGNATURE_HINTS = (
    "watch",
    "video",
    "videos",
    "play",
    "player",
    "episode",
    "vod",
    "lecture",
    "course",
    "media",
)


async def _collect_link_rows(page) -> list[dict]:
    return await page.locator("a[href]").evaluate_all(
        """anchors => anchors.map(anchor => {
            const container = anchor.closest('article, li, [class*="item"], [class*="card"], [class*="post"], [class*="episode"], [class*="video"], div');
            const text = (
                anchor.innerText ||
                anchor.getAttribute('aria-label') ||
                anchor.title ||
                container?.innerText ||
                ''
            ).trim();
            const mediaSelector = [
                'img',
                'picture',
                'video',
                'source',
                '[style*="background-image"]',
                '[data-src]',
                '[data-original]',
                '[data-background]',
                '[class*="thumb"]',
                '[class*="poster"]',
                '[class*="image"]'
            ].join(',');
            const image = anchor.querySelector(mediaSelector);
            const nearbyImage = container?.querySelector(mediaSelector);
            return {
                url: anchor.href,
                text,
                hasThumbnail: Boolean(image || nearbyImage),
            };
        })"""
    )


def _rows_signature(rows: list[dict]) -> tuple[str, ...]:
    signature_rows = [
        row
        for row in rows
        if row.get("hasThumbnail")
        or any(
            hint in f"{row.get('url', '')} {row.get('text', '')}".lower()
            for hint in SIGNATURE_HINTS
        )
    ]
    if not signature_rows:
        signature_rows = rows
    return tuple(
        f"{row.get('url', '')}\n{row.get('text', '')}"
        for row in signature_rows
    )


def _tag_rows(rows: list[dict], page_number: int) -> list[dict]:
    for row in rows:
        row["pageNumber"] = page_number
    return rows


async def _collect_stable_link_rows(page, wait_seconds: float) -> list[dict]:
    deadline = asyncio.get_running_loop().time() + max(wait_seconds, 1.0)
    previous_signature: tuple[str, ...] | None = None
    latest_rows: list[dict] = []

    while True:
        latest_rows = await _collect_link_rows(page)
        signature = _rows_signature(latest_rows)
        if signature and signature == previous_signature:
            return latest_rows
        if asyncio.get_running_loop().time() >= deadline:
            return latest_rows
        previous_signature = signature
        await page.wait_for_timeout(int(LINK_POLL_INTERVAL_SECONDS * 1000))


async def _wait_for_link_change(
    page,
    previous_signature: tuple[str, ...],
    wait_seconds: float,
) -> list[dict]:
    try:
        await page.wait_for_load_state("networkidle", timeout=1500)
    except Exception:
        pass

    deadline = asyncio.get_running_loop().time() + max(wait_seconds, 1.0)
    latest_rows: list[dict] = []
    while True:
        latest_rows = await _collect_stable_link_rows(
            page,
            min(wait_seconds, LINK_POLL_INTERVAL_SECONDS * 2),
        )
        signature = _rows_signature(latest_rows)
        if signature and signature != previous_signature:
            return latest_rows
        if asyncio.get_running_loop().time() >= deadline:
            return latest_rows
        await page.wait_for_timeout(int(LINK_POLL_INTERVAL_SECONDS * 1000))


async def _advance_to_page(
    page,
    page_number: int,
    previous_signature: tuple[str, ...],
    wait_seconds: float,
) -> tuple[str, ...]:
    signature = previous_signature
    for _ in range(max(10, page_number + 2)):
        if await _click_page_number(page, page_number):
            rows = await _wait_for_link_change(page, signature, wait_seconds)
            next_signature = _rows_signature(rows)
            if next_signature and next_signature != signature:
                return next_signature

        if not await _click_next_page(page):
            return signature

        rows = await _wait_for_link_change(page, signature, wait_seconds)
        next_signature = _rows_signature(rows)
        if next_signature and next_signature != signature:
            if previous_signature:
                return next_signature
            signature = next_signature
            continue
        signature = next_signature
    return signature


async def _click_page_number(page, page_number: int) -> bool:
    return bool(
        await page.evaluate(
            """target => {
                const normalize = value => (value || '').replace(/\\s+/g, ' ').trim();
                const isVisible = element => {
                    const style = window.getComputedStyle(element);
                    const rect = element.getBoundingClientRect();
                    return style.visibility !== 'hidden' &&
                        style.display !== 'none' &&
                        rect.width > 0 &&
                        rect.height > 0;
                };
                const isDisabled = element => {
                    const className = (element.getAttribute('class') || '').toLowerCase();
                    return element.disabled ||
                        element.getAttribute('aria-disabled') === 'true' ||
                        className.includes('disabled');
                };
                const inPagination = element => Boolean(
                    element.closest(
                        'nav, [role="navigation"], .pagination, .paging, .pager, ' +
                        '.page, .pages, .paginate, .pagenation, .page-numbers, ' +
                        '[class*="pagination"], [class*="paging"], [class*="pager"]'
                    )
                );
                const pagePattern = new RegExp(
                    '(^|[^0-9])' + target + '([^0-9]|$)'
                );
                const hrefPattern = new RegExp(
                    '(page|paged|p|page_no|pageNo|page_num|pageNum)(=|/)' + target + '([^0-9]|$)?',
                    'i'
                );
                const candidates = Array.from(
                    document.querySelectorAll('a, button, [role="button"], [role="link"]')
                );
                const matches = [];
                for (const element of candidates) {
                    if (!isVisible(element) || isDisabled(element)) {
                        continue;
                    }
                    const text = normalize(element.textContent);
                    const aria = normalize(element.getAttribute('aria-label'));
                    const title = normalize(element.getAttribute('title'));
                    const dataPage = normalize(element.getAttribute('data-page'));
                    const href = element.getAttribute('href') || '';
                    const label = [text, aria, title, dataPage].filter(Boolean).join(' ');
                    const exact = [text, aria, title, dataPage].includes(target);
                    const labelledPage = pagePattern.test(label) &&
                        /(page|페이지|쪽|p\\.?)/i.test(label);
                    const hrefPage = hrefPattern.test(href);
                    if (!exact && !labelledPage && !hrefPage) {
                        continue;
                    }
                    matches.push({
                        element,
                        score:
                            (inPagination(element) ? 100 : 0) +
                            (exact ? 20 : 0) +
                            (labelledPage ? 10 : 0) +
                            (hrefPage ? 5 : 0),
                    });
                }
                matches.sort((left, right) => right.score - left.score);
                if (!matches.length) {
                    return false;
                }
                matches[0].element.scrollIntoView({block: 'center', inline: 'center'});
                matches[0].element.click();
                return true;
            }""",
            str(page_number),
        )
    )


async def _click_next_page(page) -> bool:
    return bool(
        await page.evaluate(
            """() => {
                const normalize = value => (value || '').replace(/\\s+/g, ' ').trim();
                const isVisible = element => {
                    const style = window.getComputedStyle(element);
                    const rect = element.getBoundingClientRect();
                    return style.visibility !== 'hidden' &&
                        style.display !== 'none' &&
                        rect.width > 0 &&
                        rect.height > 0;
                };
                const isDisabled = element => {
                    const className = (element.getAttribute('class') || '').toLowerCase();
                    return element.disabled ||
                        element.getAttribute('aria-disabled') === 'true' ||
                        className.includes('disabled');
                };
                const inPagination = element => Boolean(
                    element.closest(
                        'nav, [role="navigation"], .pagination, .paging, .pager, ' +
                        '.page, .pages, .paginate, .pagenation, .page-numbers, ' +
                        '[class*="pagination"], [class*="paging"], [class*="pager"]'
                    )
                );
                const candidates = Array.from(
                    document.querySelectorAll('a, button, [role="button"], [role="link"]')
                );
                const matches = [];
                for (const element of candidates) {
                    if (!isVisible(element) || isDisabled(element)) {
                        continue;
                    }
                    const text = normalize(element.textContent).toLowerCase();
                    const aria = normalize(element.getAttribute('aria-label')).toLowerCase();
                    const title = normalize(element.getAttribute('title')).toLowerCase();
                    const rel = normalize(element.getAttribute('rel')).toLowerCase();
                    const className = normalize(element.getAttribute('class')).toLowerCase();
                    const label = [text, aria, title, rel, className].filter(Boolean).join(' ');
                    const exact = ['next', '다음', '>', '›', '»'].includes(text);
                    const labelledNext = /(next|다음|forward|right|angle-right|chevron-right)/i.test(label);
                    if (!exact && !labelledNext) {
                        continue;
                    }
                    matches.push({
                        element,
                        score:
                            (inPagination(element) ? 100 : 0) +
                            (rel === 'next' ? 30 : 0) +
                            (exact ? 20 : 0) +
                            (labelledNext ? 10 : 0),
                    });
                }
                matches.sort((left, right) => right.score - left.score);
                if (!matches.length) {
                    return false;
                }
                matches[0].element.scrollIntoView({block: 'center', inline: 'center'});
                matches[0].element.click();
                return true;
            }"""
        )
    )


async def _collect_paginated_link_rows(
    page,
    *,
    page_start: int,
    page_end: int,
    wait_seconds: float,
    debug: bool,
) -> list[dict]:
    rows: list[dict] = []
    if page_start > 1:
        start_signature = await _advance_to_page(page, page_start, (), wait_seconds)
        if not start_signature:
            raise ValueError(f"could not find clickable page number: {page_start}")

    previous_signature: tuple[str, ...] | None = None
    for page_number in range(page_start, page_end + 1):
        current_rows = await _collect_stable_link_rows(page, wait_seconds)
        current_signature = _rows_signature(current_rows)
        if current_signature and current_signature != previous_signature:
            if debug:
                print(
                    f"link page {page_number}: raw={len(current_rows)}",
                    file=sys.stderr,
                )
            rows.extend(_tag_rows(current_rows, page_number))
        elif debug:
            print(
                f"link page {page_number}: skipped duplicate/empty raw={len(current_rows)}",
                file=sys.stderr,
            )
        previous_signature = current_signature
        if page_number < page_end:
            next_signature = await _advance_to_page(
                page,
                page_number + 1,
                current_signature,
                wait_seconds,
            )
            if next_signature == current_signature:
                if debug:
                    print(
                        f"link page {page_number}: could not advance to {page_number + 1}",
                        file=sys.stderr,
                    )
                break
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
    debug: bool,
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
                    debug=debug,
                )
            else:
                rows = _tag_rows(await _collect_stable_link_rows(page, wait_seconds), 1)
        finally:
            await context.close()
            if browser is not None:
                await browser.close()

    candidates = []
    accepted_by_page: dict[int, int] = {}
    raw_by_page: dict[int, int] = {}
    for row in rows:
        page_number = int(row.get("pageNumber") or 1)
        raw_by_page[page_number] = raw_by_page.get(page_number, 0) + 1
        link_url = row.get("url", "")
        if looks_like_ad_popup_url(link_url):
            continue
        text = row.get("text", "")
        has_thumbnail = bool(row.get("hasThumbnail"))
        score = score_link(link_url, text, has_thumbnail, is_ad_url=False)
        if score >= min_score:
            accepted_by_page[page_number] = accepted_by_page.get(page_number, 0) + 1
            candidates.append(
                LinkCandidate(
                    url=link_url,
                    text=text,
                    has_thumbnail=has_thumbnail,
                    score=score,
                )
            )
    links = dedupe_links(candidates)
    if debug:
        for page_number in sorted(raw_by_page):
            print(
                "link page "
                f"{page_number}: accepted={accepted_by_page.get(page_number, 0)} "
                f"raw={raw_by_page[page_number]}",
                file=sys.stderr,
            )
        print(
            f"link extraction: accepted_total={len(candidates)} unique={len(links)}",
            file=sys.stderr,
        )
    return links


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
    debug: bool = False,
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
            debug=debug,
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
        debug: bool = False,
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
            debug=debug,
        )
