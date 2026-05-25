from __future__ import annotations

import argparse
import sys
from concurrent.futures import Future, ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from pathlib import Path

from .downloader import default_output_template, download_candidate, download_url
from .models import StreamCandidate
from .progress import ProgressReporter
from .scoring import ad_score, content_score, is_likely_ad


@dataclass(frozen=True, slots=True)
class BatchJob:
    index: int
    url: str


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
        "--retries",
        type=int,
        default=3,
        help="Retry failed URLs this many times after the first attempt. Default: 3.",
    )
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
    return run_browser_download(
        args,
        args.url,
        output_template,
        progress_reporter=None,
        progress_label="single",
        show_candidates=True,
    )


def run_browser_download(
    args: argparse.Namespace,
    url: str,
    output_template: str,
    *,
    progress_reporter: ProgressReporter | None,
    progress_label: str,
    show_candidates: bool,
) -> int:
    try:
        from .sniffer import sniff_streams
    except ModuleNotFoundError as exc:
        missing = exc.name or "playwright"
        raise RuntimeError(
            f"Missing dependency: {missing}. Install with `pip install -e .` "
            "and run `python -m playwright install chromium`."
        ) from exc

    if not args.quiet:
        browser_mode = "headed Chromium" if args.headed else "headless Chromium"
        message = (
            f"Opening page with {browser_mode}; sniffing streams for "
            f"{args.play_seconds:g}s..."
        )
        if progress_reporter:
            progress_reporter.message(progress_label, message)
        else:
            print(message)

    candidates = sniff_streams(
        url,
        headless=not args.headed,
        user_agent=args.user_agent,
        play_seconds=args.play_seconds,
    )

    if not args.quiet:
        message = f"Found {len(candidates)} stream candidate(s)."
        if progress_reporter:
            progress_reporter.message(progress_label, message)
        else:
            print(message)

    if show_candidates:
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
        if progress_reporter:
            progress_reporter.message(progress_label, selected.url)
        else:
            print(selected.url)

    selected_output = output_template
    if args.output is None and selected.page_title:
        selected_output = default_output_template(args.output_dir, selected.page_title)
        Path(selected_output).parent.mkdir(parents=True, exist_ok=True)
        if progress_reporter:
            progress_reporter.message(
                progress_label,
                f"Using page title for filename: {selected.page_title}",
            )
        elif not args.quiet:
            print(f"Using page title for filename: {selected.page_title}")

    download_candidate(
        selected,
        output_template=selected_output,
        quiet=args.quiet or progress_reporter is not None,
        progress_hook=progress_reporter.hook if progress_reporter else None,
        progress_label=progress_label,
    )
    return 0


def run_ytdlp_mode(args: argparse.Namespace, output_template: str) -> int:
    return run_ytdlp_download(
        args,
        args.url,
        output_template,
        progress_reporter=None,
        progress_label="single",
    )


def run_ytdlp_download(
    args: argparse.Namespace,
    url: str,
    output_template: str,
    *,
    progress_reporter: ProgressReporter | None,
    progress_label: str,
) -> int:
    download_url(
        url,
        output_template=output_template,
        quiet=args.quiet or progress_reporter is not None,
        progress_hook=progress_reporter.hook if progress_reporter else None,
        progress_label=progress_label,
    )
    return 0


def read_urls(path: str | Path) -> list[str]:
    urls: list[str] = []
    with Path(path).open("r", encoding="utf-8") as file:
        for line in file:
            stripped = line.strip()
            if not stripped or stripped.startswith("#"):
                continue
            urls.append(stripped)
    return urls


def process_one_url(
    args: argparse.Namespace,
    url: str,
    output_template: str,
    *,
    progress_reporter: ProgressReporter,
    progress_label: str,
) -> int:
    try:
        if args.mode == "browser":
            return run_browser_download(
                args,
                url,
                output_template,
                progress_reporter=progress_reporter,
                progress_label=progress_label,
                show_candidates=args.list_only,
            )

        if args.mode == "ytdlp":
            return run_ytdlp_download(
                args,
                url,
                output_template,
                progress_reporter=progress_reporter,
                progress_label=progress_label,
            )

        try:
            return run_browser_download(
                args,
                url,
                output_template,
                progress_reporter=progress_reporter,
                progress_label=progress_label,
                show_candidates=args.list_only,
            )
        except Exception as exc:
            progress_reporter.message(
                progress_label,
                f"browser sniff failed: {exc}; falling back to yt-dlp",
            )
            return run_ytdlp_download(
                args,
                url,
                output_template,
                progress_reporter=progress_reporter,
                progress_label=progress_label,
            )
    except Exception as exc:
        progress_reporter.message(progress_label, f"failed: {exc}")
        return 1


def run_batch_mode(args: argparse.Namespace, output_template: str) -> int:
    try:
        urls = read_urls(args.input_file)
    except OSError as exc:
        print(f"Could not read {args.input_file}: {exc}", file=sys.stderr)
        return 2
    if not urls:
        print(f"No URLs found in {args.input_file}.", file=sys.stderr)
        return 2
    if args.parallel < 1:
        print("--parallel must be at least 1.", file=sys.stderr)
        return 2
    if args.retries < 0:
        print("--retries must be at least 0.", file=sys.stderr)
        return 2
    if args.headed and args.parallel > 1:
        print("--headed with --parallel > 1 opens multiple visible browsers.", file=sys.stderr)

    reporter = ProgressReporter(enabled=not args.quiet, total_jobs=len(urls))
    reporter.message(
        "batch",
        (
            f"starting {len(urls)} URL(s) with parallel={args.parallel}, "
            f"retries={args.retries}"
        ),
    )

    jobs = [BatchJob(index=index, url=url) for index, url in enumerate(urls, start=1)]
    failed_jobs = jobs
    completed: set[int] = set()
    interrupted = False
    executor: ThreadPoolExecutor | None = None
    try:
        for attempt in range(1, args.retries + 2):
            if not failed_jobs:
                break

            current_jobs = failed_jobs
            failed_jobs = []
            if attempt > 1:
                reporter.message(
                    "batch",
                    f"retry {attempt - 1}/{args.retries}: {len(current_jobs)} failed URL(s)",
                )

            executor = ThreadPoolExecutor(max_workers=args.parallel)
            futures: dict[Future[int], tuple[BatchJob, str]] = {}
            for job in current_jobs:
                label = f"{job.index}/{len(urls)} try {attempt}"
                futures[
                    executor.submit(
                        process_one_url,
                        args,
                        job.url,
                        output_template,
                        progress_reporter=reporter,
                        progress_label=label,
                    )
                ] = (job, label)

            for future in as_completed(futures):
                job, label = futures[future]
                try:
                    result = future.result()
                except Exception as exc:
                    reporter.message(label, f"failed: {exc}")
                    result = 1

                if result == 0:
                    completed.add(job.index)
                    reporter.message(label, "done")
                    reporter.complete_job(label)
                    continue

                reporter.close_bar(label)
                if attempt <= args.retries:
                    failed_jobs.append(job)
                    reporter.message(label, "will retry")
                else:
                    reporter.message(label, "failed after retries")
                    reporter.complete_job(label)

            executor.shutdown(wait=True)
            executor = None
    except KeyboardInterrupt:
        interrupted = True
        reporter.message("batch", "interrupted; stopping after current cleanup")
        if executor:
            executor.shutdown(wait=False, cancel_futures=True)
    finally:
        reporter.close()

    if interrupted:
        print("Interrupted by user.", file=sys.stderr)
        return 130

    failures = len(urls) - len(completed)
    if failures:
        print(
            f"Completed with {failures} failure(s) after {args.retries} retry attempt(s).",
            file=sys.stderr,
        )
        return 1
    if not args.quiet:
        print("All downloads completed.")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.input_file is None and args.url is None:
        parser.error("provide a URL or --input-file")
    if args.input_file is not None and args.url is not None:
        parser.error("provide either a URL or --input-file, not both")

    output_template = args.output or default_output_template(args.output_dir)
    Path(output_template).parent.mkdir(parents=True, exist_ok=True)

    try:
        if args.input_file:
            return run_batch_mode(args, output_template)

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
    except KeyboardInterrupt:
        print("Interrupted by user.", file=sys.stderr)
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
