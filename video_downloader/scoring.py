from __future__ import annotations

from urllib.parse import parse_qs, urlparse

from .models import StreamCandidate


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
