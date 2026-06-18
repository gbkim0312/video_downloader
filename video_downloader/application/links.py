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
    user_data_dir: str | None = None
    browser_channel: str | None = None
    spoof_browser: bool = False
    block_devtool_detectors: bool = False
    page_start: int | None = None
    page_end: int | None = None
    debug: bool = False


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
            user_data_dir=options.user_data_dir,
            browser_channel=options.browser_channel,
            spoof_browser=options.spoof_browser,
            block_devtool_detectors=options.block_devtool_detectors,
            page_start=options.page_start,
            page_end=options.page_end,
            debug=options.debug,
        )

    def append_links(self, output_path: str, links: list[LinkCandidate]) -> None:
        self.url_store.append_urls(output_path, [candidate.url for candidate in links])
