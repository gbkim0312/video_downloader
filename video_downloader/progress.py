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


class ProgressReporter:
    def __init__(
        self,
        *,
        enabled: bool = True,
        min_interval: float = 0.2,
        total_jobs: int = 0,
    ) -> None:
        self.enabled = enabled
        self.min_interval = min_interval
        self._lock = threading.RLock()
        self._last_updated: dict[str, float] = {}
        self._bars: dict[str, Any] = {}
        self._last_downloaded: dict[str, int] = {}
        self._positions: dict[str, int] = {}
        self._next_position = 1
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
            if self._overall:
                self._overall.close()
                self._overall = None

    def message(self, label: str, text: str) -> None:
        if not self.enabled:
            return
        with self._lock:
            self._load_tqdm().write(f"[{label}] {text}", file=sys.stdout)

    def complete_job(self, label: str) -> None:
        if not self.enabled:
            return
        with self._lock:
            bar = self._bars.pop(label, None)
            if bar:
                bar.close()
            if self._overall:
                self._overall.update(1)

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
            position = self._next_position
            self._next_position += 1
            self._positions[label] = position
        return position

    def _bar_for(self, label: str, status: dict[str, Any]) -> tqdm:
        total_bytes = status.get("total_bytes") or status.get("total_bytes_estimate")
        bar = self._bars.get(label)
        if bar is None:
            total_mb = bytes_to_mb(total_bytes)
            bar = self._load_tqdm()(
                total=total_mb,
                desc=label,
                unit="MB",
                position=self._position_for(label),
                leave=True,
                dynamic_ncols=True,
                file=sys.stdout,
                bar_format="{l_bar}{bar}| {n:.1f}/{total_fmt} MB [{rate_fmt}]",
            )
            self._bars[label] = bar
            return bar

        if total_bytes and bar.total is None:
            bar.total = bytes_to_mb(total_bytes)
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
            bar.set_postfix_str(
                f"{format_mb(downloaded)}/{format_mb(total)} MB, "
                f"{format_mb(speed)} MB/s",
                refresh=False,
            )
            bar.refresh()

    def _finish_download(self, label: str, total: int | float | None) -> None:
        with self._lock:
            bar = self._bars.get(label)
            if bar and total:
                total_mb = bytes_to_mb(total)
                bar.total = total_mb
                if total_mb is not None and bar.n < total_mb:
                    bar.update(total_mb - bar.n)
                bar.set_postfix_str("processing", refresh=False)
                bar.refresh()
