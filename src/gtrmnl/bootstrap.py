"""Unattended Garmin login from the environment.

Ways to get tokens without `gtrmnl login` in a shell:

1. GARMIN_TOKENS_FILE (or GARMIN_TOKENS): a garmin_tokens.json made by
   `gtrmnl login` elsewhere. It is copied into /data/tokens when it is new or
   has changed; after that the library refreshes the copy in /data on its own.
2. GARMIN_EMAIL + GARMIN_PASSWORD_FILE (or GARMIN_PASSWORD): a password login
   when there are no working tokens. For accounts with MFA, either:
   a. GARMIN_TOTP_SECRET_FILE: the authenticator-app secret; codes are
      generated here, so this stays fully unattended; or
   b. a one-time code: Garmin sends it (email/SMS) when the login starts, the
      half-finished login is saved in /data/tokens/mfa_pending.json (0600),
      and it is completed once a code appears in GARMIN_MFA_CODE (set it and
      restart) or GARMIN_MFA_CODE_FILE (re-read while waiting).

Safeguards: a rejected password is not retried until the process restarts
with different credentials; a code is never tried twice; a pending login
expires after GARMIN_MFA_WAIT_MINUTES; at most MAX_MFA_REQUESTS codes are
requested per process start. Codes, secrets and passwords are never logged,
and nothing derived from the password is written to disk.
"""

from __future__ import annotations

import contextlib
import hashlib
import json
import logging
import os
import time
from dataclasses import dataclass, field
from pathlib import Path

from . import garmin
from .config import Config
from .state import State

log = logging.getLogger(__name__)

PRESENT, SEEDED, PASSWORD, MISSING, MFA_PENDING = (
    "present",
    "seeded",
    "password",
    "missing",
    "mfa_pending",
)
MAX_MFA_REQUESTS = 3
PENDING_FILE = "mfa_pending.json"


@dataclass
class Memo:
    """Per-process memory, deliberately not persisted."""

    failed: set[tuple[str, str]] = field(default_factory=set, repr=False)
    warned_missing: bool = False
    bad_seed: str | None = None  # fingerprint of a seed that did not parse
    mfa_requests: int = 0
    warned: set[str] = field(default_factory=set)

    def once(self, key: str) -> bool:
        """True the first time `key` is seen, to log something only once."""
        if key in self.warned:
            return False
        self.warned.add(key)
        return True


def _fingerprint(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()[:16]


def ensure_tokens(cfg: Config, st: State, memo: Memo, now: float | None = None) -> str:
    """Make sure /data/tokens holds tokens if the environment can provide them.

    Mutates `st` (seed fingerprint, auth flags) but does not save it.
    """
    now = time.time() if now is None else now
    if cfg.garmin_tokens:
        fp = _fingerprint(cfg.garmin_tokens)
        if fp not in (st.seed_hash, memo.bad_seed):
            try:
                garmin.seed_tokens(cfg.token_dir, cfg.garmin_tokens)
            except garmin.AuthError as e:
                memo.bad_seed = fp
                log.error("%s; ignoring it", e)
                return _fallback(cfg, st, memo, now)
            st.seed_hash = fp
            st.auth_failed = False
            st.last_error = None
            _drop_pending(cfg)
            log.info(
                "Garmin tokens seeded from GARMIN_TOKENS%s",
                "_FILE" if _from_file("GARMIN_TOKENS") else "",
            )
            return SEEDED
    return _fallback(cfg, st, memo, now)


def _fallback(cfg: Config, st: State, memo: Memo, now: float) -> str:
    have = cfg.token_file.is_file()
    if have and not st.auth_failed:
        _drop_pending(cfg)  # e.g. `gtrmnl login` finished it in a shell
        return PRESENT
    idle = PRESENT if have else MISSING

    creds = (cfg.garmin_email or "", cfg.garmin_password or "")
    if not all(creds):
        if not have and not memo.warned_missing:
            log.warning(
                "no Garmin tokens: run `gtrmnl login` in this container, or set "
                "GARMIN_TOKENS_FILE, or GARMIN_EMAIL + GARMIN_PASSWORD_FILE"
            )
            memo.warned_missing = True
        return idle
    if creds in memo.failed:
        return idle

    pending = _load_pending(cfg, now)
    if pending is not None:
        return _resume(cfg, st, memo, pending, idle)

    ask = None
    if cfg.garmin_totp is not None:
        totp = cfg.garmin_totp
        ask = totp.fresh
    elif memo.mfa_requests >= MAX_MFA_REQUESTS:
        if memo.once("mfa-cap"):
            log.error(
                "requested %d MFA codes without getting one back; not asking again "
                "until the container restarts",
                MAX_MFA_REQUESTS,
            )
        return idle

    try:
        state = garmin.begin_login(cfg.token_dir, *creds, ask_mfa=ask)
    except garmin.TransientError as e:
        log.warning("Garmin password login postponed: %s", e)
        return idle
    except garmin.AuthError as e:
        memo.failed.add(creds)
        log.error("Garmin password login failed, not retrying with these credentials: %s", e)
        return idle

    if state is not None:
        memo.mfa_requests += 1
        code_now = cfg.mfa_code()
        # A code already present belongs to an earlier login: never try it here.
        _save_pending(
            cfg,
            {
                "created": now,
                "state": state,
                "tried": [_fingerprint(code_now)] if code_now else [],
            },
        )
        memo.warned.discard("mfa-waiting")
        method = str(state.get("method") or "email").lower() if isinstance(state, dict) else ""
        where = {"email": "by email", "sms": "by text message"}.get(
            method, "in your authenticator app"
        )
        log.warning(
            "Garmin asked for an MFA code (%s). Within %d minutes, %s.",
            where,
            cfg.mfa_wait_minutes,
            _how_to_supply(cfg),
        )
        return MFA_PENDING
    return _logged_in(cfg, st, uses_code=False)


def _resume(cfg: Config, st: State, memo: Memo, pending: dict, idle: str) -> str:
    code = cfg.mfa_code()
    if not code:
        if memo.once("mfa-waiting"):
            log.info("waiting for the Garmin MFA code: %s", _how_to_supply(cfg))
        return MFA_PENDING
    fp = _fingerprint(code)
    if fp in pending["tried"]:
        if memo.once(f"mfa-tried-{fp}"):
            log.warning(
                "the MFA code supplied was already tried (or is from an earlier login); "
                "waiting for a new one"
            )
        return MFA_PENDING
    try:
        garmin.finish_login(cfg.token_dir, pending["state"], code)
    except garmin.TransientError as e:
        log.warning("Garmin MFA verification postponed: %s", e)
        return MFA_PENDING
    except garmin.MfaStateInvalid:
        _drop_pending(cfg)
        log.error("the saved MFA login cannot be resumed; a new code will be requested")
        return idle
    except garmin.AuthError as e:
        pending["tried"].append(fp)
        _save_pending(cfg, pending)
        log.error("Garmin rejected the MFA code (%s); supply a new one", e)
        return MFA_PENDING
    _drop_pending(cfg)
    return _logged_in(cfg, st, uses_code=True)


def _logged_in(cfg: Config, st: State, uses_code: bool) -> str:
    st.auth_failed = False
    st.last_error = None
    remove = [cfg.garmin_password_source or "GARMIN_PASSWORD", "GARMIN_EMAIL"]
    if uses_code:
        remove.append("GARMIN_MFA_CODE_FILE" if cfg.garmin_mfa_code_file else "GARMIN_MFA_CODE")
    log.warning(
        "Garmin tokens saved in %s. Remove %s from the deployment; the tokens "
        "refresh on their own.%s",
        cfg.token_dir,
        ", ".join(remove),
        " (Keeping the password and GARMIN_TOTP_SECRET lets the collector log in "
        "again by itself if Garmin revokes the tokens.)"
        if cfg.garmin_totp
        else "",
    )
    return PASSWORD


def _how_to_supply(cfg: Config) -> str:
    if cfg.garmin_mfa_code_file is not None:
        return f"write the code to {cfg.garmin_mfa_code_file} (no restart needed)"
    return (
        "set GARMIN_MFA_CODE and restart the app, or set GARMIN_MFA_CODE_FILE to a "
        "file you can write the code to"
    )


# --- pending login store -------------------------------------------------------


def _pending_path(cfg: Config) -> Path:
    return cfg.token_dir / PENDING_FILE


def _load_pending(cfg: Config, now: float) -> dict | None:
    p = _pending_path(cfg)
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
        created = float(data["created"])
        if not isinstance(data.get("tried"), list):
            raise ValueError
    except FileNotFoundError:
        return None
    except (OSError, ValueError, KeyError, TypeError):
        log.warning("discarding an unreadable pending MFA login")
        _drop_pending(cfg)
        return None
    if now - created > cfg.mfa_wait_minutes * 60:
        log.warning("the pending MFA login expired; a new code will be requested")
        _drop_pending(cfg)
        return None
    return data


def _save_pending(cfg: Config, data: dict) -> None:
    """Write 0600 in the 0700 token directory, atomically, refusing symlinks."""
    d = cfg.token_dir
    d.mkdir(mode=0o700, parents=True, exist_ok=True)
    os.chmod(d, 0o700)
    tmp = d / f".{PENDING_FILE}.{os.getpid()}.tmp"
    with contextlib.suppress(FileNotFoundError):
        tmp.unlink()
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0)
    fd = os.open(tmp, flags, 0o600)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(data, f)
        os.replace(tmp, _pending_path(cfg))
    except BaseException:
        with contextlib.suppress(OSError):
            tmp.unlink()
        raise


def _drop_pending(cfg: Config) -> None:
    with contextlib.suppress(FileNotFoundError):
        _pending_path(cfg).unlink()


def pending_exists(cfg: Config) -> bool:
    return _pending_path(cfg).is_file()


def _from_file(name: str) -> bool:
    return bool(os.environ.get(f"{name}_FILE", "").strip())
