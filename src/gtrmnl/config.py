"""Configuration from the environment.

The webhook URL is a bearer credential: anyone holding it can overwrite the
screen. It is never logged and never included in exceptions or repr().
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from zoneinfo import ZoneInfo

# TRMNL+ allows 10 kB per push. Stay a little under it; a 422 lowers it.
DEFAULT_PAYLOAD_LIMIT = 9800


class ConfigError(ValueError):
    pass


def _bool(name: str, default: bool) -> bool:
    raw = os.environ.get(name)
    if raw is None or raw.strip() == "":
        return default
    val = raw.strip().lower()
    if val in {"1", "true", "yes", "on"}:
        return True
    if val in {"0", "false", "no", "off"}:
        return False
    raise ConfigError(f"{name} must be true or false, got {raw!r}")


def _int(name: str, default: int, minimum: int = 0) -> int:
    raw = os.environ.get(name)
    if raw is None or raw.strip() == "":
        return default
    try:
        val = int(raw)
    except ValueError as e:
        raise ConfigError(f"{name} must be an integer, got {raw!r}") from e
    if val < minimum:
        raise ConfigError(f"{name} must be at least {minimum}, got {val}")
    return val


def _webhook_url() -> str | None:
    path = os.environ.get("TRMNL_WEBHOOK_URL_FILE")
    if path:
        try:
            url = Path(path).read_text(encoding="utf-8").strip()
        except OSError as e:
            raise ConfigError(f"cannot read TRMNL_WEBHOOK_URL_FILE: {e.strerror}") from None
    else:
        url = os.environ.get("TRMNL_WEBHOOK_URL", "").strip()
    if not url:
        return None
    if not url.startswith("https://"):
        raise ConfigError("the TRMNL webhook URL must start with https://")
    return url


@dataclass(frozen=True)
class Config:
    data_dir: Path = Path("/data")
    webhook_url: str | None = field(default=None, repr=False)
    interval_minutes: int = 15
    payload_limit: int = DEFAULT_PAYLOAD_LIMIT
    heartbeat_hours: int = 3
    tz: ZoneInfo = field(default_factory=lambda: ZoneInfo("Europe/Warsaw"))
    log_level: str = "INFO"
    recent_count: int = 5
    latest_skip_commutes: bool = False
    totals_include_commutes: bool = True

    @property
    def token_dir(self) -> Path:
        return self.data_dir / "tokens"

    @property
    def token_file(self) -> Path:
        return self.token_dir / "garmin_tokens.json"

    @property
    def state_file(self) -> Path:
        return self.data_dir / "state.json"

    @property
    def cache_dir(self) -> Path:
        return self.data_dir / "cache"

    @classmethod
    def from_env(cls) -> Config:
        tz_name = os.environ.get("TZ", "Europe/Warsaw").strip() or "Europe/Warsaw"
        try:
            tz = ZoneInfo(tz_name)
        except Exception as e:
            raise ConfigError(f"unknown TZ {tz_name!r}") from e
        return cls(
            data_dir=Path(os.environ.get("DATA_DIR", "/data")),
            webhook_url=_webhook_url(),
            interval_minutes=_int("INTERVAL_MINUTES", 15, minimum=5),
            payload_limit=_int("PAYLOAD_LIMIT", DEFAULT_PAYLOAD_LIMIT, minimum=1000),
            heartbeat_hours=_int("HEARTBEAT_HOURS", 3, minimum=1),
            tz=tz,
            log_level=os.environ.get("LOG_LEVEL", "INFO").upper(),
            recent_count=_int("RECENT_COUNT", 5, minimum=0),
            latest_skip_commutes=_bool("LATEST_SKIP_COMMUTES", False),
            totals_include_commutes=_bool("TOTALS_INCLUDE_COMMUTES", True),
        )
