"""Garmin Connect access through python-garminconnect.

Only this module talks to Garmin. It returns raw responses; normalize.py
turns them into the model.
"""

from __future__ import annotations

import contextlib
import datetime as dt
import json
import logging
import os
from collections.abc import Callable
from pathlib import Path
from typing import Any

from . import normalize

log = logging.getLogger(__name__)


class AuthError(Exception):
    """Tokens are missing, expired or revoked: run `gtrmnl login`."""


class MfaRequired(AuthError):
    """The account asks for an MFA code and nobody is there to type it."""


class MfaStateInvalid(AuthError):
    """The saved pending MFA login cannot be resumed; start a new one."""


class TransientError(Exception):
    """Network trouble or rate limiting; try again next interval."""


def _lib():
    import garminconnect  # imported lazily so tests need no network stack

    return garminconnect


def connect(token_dir: Path):
    """Log in with saved tokens only. Never uses a password."""
    gc = _lib()
    if not (token_dir / "garmin_tokens.json").is_file():
        raise AuthError("no Garmin tokens yet")
    client = gc.Garmin()
    try:
        client.login(str(token_dir))
    except gc.GarminConnectAuthenticationError as e:
        raise AuthError(str(e)) from None
    except gc.GarminConnectTooManyRequestsError as e:
        raise TransientError(f"rate limited: {e}") from None
    except gc.GarminConnectConnectionError as e:
        raise TransientError(f"connection error: {type(e).__name__}") from None
    return client


def _prepare_dir(token_dir: Path) -> None:
    token_dir.mkdir(mode=0o700, parents=True, exist_ok=True)
    os.chmod(token_dir, 0o700)


@contextlib.contextmanager
def _login_errors(gc: Any):
    try:
        yield
    except gc.GarminConnectAuthenticationError as e:
        raise AuthError(str(e)) from None
    except gc.GarminConnectTooManyRequestsError as e:
        raise TransientError(f"rate limited: {e}") from None
    except gc.GarminConnectConnectionError as e:
        raise TransientError(f"connection error: {type(e).__name__}") from None


def _save_tokens(client: Any, token_dir: Path) -> None:
    client.client.dump(str(token_dir))
    # Prove the stored tokens work on their own.
    connect(token_dir)


def begin_login(
    token_dir: Path,
    email: str,
    password: str,
    ask_mfa: Callable[[], str] | None = None,
) -> dict | None:
    """Password login. Writes tokens only, never the password.

    When Garmin asks for an MFA code: with `ask_mfa` the code is requested
    and the login completed here; without it, Garmin sends the code (email or
    SMS) and the pending login is returned as JSON-serializable state for
    finish_login(). It holds SSO session cookies: store it like a password.
    Returns None once tokens are saved.
    """
    gc = _lib()
    _prepare_dir(token_dir)
    client = gc.Garmin(email, password, return_on_mfa=True)
    with _login_errors(gc):
        status, state = client.login()
        if status == "needs_mfa":
            if ask_mfa is None:
                return state
            client.resume_login(state, ask_mfa().strip())
    _save_tokens(client, token_dir)
    return None


def finish_login(token_dir: Path, state: dict, code: str) -> None:
    """Complete a login begun by begin_login(), possibly in another process."""
    gc = _lib()
    _prepare_dir(token_dir)
    client = gc.Garmin()
    try:
        with _login_errors(gc):
            client.resume_login(state, code.strip())
    except AuthError as e:
        if "client_state" in str(e):
            raise MfaStateInvalid(str(e)) from None
        raise
    _save_tokens(client, token_dir)


def password_login(
    token_dir: Path,
    email: str,
    password: str,
    ask_mfa: Callable[[], str] | None = None,
) -> None:
    """Login that must finish now: raises MfaRequired if a code is needed."""
    if begin_login(token_dir, email, password, ask_mfa) is not None:
        raise MfaRequired(
            "the account needs an MFA code: set GARMIN_MFA_CODE_FILE or "
            "GARMIN_TOTP_SECRET_FILE, run `gtrmnl login`, or seed tokens"
        )


# Kept for the interactive `gtrmnl login` command.
def interactive_login(
    token_dir: Path, email: str, password: str, ask_mfa: Callable[[], str]
) -> None:
    password_login(token_dir, email, password, ask_mfa)


def seed_tokens(token_dir: Path, tokens_json: str) -> None:
    """Write tokens produced elsewhere (a garmin_tokens.json) into the store.

    The library parses and validates them and writes the file 0600 in a 0700
    directory, refusing symlinks. From then on it refreshes them in place.
    """
    gc = _lib()
    client = gc.Garmin()
    try:
        client.client.loads(tokens_json)
    except Exception:  # the library wraps parse errors; never echo token text
        raise AuthError("GARMIN_TOKENS is not a valid garmin_tokens.json") from None
    _prepare_dir(token_dir)
    client.client.dump(str(token_dir))


def _call(fn: Callable[..., Any], *args: Any, **kw: Any) -> Any:
    """Call one endpoint; missing optional data is not an error."""
    gc = _lib()
    try:
        return fn(*args, **kw)
    except gc.GarminConnectAuthenticationError as e:
        raise AuthError(str(e)) from None
    except gc.GarminConnectTooManyRequestsError as e:
        raise TransientError(f"rate limited: {e}") from None
    except gc.GarminConnectNotFoundError:
        return None
    except gc.GarminConnectConnectionError as e:
        log.warning("%s failed: %s", getattr(fn, "__name__", "call"), type(e).__name__)
        return None


def fetch_raw(client: Any, today: dt.date) -> dict:
    """Everything except per-activity details. About 7 API calls."""
    iso = today.isoformat()
    since = normalize.week_start(today) - dt.timedelta(days=7)
    acts = _call(client.get_activities_by_date, since.isoformat(), iso)
    if acts is None:
        raise TransientError("activity list unavailable")
    if not acts:
        # Nothing in the last two weeks: still show the latest activity.
        acts = _call(client.get_activities, 0, 1) or []
    yesterday = (today - dt.timedelta(days=1)).isoformat()
    raw = {
        "activities": acts,
        "user_summary": _call(client.get_user_summary, iso),
        "sleep": _call(client.get_sleep_data, iso),
        "hrv": _call(client.get_hrv_data, iso),
        "readiness": _call(client.get_training_readiness, iso),
        "training_status": _call(client.get_training_status, iso),
    }
    us = raw["user_summary"] or {}
    if us.get("bodyBatteryMostRecentValue") is None:
        raw["body_battery"] = _call(client.get_body_battery, yesterday, iso)
    ts = raw["training_status"] or {}
    if not normalize.g(ts, "mostRecentVO2Max", "generic"):
        raw["max_metrics"] = _call(client.get_max_metrics, iso)
    return raw


def fetch_details(client: Any, activity_id: Any) -> dict | None:
    return _call(client.get_activity_details, str(activity_id), maxchart=2000, maxpoly=4000)


def fetch_hr_zones(client: Any, activity_id: Any) -> Any:
    return _call(client.get_activity_hr_in_timezones, str(activity_id))


# Bump when the cached series format or its time base changes.
CACHE_VERSION = 2


class SeriesCache:
    """Extracted track/series per activity id, so details are fetched once."""

    def __init__(self, directory: Path, keep: int = 5):
        self.dir = directory
        self.keep = keep

    def _path(self, activity_id: Any) -> Path:
        return self.dir / f"act_{int(activity_id)}.json"

    def get(self, activity_id: Any) -> dict | None:
        try:
            return json.loads(self._path(activity_id).read_text(encoding="utf-8"))
        except (OSError, ValueError, TypeError):
            return None

    def put(self, activity_id: Any, entry: dict) -> None:
        self.dir.mkdir(parents=True, exist_ok=True)
        p = self._path(activity_id)
        tmp = p.with_suffix(".tmp")
        tmp.write_text(json.dumps(entry, separators=(",", ":")), encoding="utf-8")
        tmp.replace(p)
        files = sorted(self.dir.glob("act_*.json"), key=lambda f: f.stat().st_mtime)
        for old in files[: -self.keep]:
            old.unlink(missing_ok=True)


def latest_series(client: Any, raw: dict, cache: SeriesCache, skip_commutes: bool) -> dict | None:
    """Series for the activity the payload will show, from cache when possible.

    Also fills raw["hr_zones"] when the list entry lacks time in zones.
    """
    acts = sorted(
        (normalize.summary(a) for a in raw.get("activities") or [] if isinstance(a, dict)),
        key=lambda a: a["start"] or 0,
        reverse=True,
    )
    latest = normalize.pick_latest(acts, skip_commutes)
    if latest is None or latest["id"] is None:
        return None
    hit = cache.get(latest["id"])
    if hit is None or hit.get("v") != CACHE_VERSION:
        details = fetch_details(client, latest["id"])
        moving = not normalize.uses_timer(latest["cat"])
        hit = {
            "v": CACHE_VERSION,
            "series": normalize.extract_series(details, moving) if details else None,
        }
        if latest["zones"] is None:
            hit["hr_zones"] = fetch_hr_zones(client, latest["id"])
        if details is not None:
            cache.put(latest["id"], hit)
    if hit.get("hr_zones") is not None:
        raw["hr_zones"] = hit["hr_zones"]
    return hit.get("series")
