from __future__ import annotations

from dataclasses import dataclass, field
from urllib.parse import urlparse


@dataclass(slots=True)
class StreamCandidate:
    url: str
    content_type: str = ""
    method: str = "GET"
    resource_type: str = ""
    referer: str = ""
    user_agent: str = ""
    page_title: str = ""
    duration: float | None = None
    byte_length: int | None = None
    discovered_at: float = 0.0
    request_headers: dict[str, str] = field(default_factory=dict)
    response_headers: dict[str, str] = field(default_factory=dict)
    manifest_text: str = ""

    @property
    def host(self) -> str:
        return urlparse(self.url).hostname or ""

    @property
    def path(self) -> str:
        return urlparse(self.url).path.lower()

    @property
    def kind(self) -> str:
        lower_path = urlparse(self.url).path.lower().rstrip("/")
        lower_type = self.content_type.lower()
        if lower_path.endswith(".m3u8") or "mpegurl" in lower_type:
            return "hls"
        if lower_path.endswith(".mpd") or "dash+xml" in lower_type:
            return "dash"
        if any(lower_path.endswith(ext) for ext in (".mp4", ".m4v", ".webm", ".mov")):
            return "file"
        if lower_type.startswith("video/"):
            return "file"
        return "unknown"


@dataclass(frozen=True, slots=True)
class LinkCandidate:
    url: str
    text: str = ""
    has_thumbnail: bool = False
    score: int = 0


@dataclass(frozen=True, slots=True)
class BatchJob:
    index: int
    url: str


@dataclass(frozen=True, slots=True)
class BatchSummary:
    downloaded: int
    skipped: int
    failed: int
    total: int
    failed_jobs: tuple[BatchJob, ...] = ()


@dataclass(frozen=True, slots=True)
class ProxySettings:
    proxy_url: str
    control_host: str = "127.0.0.1"
    control_port: int = 9051
    control_password: str | None = None
    newnym_delay: float = 10
    rotation_retries: int = 1
    rotate_on_status: tuple[int, ...] = (403, 429, 500, 502, 503, 504)
    ip_check_url: str = "https://api.ipify.org"
    kill_switch: bool = True
    connect_retries: int = 3
