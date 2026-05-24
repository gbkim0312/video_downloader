from __future__ import annotations

from pathlib import Path

from .models import StreamCandidate


def default_output_template(output_dir: str | Path) -> str:
    path = Path(output_dir)
    return str(path / "%(title).200B.%(ext)s")


def download_url(
    url: str,
    *,
    output_template: str,
    headers: dict[str, str] | None = None,
    quiet: bool = False,
) -> None:
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
        "concurrent_fragment_downloads": 4,
    }
    if headers:
        options["http_headers"] = headers

    with YoutubeDL(options) as ydl:
        ydl.download([url])


def download_candidate(
    candidate: StreamCandidate,
    *,
    output_template: str,
    quiet: bool = False,
) -> None:
    headers = {}
    if candidate.user_agent:
        headers["User-Agent"] = candidate.user_agent
    if candidate.referer:
        headers["Referer"] = candidate.referer
    download_url(
        candidate.url,
        output_template=output_template,
        headers=headers,
        quiet=quiet,
    )
