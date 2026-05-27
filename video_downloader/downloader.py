from __future__ import annotations

from pathlib import Path
import re
from typing import Any, Callable

from .models import StreamCandidate


ProgressHook = Callable[[str, dict[str, Any]], None]
DOWNLOAD_DOWNLOADED = "downloaded"
DOWNLOAD_SKIPPED = "skipped"


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


def download_url(
    url: str,
    *,
    output_template: str,
    headers: dict[str, str] | None = None,
    quiet: bool = False,
    progress_hook: ProgressHook | None = None,
    progress_label: str = "",
    fragment_parallel: int = 4,
) -> str:
    try:
        from yt_dlp import YoutubeDL
    except ModuleNotFoundError as exc:
        raise RuntimeError(
            "Missing dependency: yt-dlp. Install with `pip install -e .`."
        ) from exc

    options = {
        "outtmpl": output_template,
        "format": "bestvideo+bestaudio/best",
        "merge_output_format": "mp4",
        "noplaylist": True,
        "quiet": quiet,
        "no_warnings": quiet,
        "retries": 8,
        "fragment_retries": 8,
        "concurrent_fragment_downloads": fragment_parallel,
    }
    saw_download_activity = False

    def handle_progress(status: dict[str, Any]) -> None:
        nonlocal saw_download_activity
        if status.get("status") in {"downloading", "finished"}:
            saw_download_activity = True
        if progress_hook:
            progress_hook(progress_label or url, status)

    if progress_hook:
        options["progress_hooks"] = [
            handle_progress
        ]
    else:
        options["progress_hooks"] = [handle_progress]
    if headers:
        options["http_headers"] = headers

    with YoutubeDL(options) as ydl:
        ydl.download([url])
    return DOWNLOAD_DOWNLOADED if saw_download_activity else DOWNLOAD_SKIPPED


def download_candidate(
    candidate: StreamCandidate,
    *,
    output_template: str,
    quiet: bool = False,
    progress_hook: ProgressHook | None = None,
    progress_label: str = "",
    fragment_parallel: int = 4,
) -> str:
    headers = {}
    if candidate.user_agent:
        headers["User-Agent"] = candidate.user_agent
    if candidate.referer:
        headers["Referer"] = candidate.referer
    return download_url(
        candidate.url,
        output_template=output_template,
        headers=headers,
        quiet=quiet,
        progress_hook=progress_hook,
        progress_label=progress_label,
        fragment_parallel=fragment_parallel,
    )
