from __future__ import annotations

from pathlib import Path


class TextUrlListStore:
    def read_urls(self, path: str | Path) -> list[str]:
        urls: list[str] = []
        with Path(path).open("r", encoding="utf-8") as file:
            for line in file:
                stripped = line.strip()
                if not stripped or stripped.startswith("#"):
                    continue
                urls.append(stripped)
        return urls

    def append_urls(self, path: str | Path, urls: list[str]) -> None:
        output = Path(path)
        output.parent.mkdir(parents=True, exist_ok=True)
        needs_leading_newline = (
            output.exists()
            and output.stat().st_size > 0
            and not output.read_bytes().endswith(b"\n")
        )
        with output.open("a", encoding="utf-8") as file:
            if needs_leading_newline and urls:
                file.write("\n")
            for url in urls:
                file.write(f"{url}\n")
