from __future__ import annotations

import asyncio
import re
import time
import xml.etree.ElementTree as ET
from collections.abc import Iterable
from typing import Any
from urllib.parse import urlparse

from .context import (
    browser_launch_options,
    desktop_context_options,
    harden_context,
    new_desktop_context,
)
from .protection import install_popup_protection
from video_downloader.domain.models import ProxySettings, StreamCandidate
from video_downloader.domain.scoring import content_score


STREAM_EXTENSIONS = (".m3u8", ".mpd", ".mp4", ".m4v", ".webm", ".mov")
IMAGE_EXTENSIONS = (".jpg", ".jpeg", ".png", ".gif", ".webp", ".avif")
VIDEO_CONTENT_TYPES = (
    "application/vnd.apple.mpegurl",
    "application/x-mpegurl",
    "application/dash+xml",
    "video/",
)
IMAGE_CONTENT_TYPES = ("image/",)


def looks_like_stream(url: str, content_type: str = "") -> bool:
    path = urlparse(url).path.lower().rstrip("/")
    lower_type = content_type.lower()
    if any(path.endswith(ext) for ext in IMAGE_EXTENSIONS) or any(
        marker in lower_type for marker in IMAGE_CONTENT_TYPES
    ):
        return False
    return any(path.endswith(ext) for ext in STREAM_EXTENSIONS) or any(
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


def cookie_header_for_host(cookies: list[dict[str, Any]], host: str) -> str:
    values = []
    normalized_host = host.lower()
    for cookie in cookies:
        domain = str(cookie.get("domain", "")).lstrip(".").lower()
        name = cookie.get("name")
        value = cookie.get("value")
        if not domain or not name:
            continue
        if normalized_host == domain or normalized_host.endswith(f".{domain}"):
            values.append(f"{name}={value}")
    return "; ".join(values)


class BrowserStreamSniffer:
    def __init__(
        self,
        *,
        headless: bool = True,
        user_agent: str | None = None,
        play_seconds: float = 25,
        allow_popups: bool = False,
        proxy_settings: ProxySettings | None = None,
        auto_click: bool = True,
        user_data_dir: str | None = None,
        browser_channel: str | None = None,
        spoof_browser: bool = False,
        restore_blank: bool = True,
    ) -> None:
        self.headless = headless
        self.user_agent = user_agent
        self.play_seconds = play_seconds
        self.allow_popups = allow_popups
        self.proxy_settings = proxy_settings
        self.auto_click = auto_click
        self.user_data_dir = user_data_dir
        self.browser_channel = browser_channel
        self.spoof_browser = spoof_browser
        self.restore_blank = restore_blank

    async def sniff(self, url: str) -> list[StreamCandidate]:
        try:
            from playwright.async_api import async_playwright
        except ModuleNotFoundError as exc:
            raise RuntimeError(
                "Missing dependency: playwright. Install with `pip install -e .` "
                "and run `python -m playwright install chromium`."
            ) from exc

        started = time.monotonic()
        candidates: list[StreamCandidate] = []
        pending: set[asyncio.Task[None]] = set()
        observed_pages: list[Any] = []

        async with async_playwright() as p:
            proxy_url = self.proxy_settings.proxy_url if self.proxy_settings else None
            browser = None
            if self.user_data_dir:
                context = await p.chromium.launch_persistent_context(
                    self.user_data_dir,
                    **browser_launch_options(
                        headless=self.headless,
                        proxy_url=proxy_url,
                        browser_channel=self.browser_channel,
                        spoof_browser=self.spoof_browser,
                    ),
                    **desktop_context_options(
                        user_agent=self.user_agent,
                        spoof_browser=self.spoof_browser,
                    ),
                )
                if self.spoof_browser:
                    await harden_context(context)
            else:
                browser = await p.chromium.launch(
                    **browser_launch_options(
                        headless=self.headless,
                        proxy_url=proxy_url,
                        browser_channel=self.browser_channel,
                        spoof_browser=self.spoof_browser,
                    )
                )
                context = await new_desktop_context(
                    browser,
                    user_agent=self.user_agent,
                    spoof_browser=self.spoof_browser,
                )
            await install_popup_protection(context, allow_popups=self.allow_popups)

            def schedule(response: Any) -> None:
                task = asyncio.create_task(
                    self._handle_response(response, candidates, started)
                )
                pending.add(task)
                task.add_done_callback(pending.discard)

            def attach_page(page: Any) -> None:
                if page not in observed_pages:
                    observed_pages.append(page)
                page.on("response", schedule)

            context.on("page", attach_page)
            page = (
                context.pages[0]
                if self.user_data_dir and context.pages
                else await context.new_page()
            )
            attach_page(page)
            await self._open_and_play(page, url)
            await asyncio.sleep(self.play_seconds)

            if pending:
                await asyncio.wait(pending, timeout=10)

            page_title = await self._page_title(observed_pages)
            if page_title:
                for candidate in candidates:
                    if not candidate.page_title:
                        candidate.page_title = page_title
            cookies = await context.cookies()
            for candidate in candidates:
                if "cookie" not in candidate.request_headers:
                    cookie_header = cookie_header_for_host(cookies, candidate.host)
                    if cookie_header:
                        candidate.request_headers["cookie"] = cookie_header

            await self._close(context, browser)

        unique = dedupe_candidates(candidates)
        return sorted(unique, key=content_score, reverse=True)

    async def _page_title(self, pages: list[Any]) -> str:
        for page in reversed(pages):
            try:
                if page.is_closed() or page.url == "about:blank":
                    continue
                title = (await page.title()).strip()
                if title:
                    return title
            except Exception:
                continue
        return ""

    async def _open_and_play(self, page: Any, url: str) -> None:
        try:
            await page.goto(url, wait_until="domcontentloaded", timeout=60000)
        except Exception:
            return
        await asyncio.sleep(1.5)
        await self._restore_blank_page(page, url)
        if not self.auto_click:
            return

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
                    await self._restore_blank_page(page, url)
            except Exception:
                pass

        if self.headless:
            try:
                await page.keyboard.press("Space")
                await self._restore_blank_page(page, url)
            except Exception:
                pass

            try:
                box = page.viewport_size or {"width": 1365, "height": 900}
                await page.mouse.click(box["width"] / 2, box["height"] / 2)
                await self._restore_blank_page(page, url)
            except Exception:
                pass

    async def _restore_blank_page(self, page: Any, url: str) -> None:
        if not self.restore_blank or page.url != "about:blank":
            return
        if not self.headless:
            return
        try:
            await page.goto(url, wait_until="domcontentloaded", timeout=60000)
            await page.wait_for_timeout(1000)
        except Exception:
            pass

    async def _handle_response(
        self,
        response: Any,
        candidates: list[StreamCandidate],
        started: float,
    ) -> None:
        headers = await response.all_headers()
        content_type = headers.get("content-type", "")
        if not looks_like_stream(response.url, content_type):
            return

        request = response.request
        if request.resource_type == "image":
            return
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

    async def _close(self, context: Any, browser: Any) -> None:
        await context.close()
        if browser is not None:
            await browser.close()


def sniff_streams(
    url: str,
    *,
    headless: bool = True,
    user_agent: str | None = None,
    play_seconds: float = 25,
    allow_popups: bool = False,
    proxy_settings: ProxySettings | None = None,
    auto_click: bool = True,
    user_data_dir: str | None = None,
    browser_channel: str | None = None,
    spoof_browser: bool = False,
    restore_blank: bool = True,
) -> list[StreamCandidate]:
    sniffer = BrowserStreamSniffer(
        headless=headless,
        user_agent=user_agent,
        play_seconds=play_seconds,
        allow_popups=allow_popups,
        proxy_settings=proxy_settings,
        auto_click=auto_click,
        user_data_dir=user_data_dir,
        browser_channel=browser_channel,
        spoof_browser=spoof_browser,
        restore_blank=restore_blank,
    )
    return asyncio.run(sniffer.sniff(url))


class PlaywrightStreamSniffer:
    def sniff_streams(
        self,
        url: str,
        *,
        headless: bool = True,
        user_agent: str | None = None,
        play_seconds: float = 25,
        allow_popups: bool = False,
        proxy_settings: ProxySettings | None = None,
        auto_click: bool = True,
        user_data_dir: str | None = None,
        browser_channel: str | None = None,
        spoof_browser: bool = False,
        restore_blank: bool = True,
    ) -> list[StreamCandidate]:
        return sniff_streams(
            url,
            headless=headless,
            user_agent=user_agent,
            play_seconds=play_seconds,
            allow_popups=allow_popups,
            proxy_settings=proxy_settings,
            auto_click=auto_click,
            user_data_dir=user_data_dir,
            browser_channel=browser_channel,
            spoof_browser=spoof_browser,
            restore_blank=restore_blank,
        )
