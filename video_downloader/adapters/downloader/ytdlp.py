from __future__ import annotations

import re
import tempfile
from pathlib import Path
from typing import Any, Callable
from urllib.parse import urljoin

from video_downloader.domain.models import ProxySettings, StreamCandidate


ProgressHook = Callable[[str, dict[str, Any]], None]
DOWNLOAD_DOWNLOADED = "downloaded"
DOWNLOAD_SKIPPED = "skipped"
FORWARDED_HEADER_NAMES = {
    "accept",
    "accept-language",
    "cookie",
    "origin",
    "referer",
    "sec-ch-ua",
    "sec-ch-ua-mobile",
    "sec-ch-ua-platform",
    "sec-fetch-dest",
    "sec-fetch-mode",
    "sec-fetch-site",
    "user-agent",
}


def sanitize_filename(value: str, *, max_length: int = 200) -> str:
    cleaned = re.sub(r'[\\/:*?"<>|\x00-\x1f]', " ", value)
    cleaned = re.sub(r"\s+", " ", cleaned).strip(" .")
    return cleaned[:max_length].strip(" .")


def default_output_template(output_dir: str | Path, title: str = "") -> str:
    path = Path(output_dir)
    safe_title = sanitize_filename(title)
    if safe_title:
        return str(path / f"{safe_title}.%(ext)s")
    return str(path / "%(title).200B.%(ext)s")


def headers_for_candidate(candidate: StreamCandidate) -> dict[str, str]:
    headers: dict[str, str] = {}
    for name, value in candidate.request_headers.items():
        lower_name = name.lower()
        if lower_name in FORWARDED_HEADER_NAMES and value:
            headers[_canonical_header_name(lower_name)] = value
    if candidate.user_agent:
        headers["User-Agent"] = candidate.user_agent
    if candidate.referer:
        headers["Referer"] = candidate.referer
    return headers


def _canonical_header_name(name: str) -> str:
    return "-".join(part.capitalize() for part in name.split("-"))


def rewrite_hls_manifest_urls(text: str, base_url: str) -> str:
    uri_pattern = re.compile(r'URI="([^"]+)"')
    rewritten: list[str] = []
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped:
            rewritten.append(line)
            continue
        if stripped.startswith("#"):
            rewritten.append(
                uri_pattern.sub(
                    lambda match: f'URI="{urljoin(base_url, match.group(1))}"',
                    line,
                )
            )
            continue
        rewritten.append(urljoin(base_url, stripped))
    return "\n".join(rewritten) + "\n"


class YtDlpDownloader:
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
        try:
            from yt_dlp import YoutubeDL
        except ModuleNotFoundError as exc:
            raise RuntimeError(
                "Missing dependency: yt-dlp. Install with `pip install -e .`."
            ) from exc

        saw_download_activity = False

        def handle_progress(status: dict[str, Any]) -> None:
            nonlocal saw_download_activity
            if status.get("status") in {"downloading", "finished"}:
                saw_download_activity = True
            if progress_hook:
                progress_hook(progress_label or url, status)

        if progress_hook:
            progress_hook(
                progress_label or url,
                {
                    "status": "downloading",
                    "downloaded_bytes": 0,
                    "total_bytes": None,
                    "total_bytes_estimate": None,
                    "speed": None,
                },
            )

        options = {
            "outtmpl": output_template,
            "format": "bestvideo+bestaudio/best",
            "merge_output_format": "mp4",
            "noplaylist": True,
            "quiet": quiet,
            "no_warnings": quiet,
            "retries": 8,
            "fragment_retries": 8,
            "hls_prefer_native": False,
            "concurrent_fragment_downloads": fragment_parallel,
            "progress_hooks": [handle_progress],
            "external_downloader_args": {
                "ffmpeg_i": ["-hide_banner", "-loglevel", "error"],
            },
            "postprocessor_args": {
                "ffmpeg": ["-hide_banner", "-loglevel", "error"],
            },
        }
        if Path(url).exists():
            options["enable_file_urls"] = True
        if headers:
            options["http_headers"] = headers
        if proxy_settings:
            options["proxy"] = proxy_settings.proxy_url

        with YoutubeDL(options) as ydl:
            ydl.download([url])
        return DOWNLOAD_DOWNLOADED if saw_download_activity else DOWNLOAD_SKIPPED

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
        headers = headers_for_candidate(candidate)
        if candidate.kind == "hls" and candidate.manifest_text.startswith("#EXTM3U"):
            with tempfile.TemporaryDirectory(prefix="video-dl-hls-") as temp_dir:
                manifest_path = Path(temp_dir) / "manifest.m3u8"
                manifest_path.write_text(
                    rewrite_hls_manifest_urls(candidate.manifest_text, candidate.url),
                    encoding="utf-8",
                )
                return self.download_url(
                    str(manifest_path),
                    output_template=output_template,
                    headers=headers,
                    quiet=quiet,
                    progress_hook=progress_hook,
                    progress_label=progress_label,
                    fragment_parallel=fragment_parallel,
                    proxy_settings=proxy_settings,
                )
        return self.download_url(
            candidate.url,
            output_template=output_template,
            headers=headers,
            quiet=quiet,
            progress_hook=progress_hook,
            progress_label=progress_label,
            fragment_parallel=fragment_parallel,
            proxy_settings=proxy_settings,
        )
