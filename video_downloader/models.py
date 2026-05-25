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

    @property
    def host(self) -> str:
        return urlparse(self.url).hostname or ""

    @property
    def path(self) -> str:
        return urlparse(self.url).path.lower()

    @property
    def kind(self) -> str:
        lower_url = self.url.lower()
        lower_type = self.content_type.lower()
        if ".m3u8" in lower_url or "mpegurl" in lower_type:
            return "hls"
        if ".mpd" in lower_url or "dash+xml" in lower_type:
            return "dash"
        if any(ext in lower_url for ext in (".mp4", ".m4v", ".webm", ".mov")):
            return "file"
        if lower_type.startswith("video/"):
            return "file"
        return "unknown"
