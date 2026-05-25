from __future__ import annotations

import asyncio
import re
from dataclasses import dataclass
from urllib.parse import urlparse

from .browser_protection import install_popup_protection, looks_like_ad_popup_url


VIDEO_URL_HINTS = (
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

VIDEO_TEXT_HINTS = (
    "watch",
    "video",
    "play",
    "episode",
    "lecture",
    "lesson",
    "view",
)


@dataclass(frozen=True, slots=True)
class LinkCandidate:
    url: str
    text: str = ""
    has_thumbnail: bool = False
    score: int = 0


def score_link(url: str, text: str, has_thumbnail: bool) -> int:
    if looks_like_ad_popup_url(url):
        return -100

    parsed = urlparse(url)
    haystack = " ".join([parsed.path, parsed.query, text]).lower()
    score = 0
    if has_thumbnail:
        score += 6
    for hint in VIDEO_URL_HINTS:
        if hint in haystack:
            score += 2
    for hint in VIDEO_TEXT_HINTS:
        if hint in text.lower():
            score += 1
    if re.search(r"/(?:watch|video|videos|episode|lecture|lesson)s?[/=?-]", haystack):
        score += 4
    if parsed.fragment:
        score -= 1
    if parsed.scheme not in {"http", "https"}:
        score -= 10
    return score


def dedupe_links(candidates: list[LinkCandidate]) -> list[LinkCandidate]:
    by_url: dict[str, LinkCandidate] = {}
    for candidate in candidates:
        existing = by_url.get(candidate.url)
        if existing is None or candidate.score > existing.score:
            by_url[candidate.url] = candidate
    return sorted(by_url.values(), key=lambda item: item.score, reverse=True)


async def _extract_links(
    url: str,
    *,
    headless: bool,
    user_agent: str | None,
    min_score: int,
    wait_seconds: float,
    allow_popups: bool,
) -> list[LinkCandidate]:
    try:
        from playwright.async_api import async_playwright
    except ModuleNotFoundError as exc:
        raise RuntimeError(
            "Missing dependency: playwright. Install with `pip install -e .` "
            "and run `python -m playwright install chromium`."
        ) from exc

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=headless)
        context = await browser.new_context(
            user_agent=user_agent,
            viewport={"width": 1365, "height": 900},
        )
        await install_popup_protection(context, allow_popups=allow_popups)
        page = await context.new_page()
        try:
            await page.goto(url, wait_until="domcontentloaded", timeout=60000)
            await page.wait_for_timeout(int(wait_seconds * 1000))
            rows = await page.locator("a[href]").evaluate_all(
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
        finally:
            await context.close()
            await browser.close()

    candidates = []
    for row in rows:
        link_url = row.get("url", "")
        if looks_like_ad_popup_url(link_url):
            continue
        text = row.get("text", "")
        has_thumbnail = bool(row.get("hasThumbnail"))
        score = score_link(link_url, text, has_thumbnail)
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
) -> list[LinkCandidate]:
    return asyncio.run(
        _extract_links(
            url,
            headless=headless,
            user_agent=user_agent,
            min_score=min_score,
            wait_seconds=wait_seconds,
            allow_popups=allow_popups,
        )
    )
