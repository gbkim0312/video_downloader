from __future__ import annotations

import threading
import time
from typing import Any


def bytes_to_mb(value: int | float | None) -> float | None:
    if value is None:
        return None
    return float(value) / (1024 * 1024)


def format_mb(value: int | float | None) -> str:
    mb = bytes_to_mb(value)
    if mb is None:
        return "?"
    return f"{mb:.1f}"


class ProgressReporter:
    def __init__(self, *, enabled: bool = True, min_interval: float = 0.7) -> None:
        self.enabled = enabled
        self.min_interval = min_interval
        self._lock = threading.Lock()
        self._last_printed: dict[str, float] = {}

    def message(self, label: str, text: str) -> None:
        if not self.enabled:
            return
        with self._lock:
            print(f"[{label}] {text}", flush=True)

    def hook(self, label: str, status: dict[str, Any]) -> None:
        if not self.enabled:
            return

        state = status.get("status", "")
        now = time.monotonic()
        if state == "downloading":
            with self._lock:
                last = self._last_printed.get(label, 0)
                if now - last < self.min_interval:
                    return
                self._last_printed[label] = now
                downloaded = format_mb(status.get("downloaded_bytes"))
                total = format_mb(
                    status.get("total_bytes") or status.get("total_bytes_estimate")
                )
                speed = format_mb(status.get("speed"))
                print(
                    f"[{label}] downloading {downloaded} MB / {total} MB "
                    f"at {speed} MB/s",
                    flush=True,
                )
            return

        if state == "finished":
            total = status.get("total_bytes") or status.get("downloaded_bytes")
            self.message(label, f"downloaded {format_mb(total)} MB, processing...")
