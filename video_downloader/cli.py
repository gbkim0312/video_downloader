from __future__ import annotations

import argparse
import sys
import time
from dataclasses import replace
from pathlib import Path

from .adapters.browser.link_extractor import PlaywrightLinkExtractor
from .adapters.browser.stream_sniffer import PlaywrightStreamSniffer
from .adapters.downloader.ytdlp import (
    DOWNLOAD_DOWNLOADED,
    DOWNLOAD_SKIPPED,
    YtDlpDownloader,
    default_output_template,
)
from .adapters.progress.tqdm_progress import ProgressReporter
from .adapters.progress.rich_dashboard import DashboardProgressReporter
from .adapters.proxy.tor_control import TorController
from .adapters.storage.proxy_info import DEFAULT_PROXY_INFO_PATH, read_proxy_info
from .adapters.storage.text_url_store import TextUrlListStore
from .application.downloads import (
    BatchDownloadService,
    BatchOptions,
    DownloadOptions,
    DownloadService,
)
from .application.links import LinkExtractionOptions, LinkExtractionService
from .domain.models import BatchSummary, StreamCandidate
from .domain.scoring import ad_score, content_score, is_likely_ad


SUCCESS_RESULTS = {0, DOWNLOAD_DOWNLOADED, DOWNLOAD_SKIPPED}


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="video-dl",
        description=(
            "Download a video from a page URL. Browser mode plays the page briefly "
            "and sniffs network video streams such as HLS, DASH, and MP4."
        ),
    )
    parser.add_argument("url", nargs="?", help="Page URL or direct media URL.")
    parser.add_argument(
        "-i",
        "--input-file",
        default=None,
        help="Text file containing one URL per line. Blank lines and # comments are ignored.",
    )
    parser.add_argument(
        "-j",
        "--parallel",
        type=int,
        default=1,
        help="Maximum number of URLs to process at the same time when using --input-file.",
    )
    parser.add_argument(
        "-F",
        "--fragment-parallel",
        type=int,
        default=4,
        help="Concurrent media fragments per download for HLS/DASH. Default: 4.",
    )
    parser.add_argument(
        "-r",
        "--retries",
        type=int,
        default=3,
        help="Retry failed URLs this many times after the first attempt. Default: 3.",
    )
    parser.add_argument(
        "-o",
        "--output-dir",
        default="downloads",
        help="Directory to save downloads. Default: downloads.",
    )
    parser.add_argument(
        "--output-template",
        default=None,
        help="yt-dlp output template. Defaults to downloads/%%(title).200B.%%(ext)s",
    )
    parser.add_argument(
        "-m",
        "--mode",
        choices=("auto", "browser", "ytdlp"),
        default="auto",
        help="auto/browser/ytdlp. Default: auto.",
    )
    parser.add_argument(
        "--no-fallback",
        action="store_true",
        help="In auto mode, do not fall back to yt-dlp when browser sniff finds no streams.",
    )
    parser.add_argument(
        "-p",
        "--use-proxy",
        action="store_true",
        help="Use proxy settings from .proxyinfo for browser sniffing and downloads.",
    )
    parser.add_argument(
        "--proxy-info",
        default=DEFAULT_PROXY_INFO_PATH,
        help="Proxy config file used with --use-proxy. Default: .proxyinfo.",
    )
    parser.add_argument(
        "--proxy-connect-retries",
        type=int,
        default=None,
        help="Proxy verification attempts before failing. Default: .proxyinfo or 3.",
    )
    parser.add_argument(
        "-s",
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
        "--no-auto-click",
        action="store_true",
        help="Do not automatically click play/video elements during browser sniffing.",
    )
    parser.add_argument(
        "--user-data-dir",
        default=None,
        help="Use a persistent Chromium profile directory.",
    )
    parser.add_argument(
        "--browser-channel",
        default=None,
        help="Use an installed browser channel such as chrome or msedge.",
    )
    parser.add_argument(
        "--allow-popups",
        action="store_true",
        help="Allow new tabs/popups. By default they are closed and known ad requests are blocked.",
    )
    parser.add_argument("--user-agent", default=None, help="Override browser user agent.")
    parser.add_argument(
        "--include-ads",
        action="store_true",
        help="Allow ad-looking streams to be selected.",
    )
    parser.add_argument(
        "--allow-short",
        action="store_true",
        help="Do not exclude streams only because they are very short.",
    )
    parser.add_argument(
        "-c",
        "--candidate",
        type=int,
        default=1,
        help="1-based stream candidate index to download after scoring.",
    )
    parser.add_argument(
        "-l",
        "--list-only",
        action="store_true",
        help="Print stream candidates without downloading.",
    )
    parser.add_argument(
        "--print-url",
        action="store_true",
        help="Print the selected stream URL before downloading.",
    )
    parser.add_argument("-q", "--quiet", action="store_true", help="Reduce output.")
    parser.add_argument(
        "--dashboard",
        dest="dashboard",
        action="store_true",
        default=True,
        help="Use a full-screen terminal dashboard for batch progress. Enabled by default.",
    )
    parser.add_argument(
        "--no-dashboard",
        dest="dashboard",
        action="store_false",
        help="Use the compact tqdm progress bars instead of the full-screen dashboard.",
    )
    parser.add_argument(
        "-x",
        "--extract-links",
        action="store_true",
        help="Extract likely video page links from a playlist/index page instead of downloading.",
    )
    parser.add_argument(
        "--links-output",
        default=None,
        help="Write extracted video links to this text file.",
    )
    parser.add_argument(
        "--link-min-score",
        type=int,
        default=6,
        help="Minimum score for extracted links. Lower values include more links.",
    )
    parser.add_argument(
        "--link-wait-seconds",
        type=float,
        default=3,
        help="Seconds to wait after opening a link extraction page.",
    )
    return parser


def make_download_options(args: argparse.Namespace) -> DownloadOptions:
    proxy_settings = getattr(args, "proxy_settings", None)
    return DownloadOptions(
        mode=args.mode,
        no_fallback=args.no_fallback,
        headless=not args.headed,
        user_agent=args.user_agent,
        play_seconds=args.play_seconds,
        allow_popups=args.allow_popups,
        include_ads=args.include_ads,
        allow_short=args.allow_short,
        candidate_index=args.candidate,
        list_only=args.list_only,
        print_url=args.print_url,
        quiet=args.quiet,
        output_dir=args.output_dir,
        output_template=args.output_template,
        fragment_parallel=args.fragment_parallel,
        proxy_settings=proxy_settings,
        auto_click=not args.no_auto_click,
        user_data_dir=args.user_data_dir,
        browser_channel=args.browser_channel,
    )


def make_download_service() -> DownloadService:
    return DownloadService(
        sniffer=PlaywrightStreamSniffer(),
        downloader=YtDlpDownloader(),
    )


def make_link_service() -> LinkExtractionService:
    return LinkExtractionService(
        extractor=PlaywrightLinkExtractor(),
        url_store=TextUrlListStore(),
    )


def configure_proxy(args: argparse.Namespace) -> None:
    args.proxy_settings = None
    if not args.use_proxy:
        return
    settings = read_proxy_info(args.proxy_info)
    if args.proxy_connect_retries is not None:
        if args.proxy_connect_retries < 1:
            raise ValueError("--proxy-connect-retries must be at least 1")
        settings = replace(settings, connect_retries=args.proxy_connect_retries)

    controller = TorController()
    attempts = max(1, settings.connect_retries)
    last_error: Exception | None = None
    for attempt in range(1, attempts + 1):
        try:
            ip_address = controller.current_ip(settings)
            break
        except Exception as exc:
            last_error = exc
            if not args.quiet:
                print(
                    f"proxy check failed ({attempt}/{attempts}): {exc}",
                    file=sys.stderr,
                )
            if attempt < attempts:
                time.sleep(1)
    else:
        if settings.kill_switch:
            raise ValueError(
                "Proxy kill switch: cannot verify proxy connection "
                f"after {attempts} attempt(s) ({last_error})"
            ) from last_error
        ip_address = f"unavailable after {attempts} attempt(s) ({last_error})"

    args.proxy_settings = settings
    if not args.quiet:
        print(f"proxy IP: {ip_address}")


def result_to_exit_code(result: int | str) -> int:
    if isinstance(result, int):
        return result
    return 0 if result in SUCCESS_RESULTS else 1


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


def print_batch_summary(summary: BatchSummary, retries: int) -> None:
    print(
        (
            "Summary: "
            f"downloaded={summary.downloaded}, "
            f"skipped={summary.skipped}, "
            f"failed={summary.failed}, "
            f"total={summary.total}"
        ),
        file=sys.stderr if summary.failed else sys.stdout,
    )
    if summary.failed:
        print(
            f"Completed with {summary.failed} failure(s) after {retries} retry attempt(s).",
            file=sys.stderr,
        )
        print("Failed URLs:", file=sys.stderr)
        for job in summary.failed_jobs:
            print(f"{job.index}: {job.url}", file=sys.stderr)


def run_link_extraction(args: argparse.Namespace) -> int:
    service = make_link_service()
    if not args.quiet:
        browser_mode = "headed Chromium" if args.headed else "headless Chromium"
        print(f"Opening page with {browser_mode}; extracting video links...")

    links = service.extract(
        args.url,
        options=LinkExtractionOptions(
            headless=not args.headed,
            user_agent=args.user_agent,
            min_score=args.link_min_score,
            wait_seconds=args.link_wait_seconds,
            allow_popups=args.allow_popups,
            quiet=args.quiet,
            proxy_settings=getattr(args, "proxy_settings", None),
            user_data_dir=args.user_data_dir,
            browser_channel=args.browser_channel,
        ),
    )
    if args.links_output:
        service.append_links(args.links_output, links)
        if not args.quiet:
            print(f"Appended {len(links)} link(s) to {args.links_output}")
    else:
        for candidate in links:
            print(candidate.url)

    if not args.quiet and not links:
        print("No likely video links found.")
    return 0


def run_batch(args: argparse.Namespace, output_template: str) -> int:
    progress_factory = DashboardProgressReporter if args.dashboard else ProgressReporter
    service = BatchDownloadService(
        download_service=make_download_service(),
        url_store=TextUrlListStore(),
        progress_factory=progress_factory,
    )
    try:
        exit_code, summary = service.run(
            args.input_file,
            output_template,
            download_options=make_download_options(args),
            batch_options=BatchOptions(parallel=args.parallel, retries=args.retries),
        )
    except ValueError as exc:
        print(str(exc), file=sys.stderr)
        return 2

    print_batch_summary(summary, args.retries)
    if exit_code == 130:
        print("Interrupted by user.", file=sys.stderr)
    elif not summary.failed and not args.quiet:
        print("All downloads completed.")
    return exit_code


def run_single(args: argparse.Namespace, output_template: str) -> int:
    service = make_download_service()
    options = make_download_options(args)
    result, candidates = service.download(
        args.url,
        output_template,
        options=options,
        progress_reporter=None,
        progress_label="single",
        show_candidates=True,
    )
    if candidates:
        print_candidates(candidates)
    return result_to_exit_code(result)


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.input_file is None and args.url is None:
        parser.error("provide a URL or --input-file")
    if args.input_file is not None and args.url is not None:
        parser.error("provide either a URL or --input-file, not both")
    if args.extract_links and args.input_file is not None:
        parser.error("--extract-links works with a page URL, not --input-file")

    try:
        configure_proxy(args)
        output_template = args.output_template or default_output_template(args.output_dir)
        Path(output_template).parent.mkdir(parents=True, exist_ok=True)
        if args.extract_links:
            return run_link_extraction(args)
        if args.input_file:
            return run_batch(args, output_template)
        return run_single(args, output_template)
    except ValueError as exc:
        print(str(exc), file=sys.stderr)
        return 2
    except KeyboardInterrupt:
        print("Interrupted by user.", file=sys.stderr)
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
