from __future__ import annotations

import sys
import threading
import time
from dataclasses import dataclass
from typing import Any

from .tqdm_progress import BYTES_PER_MB, format_mb, format_mb_value


BAR_WIDTH = 28


@dataclass(slots=True)
class DashboardJob:
    label: str
    state: str = "starting"
    downloaded_mb: float | None = None
    total_mb: float | None = None
    speed: str = "-"
    percent: float | None = None
    completed: bool = False


class DashboardProgressReporter:
    def __init__(
        self,
        *,
        enabled: bool = True,
        min_interval: float = 0.2,
        total_jobs: int = 0,
        worker_slots: int = 0,
    ) -> None:
        self.enabled = enabled
        self.min_interval = min_interval
        self.total_jobs = total_jobs
        self.worker_slots = max(1, worker_slots)
        self._lock = threading.RLock()
        self._last_updated: dict[str, float] = {}
        self._jobs: dict[str, DashboardJob] = {}
        self._positions: dict[str, int] = {}
        self._free_positions = list(range(1, self.worker_slots + 1))
        self._completed = 0
        self._deferred_messages: list[str] = []
        self._console = None
        self._live = None

        if enabled:
            self._start_live()

    def _start_live(self) -> None:
        try:
            from rich.console import Console
            from rich.live import Live
        except ModuleNotFoundError as exc:
            raise RuntimeError(
                "Missing dependency: rich. Install with `pip install -e .`."
            ) from exc

        self._console = Console(file=sys.stdout)
        self._live = Live(
            self._render(),
            console=self._console,
            refresh_per_second=4,
            screen=True,
            transient=True,
        )
        self._live.start()

    def close(self) -> None:
        if not self.enabled:
            return
        with self._lock:
            if self._live is not None:
                self._live.update(self._render(), refresh=True)
                self._live.stop()
                self._live = None
            self._flush_messages()

    def message(self, label: str, text: str) -> None:
        if not self.enabled:
            return
        with self._lock:
            state = self._state_from_message(text)
            if label != "batch" and state:
                self._job_for(label).state = state
                self._refresh()
            message = self._deferred_message(label, text)
            if message:
                self._deferred_messages.append(message)

    def hook(self, label: str, status: dict[str, Any]) -> None:
        if not self.enabled:
            return

        state = status.get("status", "")
        if state == "downloading":
            self._update_download(label, status)
            return

        if state == "finished":
            self._finish_download(label, status)

    def complete_job(self, label: str) -> None:
        if not self.enabled:
            return
        with self._lock:
            job = self._job_for(label)
            if job.completed:
                return
            job.completed = True
            if job.percent is not None:
                job.percent = max(job.percent, 100.0)
            self._completed += 1
            self._refresh()

    def close_bar(self, label: str) -> None:
        if not self.enabled:
            return
        with self._lock:
            self._jobs.pop(label, None)
            position = self._positions.pop(label, None)
            if position is not None:
                self._release_position(position)
            self._last_updated.pop(label, None)
            self._refresh()

    def _update_download(self, label: str, status: dict[str, Any]) -> None:
        now = time.monotonic()
        with self._lock:
            last = self._last_updated.get(label, 0)
            if now - last < self.min_interval:
                return
            self._last_updated[label] = now

            downloaded = int(status.get("downloaded_bytes") or 0)
            total = status.get("total_bytes") or status.get("total_bytes_estimate")
            job = self._job_for(label)
            downloaded_mb = downloaded / BYTES_PER_MB
            total_mb = float(total) / BYTES_PER_MB if total else job.total_mb
            percent = (
                min(100.0, downloaded_mb / total_mb * 100)
                if total_mb
                else job.percent
            )

            job.state = "downloading"
            job.completed = False
            job.downloaded_mb = downloaded_mb
            job.total_mb = total_mb
            job.percent = percent
            job.speed = f"{format_mb(status.get('speed'))} MB/s"
            self._refresh()

    def _finish_download(self, label: str, status: dict[str, Any]) -> None:
        with self._lock:
            job = self._job_for(label)
            total = status.get("total_bytes") or status.get("downloaded_bytes")
            if total:
                total_mb = float(total) / BYTES_PER_MB
                job.downloaded_mb = total_mb
                job.total_mb = total_mb
                job.percent = 100.0
            job.state = "processing"
            self._refresh()

    def _job_for(self, label: str) -> DashboardJob:
        job = self._jobs.get(label)
        if job is not None:
            return job
        self._reclaim_completed_position()
        self._positions[label] = self._next_position()
        job = DashboardJob(label=label)
        self._jobs[label] = job
        return job

    def _reclaim_completed_position(self) -> None:
        if self._free_positions:
            return
        completed = sorted(
            (
                (position, label)
                for label, position in self._positions.items()
                if self._jobs.get(label) and self._jobs[label].completed
            ),
            key=lambda item: item[0],
        )
        if not completed:
            return
        position, label = completed[0]
        self._jobs.pop(label, None)
        self._positions.pop(label, None)
        self._last_updated.pop(label, None)
        if position <= self.worker_slots:
            self._free_positions.append(position)
            self._free_positions.sort()

    def _next_position(self) -> int:
        if self._free_positions:
            return self._free_positions.pop(0)
        return max(self._positions.values(), default=0) + 1

    def _release_position(self, position: int) -> None:
        waiting = sorted(
            (
                (waiting_position, label)
                for label, waiting_position in self._positions.items()
                if waiting_position > self.worker_slots
            ),
            key=lambda item: item[0],
        )
        if position <= self.worker_slots and waiting:
            _, label = waiting[0]
            self._positions[label] = position
            return
        if position <= self.worker_slots:
            self._free_positions.append(position)
            self._free_positions.sort()

    def _state_from_message(self, text: str) -> str | None:
        compact = " ".join(text.split())
        if compact.startswith("Opening page with "):
            return "sniffing"
        if compact.startswith("Found "):
            return "selecting"
        if compact == "queued for download":
            return "queued"
        if compact.startswith("no browser streams found"):
            return "fallback"
        if compact.startswith("candidate "):
            return "next candidate"
        if compact == "skipped":
            return "skipped"
        if compact in {"done", "downloaded"}:
            return "done"
        if compact.startswith("failed"):
            return "failed"
        return None

    def _deferred_message(self, label: str, text: str) -> str | None:
        compact = " ".join(text.split())
        if not compact or compact in {"done", "downloaded", "skipped"}:
            return None
        quiet_prefixes = (
            "Opening page with ",
            "Found ",
            "Using page title for filename:",
            "queued for download",
        )
        if compact.startswith(quiet_prefixes):
            return None
        if len(compact) > 180:
            compact = compact[:177] + "..."
        return f"{label}: {compact}"

    def _refresh(self) -> None:
        if self._live is not None:
            self._live.update(self._render(), refresh=True)

    def _render(self):
        from rich import box
        from rich.panel import Panel
        from rich.table import Table

        table = Table(
            title=self._summary_text(),
            box=box.SIMPLE_HEAVY,
            expand=True,
            show_lines=False,
        )
        table.add_column("slot", width=4, justify="right")
        table.add_column("job", width=18, no_wrap=True)
        table.add_column("state", width=14, no_wrap=True)
        table.add_column("progress", width=BAR_WIDTH + 8, no_wrap=True)
        table.add_column("size", width=18, no_wrap=True)
        table.add_column("speed", width=10, no_wrap=True)

        rows = self._rows_by_position()
        for slot in range(1, self.worker_slots + 1):
            job = rows.get(slot)
            if job is None:
                table.add_row(str(slot), "-", "idle", self._bar(None), "-", "-")
                continue
            table.add_row(
                str(slot),
                job.label,
                job.state,
                self._bar(job.percent),
                self._size_text(job),
                job.speed,
            )
        return Panel(table, title="video-dl dashboard", border_style="cyan")

    def _summary_text(self) -> str:
        if self.total_jobs:
            percent = self._completed / self.total_jobs * 100
            return f"batch {self._completed}/{self.total_jobs} ({percent:.0f}%)"
        return "batch"

    def _rows_by_position(self) -> dict[int, DashboardJob]:
        rows: dict[int, DashboardJob] = {}
        for label, position in self._positions.items():
            job = self._jobs.get(label)
            if job is not None:
                rows[position] = job
        return rows

    def _bar(self, percent: float | None) -> str:
        if percent is None:
            return "-" * BAR_WIDTH + "   ?%"
        filled = int(BAR_WIDTH * percent / 100)
        empty = BAR_WIDTH - filled
        return f"{'█' * filled}{'░' * empty} {percent:3.0f}%"

    def _size_text(self, job: DashboardJob) -> str:
        downloaded = format_mb_value(job.downloaded_mb)
        total = format_mb_value(job.total_mb)
        return f"{downloaded}/{total} MB"

    def _flush_messages(self) -> None:
        if not self._deferred_messages:
            return
        print("Logs:", file=sys.stderr)
        for message in self._deferred_messages:
            print(message, file=sys.stderr)
        self._deferred_messages.clear()
