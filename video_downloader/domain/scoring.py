from __future__ import annotations

import re
from urllib.parse import parse_qs, urlparse

from .models import LinkCandidate, StreamCandidate


AD_KEYWORDS = (
    "ad",
    "ads",
    "adservice",
    "adserver",
    "adsystem",
    "doubleclick",
    "googlesyndication",
    "googleads",
    "gampad",
    "imasdk",
    "pubads",
    "vast",
    "vpaid",
    "preroll",
    "midroll",
    "postroll",
    "companion",
    "sponsor",
)

CONTENT_HINTS = (
    "master",
    "manifest",
    "playlist",
    "index",
    "main",
    "video",
    "movie",
    "episode",
    "vod",
)

VIDEO_URL_HINTS = (
    "watch",
    "video",
    "videos",
    "play",
    "player",
    "episode",
    "vod",
    "lecture",
    "course",
    "media",
)

VIDEO_TEXT_HINTS = (
    "watch",
    "video",
    "play",
    "episode",
    "lecture",
    "lesson",
    "view",
)


def ad_score(candidate: StreamCandidate) -> int:
    parsed = urlparse(candidate.url)
    haystack = " ".join(
        [
            parsed.hostname or "",
            parsed.path,
            parsed.query,
            candidate.content_type,
            candidate.resource_type,
        ]
    ).lower()
    score = 0
    for keyword in AD_KEYWORDS:
        if keyword in haystack:
            score += 3 if keyword not in {"ad", "ads"} else 1

    params = parse_qs(parsed.query.lower())
    for key in ("ad_type", "adunit", "iu", "cust_params", "correlator"):
        if key in params:
            score += 2

    if candidate.duration is not None:
        if candidate.duration <= 75:
            score += 4
        elif candidate.duration <= 120:
            score += 2

    return score


def has_ad_markers(candidate: StreamCandidate) -> bool:
    parsed = urlparse(candidate.url)
    haystack = " ".join(
        [
            parsed.hostname or "",
            parsed.path,
            parsed.query,
            candidate.content_type,
            candidate.resource_type,
        ]
    ).lower()
    if any(keyword in haystack for keyword in AD_KEYWORDS):
        return True

    params = parse_qs(parsed.query.lower())
    return any(
        key in params
        for key in ("ad_type", "adunit", "iu", "cust_params", "correlator")
    )


def content_score(candidate: StreamCandidate) -> int:
    parsed = urlparse(candidate.url)
    haystack = " ".join([parsed.path, parsed.query, candidate.content_type]).lower()
    score = 0

    if candidate.kind == "hls":
        score += 12
    elif candidate.kind == "dash":
        score += 11
    elif candidate.kind == "file":
        score += 8

    for hint in CONTENT_HINTS:
        if hint in haystack:
            score += 1

    if candidate.duration is not None:
        if candidate.duration >= 20 * 60:
            score += 8
        elif candidate.duration >= 5 * 60:
            score += 6
        elif candidate.duration >= 2 * 60:
            score += 3

    if candidate.byte_length is not None:
        if candidate.byte_length >= 100 * 1024 * 1024:
            score += 5
        elif candidate.byte_length >= 20 * 1024 * 1024:
            score += 3

    return score - ad_score(candidate)


def is_likely_ad(candidate: StreamCandidate, threshold: int = 4) -> bool:
    return ad_score(candidate) >= threshold and content_score(candidate) < 10


def is_short_duration_only_ad(candidate: StreamCandidate) -> bool:
    return (
        candidate.duration is not None
        and candidate.duration <= 120
        and is_likely_ad(candidate)
        and not has_ad_markers(candidate)
    )


def score_link(url: str, text: str, has_thumbnail: bool, *, is_ad_url: bool) -> int:
    if is_ad_url:
        return -100

    parsed = urlparse(url)
    haystack = " ".join([parsed.path, parsed.query, text]).lower()
    score = 0
    if has_thumbnail:
        score += 6
    for hint in VIDEO_URL_HINTS:
        if hint in haystack:
            score += 2
    for hint in VIDEO_TEXT_HINTS:
        if hint in text.lower():
            score += 1
    if re.search(r"/(?:watch|video|videos|episode|lecture|lesson)s?[/=?-]", haystack):
        score += 4
    if parsed.fragment:
        score -= 1
    if parsed.scheme not in {"http", "https"}:
        score -= 10
    return score


def dedupe_links(candidates: list[LinkCandidate]) -> list[LinkCandidate]:
    by_url: dict[str, LinkCandidate] = {}
    for candidate in candidates:
        existing = by_url.get(candidate.url)
        if existing is None or candidate.score > existing.score:
            by_url[candidate.url] = candidate
    return sorted(by_url.values(), key=lambda item: item.score, reverse=True)
