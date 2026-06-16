from __future__ import annotations

from typing import Any, Protocol


class ProgressReporterPort(Protocol):
    def message(self, label: str, text: str) -> None:
        """Update a user-visible status message."""

    def hook(self, label: str, status: dict[str, Any]) -> None:
        """Receive downloader progress events."""

    def complete_job(self, label: str) -> None:
        """Mark a batch job complete."""

    def close_bar(self, label: str) -> None:
        """Remove one worker progress bar."""

    def close(self) -> None:
        """Release progress resources."""
