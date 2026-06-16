from __future__ import annotations

from typing import Protocol

from video_downloader.domain.models import (
    LinkCandidate,
    ProxySettings,
    StreamCandidate,
    SubtitleCandidate,
)


class StreamSnifferPort(Protocol):
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
        block_devtool_detectors: bool = False,
        debug_log_path: str | None = None,
        subtitle_candidates: list[SubtitleCandidate] | None = None,
    ) -> list[StreamCandidate]:
        """Return playable media stream candidates observed from a browser page."""


class LinkExtractorPort(Protocol):
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
        """Return likely video page links observed in a browser page."""
