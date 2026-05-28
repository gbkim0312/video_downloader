from __future__ import annotations

from pathlib import Path

from video_downloader.domain.models import ProxySettings


DEFAULT_PROXY_INFO_PATH = ".proxyinfo"


def read_proxy_info(path: str | Path = DEFAULT_PROXY_INFO_PATH) -> ProxySettings:
    values = _read_env_file(path)
    proxy_url = values.get("PROXY_URL") or values.get("TOR_PROXY_URL")
    if not proxy_url:
        host = values.get("PROXY_HOST", "127.0.0.1")
        port = values.get("PROXY_PORT", "9050")
        scheme = values.get("PROXY_SCHEME", "socks5")
        proxy_url = f"{scheme}://{host}:{port}"

    return ProxySettings(
        proxy_url=proxy_url,
        control_host=values.get("TOR_CONTROL_HOST", "127.0.0.1"),
        control_port=_int_value(values.get("TOR_CONTROL_PORT"), 9051),
        control_password=values.get("TOR_CONTROL_PASSWORD") or None,
        newnym_delay=_float_value(values.get("TOR_NEWNYM_DELAY"), 10),
        rotation_retries=_int_value(values.get("TOR_ROTATION_RETRIES"), 1),
        rotate_on_status=_status_codes(values.get("TOR_ROTATE_ON_STATUS")),
        ip_check_url=values.get("PROXY_IP_CHECK_URL", "https://api.ipify.org"),
    )


def _read_env_file(path: str | Path) -> dict[str, str]:
    proxy_path = Path(path)
    if not proxy_path.exists():
        raise ValueError(f"Proxy config file not found: {proxy_path}")

    values: dict[str, str] = {}
    for raw_line in proxy_path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if "=" not in line:
            continue
        key, value = line.split("=", 1)
        values[key.strip()] = _unquote(value.strip())
    return values


def _unquote(value: str) -> str:
    if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}:
        return value[1:-1]
    return value


def _int_value(value: str | None, default: int) -> int:
    if value is None:
        return default
    try:
        return int(value)
    except ValueError:
        return default


def _float_value(value: str | None, default: float) -> float:
    if value is None:
        return default
    try:
        return float(value)
    except ValueError:
        return default


def _status_codes(value: str | None) -> tuple[int, ...]:
    if not value:
        return (403, 429, 500, 502, 503, 504)
    codes: list[int] = []
    for part in value.split(","):
        try:
            codes.append(int(part.strip()))
        except ValueError:
            continue
    return tuple(codes) or (403, 429, 500, 502, 503, 504)
