from __future__ import annotations

import json
import threading
import time
from pathlib import Path
from typing import Any


class BrowserDebugLog:
    def __init__(self, path: str) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        self._started = time.time()
        self.path.write_text("", encoding="utf-8")
        self.event("debug_started", path=str(self.path))

    def event(self, name: str, **fields: Any) -> None:
        payload = {
            "time": round(time.time() - self._started, 3),
            "event": name,
            **{key: self._clean(value) for key, value in fields.items()},
        }
        with self._lock:
            with self.path.open("a", encoding="utf-8") as file:
                file.write(json.dumps(payload, ensure_ascii=False) + "\n")

    def _clean(self, value: Any) -> Any:
        if isinstance(value, (str, int, float, bool)) or value is None:
            return value
        return str(value)
