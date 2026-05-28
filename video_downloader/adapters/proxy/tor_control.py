from __future__ import annotations

import re
import socket
import threading
import time

from video_downloader.domain.models import ProxySettings


HTTP_STATUS_RE = re.compile(r"(?:HTTP Error|HTTP status|status code)\s+(\d{3})")


def should_rotate_proxy(error: BaseException, settings: ProxySettings | None) -> bool:
    if settings is None:
        return False
    status = http_status_from_error(error)
    return status in settings.rotate_on_status if status is not None else False


def http_status_from_error(error: BaseException) -> int | None:
    text = str(error)
    match = HTTP_STATUS_RE.search(text)
    if not match:
        return None
    try:
        return int(match.group(1))
    except ValueError:
        return None


class TorController:
    def __init__(self) -> None:
        self._lock = threading.Lock()

    def rotate_identity(self, settings: ProxySettings) -> None:
        with self._lock:
            with socket.create_connection(
                (settings.control_host, settings.control_port),
                timeout=10,
            ) as connection:
                auth_response = self._send(
                    connection,
                    self._auth_command(settings.control_password),
                )
                self._ensure_ok(auth_response, "Tor authentication failed")
                signal_response = self._send(connection, "SIGNAL NEWNYM\r\n")
                self._ensure_ok(signal_response, "Tor NEWNYM failed")

            if settings.newnym_delay > 0:
                time.sleep(settings.newnym_delay)

    def _auth_command(self, password: str | None) -> str:
        if not password:
            return "AUTHENTICATE\r\n"
        escaped = password.replace("\\", "\\\\").replace('"', '\\"')
        return f'AUTHENTICATE "{escaped}"\r\n'

    def _send(self, connection: socket.socket, command: str) -> str:
        connection.sendall(command.encode("utf-8"))
        return connection.recv(4096).decode("utf-8", errors="replace")

    def _ensure_ok(self, response: str, message: str) -> None:
        if not response.startswith("250"):
            raise RuntimeError(f"{message}: {response.strip()}")
