from __future__ import annotations

from dataclasses import dataclass

from video_downloader.domain.models import LinkCandidate, ProxySettings
from video_downloader.ports.browser import LinkExtractorPort
from video_downloader.ports.storage import UrlListStorePort


@dataclass(frozen=True, slots=True)
class LinkExtractionOptions:
    headless: bool = True
    user_agent: str | None = None
    min_score: int = 6
    wait_seconds: float = 3
    allow_popups: bool = False
    quiet: bool = False
    proxy_settings: ProxySettings | None = None


class LinkExtractionService:
    def __init__(
        self,
        *,
        extractor: LinkExtractorPort,
        url_store: UrlListStorePort,
    ) -> None:
        self.extractor = extractor
        self.url_store = url_store

    def extract(
        self,
        url: str,
        *,
        options: LinkExtractionOptions,
    ) -> list[LinkCandidate]:
        return self.extractor.extract_video_links(
            url,
            headless=options.headless,
            user_agent=options.user_agent,
            min_score=options.min_score,
            wait_seconds=options.wait_seconds,
            allow_popups=options.allow_popups,
            proxy_settings=options.proxy_settings,
        )

    def append_links(self, output_path: str, links: list[LinkCandidate]) -> None:
        self.url_store.append_urls(output_path, [candidate.url for candidate in links])
