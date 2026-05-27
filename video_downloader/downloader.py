from __future__ import annotations

from .adapters.downloader.ytdlp import (
    DOWNLOAD_DOWNLOADED,
    DOWNLOAD_SKIPPED,
    YtDlpDownloader,
    default_output_template,
    sanitize_filename,
)

_downloader = YtDlpDownloader()
download_url = _downloader.download_url
download_candidate = _downloader.download_candidate

__all__ = [
    "DOWNLOAD_DOWNLOADED",
    "DOWNLOAD_SKIPPED",
    "YtDlpDownloader",
    "default_output_template",
    "download_candidate",
    "download_url",
    "sanitize_filename",
]
