from __future__ import annotations

from pathlib import Path
from typing import Protocol


class UrlListStorePort(Protocol):
    def read_urls(self, path: str | Path) -> list[str]:
        """Read URLs from a text file."""

    def append_urls(self, path: str | Path, urls: list[str]) -> None:
        """Append URLs to a text file."""
