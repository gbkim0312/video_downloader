from __future__ import annotations

from concurrent.futures import Future, ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from pathlib import Path
import time

from video_downloader.adapters.downloader.ytdlp import (
    DOWNLOAD_DOWNLOADED,
    DOWNLOAD_SKIPPED,
    YtDlpDownloader,
    default_output_template,
)
from video_downloader.adapters.proxy.tor_control import (
    TorController,
    should_rotate_proxy,
)
from video_downloader.domain.models import (
    BatchJob,
    BatchSummary,
    ProxySettings,
    StreamCandidate,
)
from video_downloader.domain.scoring import is_likely_ad, is_short_duration_only_ad
from video_downloader.ports.browser import StreamSnifferPort
from video_downloader.ports.progress import ProgressReporterPort
from video_downloader.ports.storage import UrlListStorePort

RESULT_FAILED = "failed"
SUCCESS_RESULTS = {0, DOWNLOAD_DOWNLOADED, DOWNLOAD_SKIPPED}


class ProxyUnavailableError(RuntimeError):
    pass


class NoStreamCandidatesError(RuntimeError):
    def __init__(self, url: str) -> None:
        super().__init__(f"no stream candidates found for {url}")
        self.url = url


@dataclass(frozen=True, slots=True)
class DownloadOptions:
    mode: str = "auto"
    no_fallback: bool = False
    headless: bool = True
    user_agent: str | None = None
    play_seconds: float = 25
    allow_popups: bool = False
    include_ads: bool = False
    allow_short: bool = False
    candidate_index: int = 1
    list_only: bool = False
    print_url: bool = False
    quiet: bool = False
    output_dir: str = "downloads"
    output_template: str | None = None
    fragment_parallel: int = 4
    proxy_settings: ProxySettings | None = None


@dataclass(frozen=True, slots=True)
class BatchOptions:
    parallel: int = 1
    retries: int = 3


class DownloadService:
    def __init__(
        self,
        *,
        sniffer: StreamSnifferPort,
        downloader: YtDlpDownloader,
        tor_controller: TorController | None = None,
    ) -> None:
        self.sniffer = sniffer
        self.downloader = downloader
        self.tor_controller = tor_controller or TorController()

    def proxy_ip_message(self, settings: ProxySettings) -> str:
        return f"proxy IP: {self.require_proxy_connection(settings)}"

    def require_proxy_connection(self, settings: ProxySettings) -> str:
        attempts = max(1, settings.connect_retries)
        last_error: Exception | None = None
        for attempt in range(1, attempts + 1):
            try:
                return self.tor_controller.current_ip(settings)
            except Exception as exc:
                last_error = exc
                if attempt < attempts:
                    time.sleep(1)
        if settings.kill_switch:
            raise ProxyUnavailableError(
                "Proxy kill switch: cannot verify proxy connection "
                f"after {attempts} attempt(s) ({last_error})"
            ) from last_error
        return f"unavailable after {attempts} attempt(s) ({last_error})"

    def select_candidates(
        self,
        candidates: list[StreamCandidate],
        *,
        include_ads: bool,
        allow_short: bool,
        candidate_index: int,
    ) -> list[StreamCandidate]:
        if include_ads:
            selectable = candidates
        else:
            selectable = [
                candidate
                for candidate in candidates
                if not is_likely_ad(candidate)
                or (allow_short and is_short_duration_only_ad(candidate))
            ]
        if not selectable:
            return []
        if candidate_index < 1 or candidate_index > len(selectable):
            raise SystemExit(
                f"--candidate must be between 1 and {len(selectable)} after filtering"
            )
        return selectable[candidate_index - 1 :]

    def browser_download(
        self,
        url: str,
        output_template: str,
        *,
        options: DownloadOptions,
        progress_reporter: ProgressReporterPort | None,
        progress_label: str,
        show_candidates: bool,
    ) -> tuple[int | str, list[StreamCandidate]]:
        if not options.quiet:
            browser_mode = "headless Chromium" if options.headless else "headed Chromium"
            self._message(
                progress_reporter,
                progress_label,
                (
                    f"Opening page with {browser_mode}; sniffing streams for "
                    f"{options.play_seconds:g}s..."
                ),
            )

        candidates = self.sniffer.sniff_streams(
            url,
            headless=options.headless,
            user_agent=options.user_agent,
            play_seconds=options.play_seconds,
            allow_popups=options.allow_popups,
            proxy_settings=options.proxy_settings,
        )

        if not options.quiet:
            self._message(
                progress_reporter,
                progress_label,
                f"Found {len(candidates)} stream candidate(s).",
            )

        if options.list_only:
            return 0, candidates
        if not candidates:
            raise NoStreamCandidatesError(url)

        selected_candidates = self.select_candidates(
            candidates,
            include_ads=options.include_ads,
            allow_short=options.allow_short,
            candidate_index=options.candidate_index,
        )
        if not selected_candidates:
            self._message(
                progress_reporter,
                progress_label,
                (
                    "No non-ad stream candidate found. "
                    f"Try --include-ads or --headed. url={url}"
                ),
            )
            return 2, candidates

        last_error: Exception | None = None
        total_selected = len(selected_candidates)
        for attempt, selected in enumerate(selected_candidates, start=1):
            candidate_number = options.candidate_index + attempt - 1
            if options.print_url:
                self._message(progress_reporter, progress_label, selected.url)

            selected_output = output_template
            if options.output_template is None and selected.page_title:
                selected_output = default_output_template(
                    options.output_dir,
                    selected.page_title,
                )
                Path(selected_output).parent.mkdir(parents=True, exist_ok=True)
                if not options.quiet:
                    self._message(
                        progress_reporter,
                        progress_label,
                        f"Using page title for filename: {selected.page_title}",
                    )

            try:
                result = self._download_with_proxy_rotation(
                    lambda: self.downloader.download_candidate(
                        selected,
                        output_template=selected_output,
                        quiet=options.quiet or progress_reporter is not None,
                        progress_hook=(
                            progress_reporter.hook if progress_reporter else None
                        ),
                        progress_label=progress_label,
                        fragment_parallel=options.fragment_parallel,
                        proxy_settings=options.proxy_settings,
                    ),
                    options=options,
                    progress_reporter=progress_reporter,
                    progress_label=progress_label,
                )
                return result, candidates
            except Exception as exc:
                last_error = exc
                if attempt >= total_selected:
                    continue
                self._message(
                    progress_reporter,
                    progress_label,
                    (
                        f"candidate {candidate_number} failed; "
                        f"trying next candidate: {exc}"
                    ),
                )

        if last_error is not None:
            raise last_error
        return RESULT_FAILED, candidates

    def ytdlp_download(
        self,
        url: str,
        output_template: str,
        *,
        options: DownloadOptions,
        progress_reporter: ProgressReporterPort | None,
        progress_label: str,
    ) -> str:
        return self._download_with_proxy_rotation(
            lambda: self.downloader.download_url(
                url,
                output_template=output_template,
                quiet=options.quiet or progress_reporter is not None,
                progress_hook=progress_reporter.hook if progress_reporter else None,
                progress_label=progress_label,
                fragment_parallel=options.fragment_parallel,
                proxy_settings=options.proxy_settings,
            ),
            options=options,
            progress_reporter=progress_reporter,
            progress_label=progress_label,
        )

    def auto_download(
        self,
        url: str,
        output_template: str,
        *,
        options: DownloadOptions,
        progress_reporter: ProgressReporterPort | None,
        progress_label: str,
        show_candidates: bool,
    ) -> tuple[int | str, list[StreamCandidate]]:
        try:
            return self.browser_download(
                url,
                output_template,
                options=options,
                progress_reporter=progress_reporter,
                progress_label=progress_label,
                show_candidates=show_candidates,
            )
        except NoStreamCandidatesError:
            if options.no_fallback:
                raise
            self._message(
                progress_reporter,
                progress_label,
                f"no browser streams found; falling back to yt-dlp url={url}",
            )
            return (
                self.ytdlp_download(
                    url,
                    output_template,
                    options=options,
                    progress_reporter=progress_reporter,
                    progress_label=progress_label,
                ),
                [],
            )

    def download(
        self,
        url: str,
        output_template: str,
        *,
        options: DownloadOptions,
        progress_reporter: ProgressReporterPort | None,
        progress_label: str,
        show_candidates: bool,
    ) -> tuple[int | str, list[StreamCandidate]]:
        if options.mode == "browser":
            return self.browser_download(
                url,
                output_template,
                options=options,
                progress_reporter=progress_reporter,
                progress_label=progress_label,
                show_candidates=show_candidates,
            )
        if options.mode == "ytdlp":
            return (
                self.ytdlp_download(
                    url,
                    output_template,
                    options=options,
                    progress_reporter=progress_reporter,
                    progress_label=progress_label,
                ),
                [],
            )
        return self.auto_download(
            url,
            output_template,
            options=options,
            progress_reporter=progress_reporter,
            progress_label=progress_label,
            show_candidates=show_candidates,
        )

    def _message(
        self,
        progress_reporter: ProgressReporterPort | None,
        label: str,
        text: str,
    ) -> None:
        if progress_reporter:
            progress_reporter.message(label, text)
        else:
            print(text)

    def _download_with_proxy_rotation(
        self,
        download,
        *,
        options: DownloadOptions,
        progress_reporter: ProgressReporterPort | None,
        progress_label: str,
    ) -> str:
        settings = options.proxy_settings
        attempts = (settings.rotation_retries + 1) if settings else 1
        last_error: Exception | None = None
        for attempt in range(1, attempts + 1):
            try:
                return download()
            except Exception as exc:
                last_error = exc
                if not should_rotate_proxy(exc, settings) or attempt >= attempts:
                    raise
                if not options.quiet:
                    self._message(
                        progress_reporter,
                        progress_label,
                        (
                            "proxy blocked/rate-limited; requesting new Tor identity "
                            f"({attempt}/{settings.rotation_retries})"
                        ),
                    )
                self.tor_controller.rotate_identity(settings)
                if not options.quiet:
                    ip_address = self.require_proxy_connection(settings)
                    self._message(
                        progress_reporter,
                        progress_label,
                        f"proxy IP: {ip_address}",
                    )

        if last_error is not None:
            raise last_error
        return RESULT_FAILED


class BatchDownloadService:
    def __init__(
        self,
        *,
        download_service: DownloadService,
        url_store: UrlListStorePort,
        progress_factory,
    ) -> None:
        self.download_service = download_service
        self.url_store = url_store
        self.progress_factory = progress_factory

    def run(
        self,
        input_file: str,
        output_template: str,
        *,
        download_options: DownloadOptions,
        batch_options: BatchOptions,
    ) -> tuple[int, BatchSummary]:
        urls = self.url_store.read_urls(input_file)
        if not urls:
            raise ValueError(f"No URLs found in {input_file}.")
        if batch_options.parallel < 1:
            raise ValueError("--parallel must be at least 1.")
        if download_options.fragment_parallel < 1:
            raise ValueError("--fragment-parallel must be at least 1.")
        if batch_options.retries < 0:
            raise ValueError("--retries must be at least 0.")

        reporter = self.progress_factory(
            enabled=not download_options.quiet,
            total_jobs=len(urls),
            worker_slots=batch_options.parallel,
        )
        jobs = [BatchJob(index=index, url=url) for index, url in enumerate(urls, start=1)]
        failed_jobs = jobs
        completed: dict[int, str] = {}
        interrupted = False
        executor: ThreadPoolExecutor | None = None
        try:
            for attempt in range(1, batch_options.retries + 2):
                if not failed_jobs:
                    break

                current_jobs = failed_jobs
                failed_jobs = []
                if attempt > 1:
                    reporter.message(
                        "batch",
                        (
                            f"retry {attempt - 1}/{batch_options.retries}: "
                            f"{len(current_jobs)} failed URL(s)"
                        ),
                    )

                executor = ThreadPoolExecutor(max_workers=batch_options.parallel)
                futures: dict[Future[int | str], tuple[BatchJob, str]] = {}
                for job in current_jobs:
                    label = f"{job.index}/{len(urls)} try {attempt}"
                    futures[
                        executor.submit(
                            self._process_one,
                            job.url,
                            output_template,
                            download_options,
                            reporter,
                            label,
                        )
                    ] = (job, label)

                for future in as_completed(futures):
                    job, label = futures[future]
                    try:
                        result = future.result()
                    except Exception as exc:
                        reporter.message(label, f"failed: {exc} url={job.url}")
                        result = RESULT_FAILED

                    if result in SUCCESS_RESULTS:
                        completed[job.index] = str(result)
                        if result == DOWNLOAD_SKIPPED:
                            status = "skipped"
                        elif result == 0:
                            status = "done"
                        else:
                            status = "downloaded"
                        reporter.message(label, status)
                        reporter.complete_job(label)
                        continue

                    reporter.close_bar(label)
                    if attempt <= batch_options.retries:
                        failed_jobs.append(job)
                        reporter.message(label, f"will retry url={job.url}")
                    else:
                        reporter.message(label, f"failed after retries url={job.url}")
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
            return 130, BatchSummary(0, 0, len(urls), len(urls), tuple(jobs))

        failures = len(urls) - len(completed)
        downloaded_count = sum(
            1 for result in completed.values() if result == DOWNLOAD_DOWNLOADED
        )
        skipped_count = sum(
            1 for result in completed.values() if result == DOWNLOAD_SKIPPED
        )
        failed_final = tuple(job for job in jobs if job.index not in completed)
        return (
            1 if failures else 0,
            BatchSummary(
                downloaded=downloaded_count,
                skipped=skipped_count,
                failed=failures,
                total=len(urls),
                failed_jobs=failed_final,
            ),
        )

    def _process_one(
        self,
        url: str,
        output_template: str,
        options: DownloadOptions,
        reporter: ProgressReporterPort,
        label: str,
    ) -> int | str:
        try:
            result, _ = self.download_service.download(
                url,
                output_template,
                options=options,
                progress_reporter=reporter,
                progress_label=label,
                show_candidates=options.list_only,
            )
            return result
        except Exception as exc:
            reporter.message(label, f"failed: {exc} url={url}")
            return RESULT_FAILED
