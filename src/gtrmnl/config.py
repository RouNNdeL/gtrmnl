"""Configuration from the environment.

The webhook URL is a bearer credential: anyone holding it can overwrite the
screen. It is never logged and never included in exceptions or repr().
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from zoneinfo import ZoneInfo

from .totp import Totp, TotpError

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


def secret(name: str) -> tuple[str | None, str | None]:
    """Value of NAME_FILE (preferred) or NAME, and which one supplied it."""
    path = os.environ.get(f"{name}_FILE", "").strip()
    if path:
        try:
            val = Path(path).read_text(encoding="utf-8").strip()
        except OSError as e:
            raise ConfigError(f"cannot read {name}_FILE: {e.strerror}") from None
        return (val or None), f"{name}_FILE"
    val = os.environ.get(name, "").strip()
    return (val or None), (name if val else None)


def _webhook_url() -> str | None:
    url, _ = secret("TRMNL_WEBHOOK_URL")
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
    # Optional unattended Garmin login (see bootstrap.py). Never logged.
    garmin_tokens: str | None = field(default=None, repr=False)
    garmin_email: str | None = field(default=None, repr=False)
    garmin_password: str | None = field(default=None, repr=False)
    garmin_password_source: str | None = None
    # MFA: an authenticator-app secret (fully unattended), or a one-time code
    # from GARMIN_MFA_CODE (fixed at start) / GARMIN_MFA_CODE_FILE (re-read).
    garmin_totp: Totp | None = field(default=None, repr=False)
    garmin_mfa_code_env: str | None = field(default=None, repr=False)
    garmin_mfa_code_file: Path | None = None
    mfa_wait_minutes: int = 15

    def mfa_code(self) -> str | None:
        """The current one-time MFA code, if one was supplied.

        The file is read on every call, so a code can be dropped in without
        restarting the container. A missing file just means "no code yet".
        """
        if self.garmin_mfa_code_file is not None:
            try:
                val = self.garmin_mfa_code_file.read_text(encoding="utf-8").strip()
            except OSError:
                val = ""
            return val.replace(" ", "") or None
        return self.garmin_mfa_code_env

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
        password, pw_source = secret("GARMIN_PASSWORD")
        totp_text, _ = secret("GARMIN_TOTP_SECRET")
        try:
            totp = Totp.parse(totp_text) if totp_text else None
        except TotpError as e:
            raise ConfigError(f"GARMIN_TOTP_SECRET: {e}") from None
        code_file = os.environ.get("GARMIN_MFA_CODE_FILE", "").strip()
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
            garmin_tokens=secret("GARMIN_TOKENS")[0],
            garmin_email=os.environ.get("GARMIN_EMAIL", "").strip() or None,
            garmin_password=password,
            garmin_password_source=pw_source,
            garmin_totp=totp,
            garmin_mfa_code_env=os.environ.get("GARMIN_MFA_CODE", "").replace(" ", "") or None,
            garmin_mfa_code_file=Path(code_file) if code_file else None,
            mfa_wait_minutes=_int("GARMIN_MFA_WAIT_MINUTES", 15, minimum=5),
        )
