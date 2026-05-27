from __future__ import annotations

from typing import Any, Callable, Protocol

from video_downloader.domain.models import StreamCandidate

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
    ) -> str:
        """Download a selected stream candidate and return a result status."""
