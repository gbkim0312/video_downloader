from __future__ import annotations

import re
import tempfile
import threading
import time
from pathlib import Path
from typing import Any, Callable
from urllib.parse import urljoin, urlparse

from video_downloader.domain.models import ProxySettings, StreamCandidate, SubtitleCandidate


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


def headers_for_subtitle(candidate: SubtitleCandidate) -> dict[str, str]:
    headers: dict[str, str] = {}
    for name, value in candidate.request_headers.items():
        lower_name = name.lower()
        if lower_name in FORWARDED_HEADER_NAMES and value:
            headers[_canonical_header_name(lower_name)] = value
    return headers


def ffmpeg_input_args(headers: dict[str, str] | None) -> list[str]:
    args = ["-hide_banner", "-loglevel", "error"]
    if not headers:
        return args

    header_lines = []
    for name, value in headers.items():
        clean_name = name.replace("\r", "").replace("\n", "")
        clean_value = value.replace("\r", "").replace("\n", "")
        if clean_name and clean_value:
            header_lines.append(f"{clean_name}: {clean_value}\r\n")
    if header_lines:
        args.extend(["-headers", "".join(header_lines)])
    return args


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


def is_file_input(url: str) -> bool:
    parsed = urlparse(url)
    if parsed.scheme == "file":
        return True
    if parsed.scheme:
        return False
    return Path(url).exists()


def subtitle_output_path(
    output_template: str,
    subtitle: SubtitleCandidate,
    *,
    index: int = 1,
) -> Path:
    extension = subtitle.extension or "srt"
    path_text = output_template
    path_text = re.sub(r"%\(ext\)(?:[.#0 +\-]?\d*)?[A-Za-z]", extension, path_text)
    path_text = re.sub(r"%\([^)]+\)(?:[.#0 +\-]?\d*)?[A-Za-z]", "subtitle", path_text)
    path = Path(path_text)
    if index > 1:
        path = path.with_name(f"{path.stem}.{index}{path.suffix}")
    return path


def _output_template_to_glob(output_template: str) -> str:
    placeholder_pattern = re.compile(r"%\([^)]+\)(?:[.#0 +\-]?\d*)?[A-Za-z]")
    return placeholder_pattern.sub("*", output_template)


def _can_monitor_output_template(output_template: str) -> bool:
    placeholder_names = re.findall(r"%\(([^)]+)\)", output_template)
    return all(name == "ext" for name in placeholder_names)


class FileSizeProgressMonitor:
    def __init__(
        self,
        *,
        output_template: str,
        progress_label: str,
        progress_hook: ProgressHook,
        on_growth: Callable[[], None],
        should_emit: Callable[[], bool] | None = None,
        interval: float = 0.5,
    ) -> None:
        self.output_template = output_template
        self.progress_label = progress_label
        self.progress_hook = progress_hook
        self.on_growth = on_growth
        self.should_emit = should_emit
        self.interval = interval
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._last_size = 0
        self._last_time = 0.0

    def start(self) -> None:
        self._last_size = self._current_size() or 0
        self._last_time = time.monotonic()
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=1)
        self._emit_progress()

    def _run(self) -> None:
        while not self._stop.wait(self.interval):
            self._emit_progress()

    def _emit_progress(self) -> None:
        if self.should_emit is not None and not self.should_emit():
            return
        size = self._current_size()
        if size is None or size <= 0:
            return

        now = time.monotonic()
        elapsed = now - self._last_time if self._last_time else 0
        delta = size - self._last_size
        speed = delta / elapsed if delta > 0 and elapsed > 0 else None
        if delta > 0:
            self.on_growth()
        if delta == 0 and self._last_size:
            return

        self._last_size = size
        self._last_time = now
        self.progress_hook(
            self.progress_label,
            {
                "status": "downloading",
                "downloaded_bytes": size,
                "total_bytes": None,
                "total_bytes_estimate": None,
                "speed": speed,
            },
        )

    def _current_size(self) -> int | None:
        files = self._matching_files()
        if not files:
            return None

        aggregate_files = [
            path
            for path in files
            if ".part-Frag" not in path.name and not path.name.endswith(".ytdl")
        ]
        if aggregate_files:
            sizes = [self._safe_size(path) for path in aggregate_files]
            return max((size for size in sizes if size is not None), default=None)

        fragment_files = [path for path in files if ".part-Frag" in path.name]
        if fragment_files:
            sizes = [self._safe_size(path) for path in fragment_files]
            return sum(size for size in sizes if size is not None)
        return None

    def _safe_size(self, path: Path) -> int | None:
        try:
            return path.stat().st_size
        except OSError:
            return None

    def _matching_files(self) -> list[Path]:
        pattern = _output_template_to_glob(self.output_template)
        path_pattern = Path(pattern)
        parent = path_pattern.parent
        name_pattern = path_pattern.name
        if not str(parent):
            parent = Path(".")
        if not parent.exists():
            return []

        if any(char in name_pattern for char in "*?["):
            paths = list(parent.glob(name_pattern))
        else:
            paths = [path_pattern]

        return [
            path
            for path in paths
            if path.exists() and path.is_file() and not path.name.endswith(".json")
        ]


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
        saw_file_growth = False
        native_progress_seen = False

        def handle_progress(status: dict[str, Any]) -> None:
            nonlocal native_progress_seen, saw_download_activity
            if status.get("status") in {"downloading", "finished"}:
                saw_download_activity = True
            if (
                status.get("status") == "downloading"
                and status.get("downloaded_bytes")
            ):
                native_progress_seen = True
            if progress_hook:
                progress_hook(progress_label or url, status)

        def mark_file_growth() -> None:
            nonlocal saw_file_growth
            saw_file_growth = True

        def should_emit_file_progress() -> bool:
            return not native_progress_seen

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
                "ffmpeg_i": ffmpeg_input_args(headers),
            },
            "postprocessor_args": {
                "ffmpeg": ["-hide_banner", "-loglevel", "error"],
            },
        }
        if is_file_input(url):
            options["enable_file_urls"] = True
        if headers:
            options["http_headers"] = headers
        if proxy_settings:
            options["proxy"] = proxy_settings.proxy_url

        monitor = None
        if progress_hook and _can_monitor_output_template(output_template):
            monitor = FileSizeProgressMonitor(
                output_template=output_template,
                progress_label=progress_label or url,
                progress_hook=progress_hook,
                on_growth=mark_file_growth,
                should_emit=should_emit_file_progress,
            )
            monitor.start()

        with YoutubeDL(options) as ydl:
            try:
                ydl.download([url])
            finally:
                if monitor is not None:
                    monitor.stop()
        return DOWNLOAD_DOWNLOADED if saw_download_activity or saw_file_growth else DOWNLOAD_SKIPPED

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
                    manifest_path.as_uri(),
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

    def download_subtitle(
        self,
        subtitle: SubtitleCandidate,
        *,
        output_template: str,
        index: int = 1,
        proxy_settings: ProxySettings | None = None,
    ) -> Path:
        try:
            import requests
        except ModuleNotFoundError as exc:
            raise RuntimeError(
                "Missing dependency: requests. Install with `pip install -e .`."
            ) from exc

        output_path = subtitle_output_path(output_template, subtitle, index=index)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        headers = headers_for_subtitle(subtitle)
        proxies = None
        if proxy_settings:
            proxies = {
                "http": proxy_settings.proxy_url,
                "https": proxy_settings.proxy_url,
            }
        response = requests.get(
            subtitle.url,
            headers=headers,
            proxies=proxies,
            timeout=30,
        )
        response.raise_for_status()
        output_path.write_bytes(response.content)
        return output_path
