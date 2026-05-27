from __future__ import annotations

from .adapters.browser.link_extractor import PlaywrightLinkExtractor, extract_video_links
from .adapters.browser.protection import looks_like_ad_popup_url
from .domain.models import LinkCandidate
from .domain.scoring import dedupe_links, score_link as _score_link


def score_link(url: str, text: str, has_thumbnail: bool) -> int:
    return _score_link(
        url,
        text,
        has_thumbnail,
        is_ad_url=looks_like_ad_popup_url(url),
    )

__all__ = [
    "LinkCandidate",
    "PlaywrightLinkExtractor",
    "dedupe_links",
    "extract_video_links",
    "score_link",
]
