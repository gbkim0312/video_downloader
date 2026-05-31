from __future__ import annotations

from pathlib import Path
from typing import Any, Callable, Protocol

from video_downloader.domain.models import ProxySettings, StreamCandidate, SubtitleCandidate

ProgressHook = Callable[[str, dict[str, Any]], None]


class MediaDownloaderPort(Protocol):
    def download_url(
        self,
        url: str,
        *,
        output_template: str,
        headers: dict[str, str] | None = None,
        quiet: bool = False,
        progress_hook: ProgressHook | None = None,
        progress_label: str = "",
        fragment_parallel: int = 4,
        proxy_settings: ProxySettings | None = None,
    ) -> str:
        """Download a URL and return a result status."""

    def download_candidate(
        self,
        candidate: StreamCandidate,
        *,
        output_template: str,
        quiet: bool = False,
        progress_hook: ProgressHook | None = None,
        progress_label: str = "",
        fragment_parallel: int = 4,
        proxy_settings: ProxySettings | None = None,
    ) -> str:
        """Download a selected stream candidate and return a result status."""

    def download_subtitle(
        self,
        subtitle: SubtitleCandidate,
        *,
        output_template: str,
        index: int = 1,
        proxy_settings: ProxySettings | None = None,
    ) -> Path:
        """Download a selected subtitle candidate and return its output path."""
