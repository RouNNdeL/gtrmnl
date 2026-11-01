"""Unattended Garmin login from the environment.

Two optional ways to get tokens without `gtrmnl login` in a shell:

1. GARMIN_TOKENS_FILE (or GARMIN_TOKENS): a garmin_tokens.json made by
   `gtrmnl login` elsewhere. It is copied into /data/tokens when it is new or
   has changed; after that the library refreshes the copy in /data on its own.
2. GARMIN_EMAIL + GARMIN_PASSWORD_FILE (or GARMIN_PASSWORD): a password login
   when there are no working tokens. Accounts with MFA cannot use this. A
   rejected password is not retried until the process restarts with
   different credentials, so a wrong password cannot lock the account.

Nothing derived from the password is written to disk.
"""

from __future__ import annotations

import hashlib
import logging
import os
from dataclasses import dataclass, field

from . import garmin
from .config import Config
from .state import State

log = logging.getLogger(__name__)

PRESENT, SEEDED, PASSWORD, MISSING = "present", "seeded", "password", "missing"


@dataclass
class Memo:
    """Per-process memory of password attempts that failed for good."""

    failed: set[tuple[str, str]] = field(default_factory=set, repr=False)
    warned_missing: bool = False
    bad_seed: str | None = None  # fingerprint of a seed that did not parse


def _fingerprint(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()[:16]


def ensure_tokens(cfg: Config, st: State, memo: Memo) -> str:
    """Make sure /data/tokens holds tokens if the environment can provide them.

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
            log.info(
                "Garmin tokens seeded from GARMIN_TOKENS%s",
                "_FILE" if _from_file("GARMIN_TOKENS") else "",
            )
            return SEEDED
    return _fallback(cfg, st, memo)


def _fallback(cfg: Config, st: State, memo: Memo) -> str:
    have = cfg.token_file.is_file()
    if have and not st.auth_failed:
        return PRESENT

    creds = (cfg.garmin_email or "", cfg.garmin_password or "")
    if all(creds) and creds not in memo.failed:
        try:
            garmin.password_login(cfg.token_dir, *creds)
        except garmin.TransientError as e:
            log.warning("Garmin password login postponed: %s", e)
            return PRESENT if have else MISSING
        except garmin.AuthError as e:
            memo.failed.add(creds)
            log.error("Garmin password login failed, not retrying with these credentials: %s", e)
            return PRESENT if have else MISSING
        st.auth_failed = False
        st.last_error = None
        log.warning(
            "Garmin tokens saved in %s. Remove %s and GARMIN_EMAIL from the deployment; "
            "the tokens refresh on their own.",
            cfg.token_dir,
            cfg.garmin_password_source or "GARMIN_PASSWORD",
        )
        return PASSWORD

    if not have and not memo.warned_missing:
        log.warning(
            "no Garmin tokens: run `gtrmnl login` in this container, or set "
            "GARMIN_TOKENS_FILE, or GARMIN_EMAIL + GARMIN_PASSWORD_FILE"
        )
        memo.warned_missing = True
    return PRESENT if have else MISSING


def _from_file(name: str) -> bool:
    return bool(os.environ.get(f"{name}_FILE", "").strip())
