"""Unattended Garmin login from the environment.

Ways to get tokens without `gtrmnl login` in a shell:

1. GARMIN_TOKENS_FILE (or GARMIN_TOKENS): a garmin_tokens.json made by
   `gtrmnl login` elsewhere. It is copied into /data/tokens when it is new or
   has changed; after that the library refreshes the copy in /data on its own.
2. GARMIN_EMAIL + GARMIN_PASSWORD_FILE (or GARMIN_PASSWORD): a password login
   when there are no working tokens. For accounts with MFA, either:
   a. GARMIN_TOTP_SECRET_FILE: the authenticator-app secret; codes are
      generated here, so this stays fully unattended; or
   b. a code sent by email or SMS, over two container starts. The first start
      logs in, Garmin sends the code, the half-finished login is saved in
      /data/tokens/mfa_pending.json (0600) and the process exits. The user adds
      GARMIN_MFA_CODE and starts the container again, which finishes the
      saved login.

A new code is requested only when there is no saved login, or when the
supplied code was rejected, so a container restarting in a loop without a
code never asks Garmin for more. A code is never tried twice, and a code
left in the environment from an earlier login is never used for a new one.
A rejected password is not retried until the process restarts. Codes,
secrets and passwords are never logged, and nothing derived from the
password is written to disk.
"""

from __future__ import annotations

import contextlib
import hashlib
import json
import logging
import os
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
PENDING_FILE = "mfa_pending.json"


@dataclass
class Memo:
    """Per-process memory, deliberately not persisted."""

    failed: set[tuple[str, str]] = field(default_factory=set, repr=False)
    warned_missing: bool = False
    bad_seed: str | None = None  # fingerprint of a seed that did not parse


def _fingerprint(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()[:16]


def ensure_tokens(cfg: Config, st: State, memo: Memo) -> str:
    """Make sure /data/tokens holds tokens if the environment can provide them.

    Returns MFA_PENDING when the login waits for GARMIN_MFA_CODE; the caller
    should then exit so the user can add it and start the container again.
    Mutates `st` (seed fingerprint, auth flags) but does not save it.
    """
    if cfg.garmin_tokens:
        fp = _fingerprint(cfg.garmin_tokens)
        if fp not in (st.seed_hash, memo.bad_seed):
            try:
                garmin.seed_tokens(cfg.token_dir, cfg.garmin_tokens)
            except garmin.AuthError as e:
                memo.bad_seed = fp
                log.error("%s; ignoring it", e)
                return _fallback(cfg, st, memo)
            st.seed_hash = fp
            st.auth_failed = False
            st.last_error = None
            _drop_pending(cfg)
            log.info(
                "Garmin tokens seeded from GARMIN_TOKENS%s",
                "_FILE" if _from_file("GARMIN_TOKENS") else "",
            )
            return SEEDED
    return _fallback(cfg, st, memo)


def _fallback(cfg: Config, st: State, memo: Memo) -> str:
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

    code = cfg.garmin_mfa_code
    code_fp = _fingerprint(code) if code else None
    used: list[str] = []  # code fingerprints not to try on the next saved login

    pending = _load_pending(cfg)
    if pending is not None:
        if code_fp is None:
            log.warning("Garmin is waiting for an MFA code: %s", _how_to(cfg, stale=False))
            return MFA_PENDING
        if code_fp in pending["tried"]:
            log.warning(
                "GARMIN_MFA_CODE was already used; Garmin is waiting for the newer code: %s",
                _how_to(cfg, stale=True),
            )
            return MFA_PENDING
        try:
            garmin.finish_login(cfg.token_dir, pending["state"], code)
        except garmin.TransientError as e:
            log.warning("Garmin MFA verification postponed, try again: %s", e)
            return MFA_PENDING
        except garmin.AuthError as e:
            # Wrong or expired code, or a saved login Garmin no longer accepts.
            # Either way the saved login is spent: start a new one below.
            log.error("Garmin rejected the MFA code (%s); requesting a new one", e)
            used = [*pending["tried"], code_fp]
            _drop_pending(cfg)
        else:
            _drop_pending(cfg)
            return _logged_in(cfg, st, used_code=True)

    ask = cfg.garmin_totp.fresh if cfg.garmin_totp is not None else None
    try:
        state = garmin.begin_login(cfg.token_dir, *creds, ask_mfa=ask)
    except garmin.TransientError as e:
        log.warning("Garmin password login postponed: %s", e)
        return idle
    except garmin.AuthError as e:
        memo.failed.add(creds)
        log.error("Garmin password login failed, not retrying with these credentials: %s", e)
        return idle
    if state is None:
        return _logged_in(cfg, st, used_code=False)

    # Garmin sent a code. Any code already in the environment belongs to an
    # earlier login and must not be spent on this one.
    if code_fp is not None and code_fp not in used:
        used.append(code_fp)
    _save_pending(cfg, {"state": state, "tried": used})
    method = str(state.get("method") or "email").lower() if isinstance(state, dict) else ""
    where = {"email": "by email", "sms": "by text message"}.get(method, "in your authenticator app")
    log.warning("Garmin sent an MFA code %s. %s", where, _how_to(cfg, stale=code_fp is not None))
    return MFA_PENDING


def _logged_in(cfg: Config, st: State, used_code: bool) -> str:
    st.auth_failed = False
    st.last_error = None
    remove = [cfg.garmin_password_source or "GARMIN_PASSWORD", "GARMIN_EMAIL"]
    if used_code:
        remove.append(cfg.garmin_mfa_code_source or "GARMIN_MFA_CODE")
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


def _how_to(cfg: Config, stale: bool) -> str:
    verb = "Replace" if stale else "Add"
    return (
        f"{verb} GARMIN_MFA_CODE=<code> in the app's environment and start the "
        "container again (the saved login waits in "
        f"{cfg.token_dir / PENDING_FILE})"
    )


# --- saved half-finished login ------------------------------------------------


def _pending_path(cfg: Config) -> Path:
    return cfg.token_dir / PENDING_FILE


def _load_pending(cfg: Config) -> dict | None:
    try:
        data = json.loads(_pending_path(cfg).read_text(encoding="utf-8"))
        if not isinstance(data.get("tried"), list) or "state" not in data:
            raise ValueError
    except FileNotFoundError:
        return None
    except (OSError, ValueError, AttributeError):
        log.warning("discarding an unreadable saved MFA login")
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


def _from_file(name: str) -> bool:
    return bool(os.environ.get(f"{name}_FILE", "").strip())
