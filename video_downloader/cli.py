from __future__ import annotations

import argparse
import sys
from pathlib import Path

from .downloader import default_output_template, download_candidate, download_url
from .models import StreamCandidate
from .scoring import ad_score, content_score, is_likely_ad


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="video-dl",
        description=(
            "Download a video from a page URL. Browser mode plays the page briefly "
            "and sniffs network video streams such as HLS, DASH, and MP4."
        ),
    )
    parser.add_argument("url", help="Page URL or direct media URL.")
    parser.add_argument(
        "-o",
        "--output",
        default=None,
        help="yt-dlp output template. Defaults to downloads/%%(title).200B.%%(ext)s",
    )
    parser.add_argument(
        "--output-dir",
        default="downloads",
        help="Directory used when --output is not supplied.",
    )
    parser.add_argument(
        "--mode",
        choices=("auto", "browser", "ytdlp"),
        default="auto",
        help="auto/browser/ytdlp. Default: auto.",
    )
    parser.add_argument(
        "--play-seconds",
        type=float,
        default=25,
        help="Seconds to keep the page open after trying to start playback.",
    )
    parser.add_argument(
        "--headed",
        action="store_true",
        help="Show Chromium. Useful for pages that need login, consent, or manual play.",
    )
    parser.add_argument(
        "--user-agent",
        default=None,
        help="Override browser user agent.",
    )
    parser.add_argument(
        "--include-ads",
        action="store_true",
        help="Allow ad-looking streams to be selected.",
    )
    parser.add_argument(
        "--candidate",
        type=int,
        default=1,
        help="1-based stream candidate index to download after scoring.",
    )
    parser.add_argument(
        "--list-only",
        action="store_true",
        help="Print stream candidates without downloading.",
    )
    parser.add_argument(
        "--print-url",
        action="store_true",
        help="Print the selected stream URL before downloading.",
    )
    parser.add_argument(
        "--quiet",
        action="store_true",
        help="Reduce yt-dlp output.",
    )
    return parser


def format_duration(seconds: float | None) -> str:
    if seconds is None:
        return "unknown"
    seconds = int(seconds)
    hours, remainder = divmod(seconds, 3600)
    minutes, secs = divmod(remainder, 60)
    if hours:
        return f"{hours}:{minutes:02d}:{secs:02d}"
    return f"{minutes}:{secs:02d}"


def print_candidates(candidates: list[StreamCandidate]) -> None:
    if not candidates:
        print("No stream candidates found.")
        return

    print("Stream candidates:")
    for index, candidate in enumerate(candidates, start=1):
        ad = "ad?" if is_likely_ad(candidate) else "main?"
        print(
            f"{index:>2}. {candidate.kind:<4} {ad:<5} "
            f"score={content_score(candidate):>3} ad={ad_score(candidate):>2} "
            f"duration={format_duration(candidate.duration):>8} "
            f"host={candidate.host}"
        )
        print(f"    {candidate.url}")


def select_candidate(
    candidates: list[StreamCandidate],
    *,
    include_ads: bool,
    candidate_index: int,
) -> StreamCandidate | None:
    selectable = candidates if include_ads else [
        candidate for candidate in candidates if not is_likely_ad(candidate)
    ]
    if not selectable:
        return None
    if candidate_index < 1 or candidate_index > len(selectable):
        raise SystemExit(
            f"--candidate must be between 1 and {len(selectable)} after filtering"
        )
    return selectable[candidate_index - 1]


def run_browser_mode(args: argparse.Namespace, output_template: str) -> int:
    try:
        from .sniffer import sniff_streams
    except ModuleNotFoundError as exc:
        missing = exc.name or "playwright"
        raise RuntimeError(
            f"Missing dependency: {missing}. Install with `pip install -e .` "
            "and run `python -m playwright install chromium`."
        ) from exc

    candidates = sniff_streams(
        args.url,
        headless=not args.headed,
        user_agent=args.user_agent,
        play_seconds=args.play_seconds,
    )
    print_candidates(candidates)
    if args.list_only:
        return 0

    selected = select_candidate(
        candidates,
        include_ads=args.include_ads,
        candidate_index=args.candidate,
    )
    if selected is None:
        print("No non-ad stream candidate found. Try --include-ads or --headed.", file=sys.stderr)
        return 2

    if args.print_url:
        print(selected.url)

    download_candidate(selected, output_template=output_template, quiet=args.quiet)
    return 0


def run_ytdlp_mode(args: argparse.Namespace, output_template: str) -> int:
    download_url(args.url, output_template=output_template, quiet=args.quiet)
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    output_template = args.output or default_output_template(args.output_dir)
    Path(output_template).parent.mkdir(parents=True, exist_ok=True)

    if args.mode == "browser":
        return run_browser_mode(args, output_template)

    if args.mode == "ytdlp":
        return run_ytdlp_mode(args, output_template)

    try:
        return run_browser_mode(args, output_template)
    except Exception as exc:
        print(f"Browser sniff failed: {exc}", file=sys.stderr)
        print("Falling back to yt-dlp page extraction.", file=sys.stderr)
        return run_ytdlp_mode(args, output_template)


if __name__ == "__main__":
    raise SystemExit(main())
