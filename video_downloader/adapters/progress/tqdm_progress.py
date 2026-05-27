from __future__ import annotations

import sys
import threading
import time
from typing import Any


BYTES_PER_MB = 1024 * 1024


def bytes_to_mb(value: int | float | None) -> float | None:
    if value is None:
        return None
    return float(value) / BYTES_PER_MB


def format_mb(value: int | float | None) -> str:
    mb = bytes_to_mb(value)
    if mb is None:
        return "?"
    return f"{mb:.1f}"


def format_mb_value(value: int | float | None) -> str:
    if value is None:
        return "?"
    return f"{float(value):.1f}"


class ProgressReporter:
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
        self._lock = threading.RLock()
        self._last_updated: dict[str, float] = {}
        self._bars: dict[str, Any] = {}
        self._last_downloaded: dict[str, int] = {}
        self._positions: dict[str, int] = {}
        self._free_positions = list(range(1, worker_slots + 1))
        self._next_position = worker_slots + 1
        self._overall = None
        self._tqdm = None
        if enabled and total_jobs:
            self._tqdm = self._load_tqdm()
            self._overall = self._tqdm(
                total=total_jobs,
                desc="batch",
                unit="file",
                position=0,
                leave=True,
                dynamic_ncols=True,
                file=sys.stdout,
            )

    def _load_tqdm(self) -> Any:
        if self._tqdm is not None:
            return self._tqdm
        try:
            from tqdm import tqdm
        except ModuleNotFoundError as exc:
            raise RuntimeError(
                "Missing dependency: tqdm. Install with `pip install -e .`."
            ) from exc
        self._tqdm = tqdm
        return tqdm

    def close(self) -> None:
        if not self.enabled:
            return
        with self._lock:
            for bar in self._bars.values():
                bar.close()
            self._bars.clear()
            if self._overall is not None:
                self._overall.close()
                self._overall = None

    def message(self, label: str, text: str) -> None:
        if not self.enabled:
            return
        with self._lock:
            if label == "batch":
                if self._overall is not None:
                    self._overall.set_postfix_str(text, refresh=True)
                return
            bar = self._status_bar_for(label)
            bar.set_description_str(f"{label}: {self._compact(text)}", refresh=True)

    def _set_postfix(self, bar: Any, text: str, *, refresh: bool = False) -> None:
        bar.postfix = text
        if refresh:
            bar.refresh()

    def complete_job(self, label: str) -> None:
        if not self.enabled:
            return
        with self._lock:
            self.close_bar(label)
            if self._overall is not None:
                self._overall.update(1)

    def close_bar(self, label: str) -> None:
        if not self.enabled:
            return
        with self._lock:
            bar = self._bars.pop(label, None)
            if bar is not None:
                bar.close()
            position = self._positions.pop(label, None)
            if position is not None:
                self._free_positions.append(position)
                self._free_positions.sort()
            self._last_downloaded.pop(label, None)
            self._last_updated.pop(label, None)

    def hook(self, label: str, status: dict[str, Any]) -> None:
        if not self.enabled:
            return

        state = status.get("status", "")
        if state == "downloading":
            self._update_download(label, status)
            return

        if state == "finished":
            total = status.get("total_bytes") or status.get("downloaded_bytes")
            self._finish_download(label, total)

    def _position_for(self, label: str) -> int:
        position = self._positions.get(label)
        if position is None:
            if self._free_positions:
                position = self._free_positions.pop(0)
            else:
                position = self._next_position
                self._next_position += 1
            self._positions[label] = position
        return position

    def _compact(self, text: str, *, limit: int = 90) -> str:
        cleaned = " ".join(text.split())
        if len(cleaned) <= limit:
            return cleaned
        return cleaned[: limit - 3] + "..."

    def _status_bar_for(self, label: str) -> Any:
        bar = self._bars.get(label)
        if bar is not None:
            return bar

        bar = self._load_tqdm()(
            total=1,
            desc=label,
            position=self._position_for(label),
            leave=False,
            dynamic_ncols=True,
            file=sys.stdout,
            bar_format="{desc}",
        )
        self._bars[label] = bar
        return bar

    def _bar_for(self, label: str, status: dict[str, Any]) -> Any:
        total_bytes = status.get("total_bytes") or status.get("total_bytes_estimate")
        bar = self._bars.get(label)
        if bar is None:
            total_mb = bytes_to_mb(total_bytes)
            bar = self._load_tqdm()(
                total=total_mb,
                desc=label,
                unit="MB",
                position=self._position_for(label),
                leave=False,
                dynamic_ncols=True,
                file=sys.stdout,
                bar_format="{desc}: {bar}| {percentage:3.0f}%{postfix}",
            )
            self._set_postfix(bar, "starting")
            if total_mb is None:
                bar.bar_format = "{desc}: {bar}|{postfix}"
            self._bars[label] = bar
            return bar

        bar.unit = "MB"
        bar.set_description_str(label, refresh=False)
        bar.bar_format = "{desc}: {bar}| {percentage:3.0f}%{postfix}"
        if total_bytes and bar.total is None:
            bar.total = bytes_to_mb(total_bytes)
        elif total_bytes and bar.total == 1 and bar.n == 0:
            bar.total = bytes_to_mb(total_bytes)
        elif not total_bytes and bar.total == 1 and bar.n == 0:
            bar.total = None
            bar.bar_format = "{desc}: {bar}|{postfix}"
        return bar

    def _update_download(self, label: str, status: dict[str, Any]) -> None:
        now = time.monotonic()
        with self._lock:
            last = self._last_updated.get(label, 0)
            if now - last < self.min_interval:
                return
            self._last_updated[label] = now

            downloaded = int(status.get("downloaded_bytes") or 0)
            previous = self._last_downloaded.get(label, 0)
            delta = max(0, downloaded - previous)
            self._last_downloaded[label] = downloaded

            bar = self._bar_for(label, status)
            if delta:
                bar.update(delta / BYTES_PER_MB)

            speed = status.get("speed")
            total = status.get("total_bytes") or status.get("total_bytes_estimate")
            downloaded_mb = bytes_to_mb(downloaded)
            total_mb = bytes_to_mb(total)
            self._set_postfix(
                bar,
                f" {format_mb_value(downloaded_mb)}/{format_mb_value(total_mb)} MB "
                f"{format_mb(speed)} MB/s",
            )
            bar.refresh()

    def _finish_download(self, label: str, total: int | float | None) -> None:
        with self._lock:
            bar = self._bars.get(label)
            if bar is not None and total:
                total_mb = bytes_to_mb(total)
                bar.total = total_mb
                if total_mb is not None and bar.n < total_mb:
                    bar.update(total_mb - bar.n)
                self._set_postfix(bar, "processing")
                bar.refresh()
