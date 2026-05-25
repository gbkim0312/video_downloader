from __future__ import annotations

import asyncio
import re
import time
import xml.etree.ElementTree as ET
from collections.abc import Iterable

from playwright.async_api import Browser, BrowserContext, Page, Response, async_playwright

from .models import StreamCandidate
from .scoring import content_score


VIDEO_EXTENSIONS = (".m3u8", ".mpd", ".mp4", ".m4v", ".webm", ".mov")
VIDEO_CONTENT_TYPES = (
    "application/vnd.apple.mpegurl",
    "application/x-mpegurl",
    "application/dash+xml",
    "video/",
)


def looks_like_stream(url: str, content_type: str = "") -> bool:
    lower_url = url.lower()
    lower_type = content_type.lower()
    return any(ext in lower_url for ext in VIDEO_EXTENSIONS) or any(
        marker in lower_type for marker in VIDEO_CONTENT_TYPES
    )


def parse_content_length(headers: dict[str, str]) -> int | None:
    value = headers.get("content-length") or headers.get("Content-Length")
    if not value:
        return None
    try:
        return int(value)
    except ValueError:
        return None


def parse_hls_duration(text: str) -> float | None:
    durations = [float(match) for match in re.findall(r"#EXTINF:([0-9.]+)", text)]
    if durations:
        return sum(durations)
    return None


def parse_iso8601_duration(value: str) -> float | None:
    match = re.fullmatch(
        r"P(?:(?P<days>\d+(?:\.\d+)?)D)?"
        r"(?:T(?:(?P<hours>\d+(?:\.\d+)?)H)?"
        r"(?:(?P<minutes>\d+(?:\.\d+)?)M)?"
        r"(?:(?P<seconds>\d+(?:\.\d+)?)S)?)?",
        value,
    )
    if not match:
        return None
    days = float(match.group("days") or 0)
    hours = float(match.group("hours") or 0)
    minutes = float(match.group("minutes") or 0)
    seconds = float(match.group("seconds") or 0)
    return days * 86400 + hours * 3600 + minutes * 60 + seconds


def parse_dash_duration(text: str) -> float | None:
    try:
        root = ET.fromstring(text)
    except ET.ParseError:
        return None
    duration = root.attrib.get("mediaPresentationDuration")
    if duration:
        return parse_iso8601_duration(duration)
    return None


def dedupe_candidates(candidates: Iterable[StreamCandidate]) -> list[StreamCandidate]:
    by_url: dict[str, StreamCandidate] = {}
    for candidate in candidates:
        existing = by_url.get(candidate.url)
        if existing is None:
            by_url[candidate.url] = candidate
            continue
        if existing.duration is None and candidate.duration is not None:
            existing.duration = candidate.duration
        if existing.byte_length is None and candidate.byte_length is not None:
            existing.byte_length = candidate.byte_length
    return list(by_url.values())


class BrowserStreamSniffer:
    def __init__(
        self,
        *,
        headless: bool = True,
        user_agent: str | None = None,
        play_seconds: float = 25,
    ) -> None:
        self.headless = headless
        self.user_agent = user_agent
        self.play_seconds = play_seconds

    async def sniff(self, url: str) -> list[StreamCandidate]:
        started = time.monotonic()
        candidates: list[StreamCandidate] = []
        pending: set[asyncio.Task[None]] = set()

        async with async_playwright() as p:
            browser = await p.chromium.launch(headless=self.headless)
            context = await browser.new_context(
                user_agent=self.user_agent,
                viewport={"width": 1365, "height": 900},
            )
            page = await context.new_page()

            def schedule(response: Response) -> None:
                task = asyncio.create_task(
                    self._handle_response(response, candidates, started)
                )
                pending.add(task)
                task.add_done_callback(pending.discard)

            page.on("response", schedule)
            await self._open_and_play(page, url)
            await page.wait_for_timeout(int(self.play_seconds * 1000))

            if pending:
                await asyncio.wait(pending, timeout=10)

            page_title = (await page.title()).strip()
            if page_title:
                for candidate in candidates:
                    if not candidate.page_title:
                        candidate.page_title = page_title

            await self._close(context, browser)

        unique = dedupe_candidates(candidates)
        return sorted(unique, key=content_score, reverse=True)

    async def _open_and_play(self, page: Page, url: str) -> None:
        await page.goto(url, wait_until="domcontentloaded", timeout=60000)
        await page.wait_for_timeout(1500)

        selectors = [
            'button[aria-label*="Play" i]',
            'button[title*="Play" i]',
            '[role="button"][aria-label*="Play" i]',
            "video",
        ]
        for selector in selectors:
            try:
                element = page.locator(selector).first
                if await element.count():
                    await element.click(timeout=1500, force=True)
                    await page.wait_for_timeout(1000)
            except Exception:
                pass

        try:
            await page.keyboard.press("Space")
        except Exception:
            pass

        try:
            box = page.viewport_size or {"width": 1365, "height": 900}
            await page.mouse.click(box["width"] / 2, box["height"] / 2)
        except Exception:
            pass

    async def _handle_response(
        self,
        response: Response,
        candidates: list[StreamCandidate],
        started: float,
    ) -> None:
        headers = await response.all_headers()
        content_type = headers.get("content-type", "")
        if not looks_like_stream(response.url, content_type):
            return

        request = response.request
        request_headers = await request.all_headers()
        candidate = StreamCandidate(
            url=response.url,
            content_type=content_type,
            method=request.method,
            resource_type=request.resource_type,
            referer=request_headers.get("referer", ""),
            user_agent=request_headers.get("user-agent", ""),
            byte_length=parse_content_length(headers),
            discovered_at=time.monotonic() - started,
            request_headers=request_headers,
            response_headers=headers,
        )

        if candidate.kind in {"hls", "dash"}:
            try:
                text = await response.text()
            except Exception:
                text = ""
            if candidate.kind == "hls":
                candidate.duration = parse_hls_duration(text)
            elif candidate.kind == "dash":
                candidate.duration = parse_dash_duration(text)

        candidates.append(candidate)

    async def _close(self, context: BrowserContext, browser: Browser) -> None:
        try:
            await context.close()
        finally:
            await browser.close()


def sniff_streams(
    url: str,
    *,
    headless: bool = True,
    user_agent: str | None = None,
    play_seconds: float = 25,
) -> list[StreamCandidate]:
    sniffer = BrowserStreamSniffer(
        headless=headless,
        user_agent=user_agent,
        play_seconds=play_seconds,
    )
    return asyncio.run(sniffer.sniff(url))
