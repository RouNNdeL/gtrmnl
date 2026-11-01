"""MFA paths of bootstrap.ensure_tokens, against a fake python-garminconnect.

Each `start()` is one container start: a fresh process (Memo) and the
environment as configured for that start (Config). State on disk persists.
"""

import json
import logging
import stat
from typing import ClassVar

import pytest

from gtrmnl import bootstrap, cli, garmin
from gtrmnl.config import Config
from gtrmnl.state import State
from gtrmnl.totp import Totp

SECRET = "GEZDGNBVGY3TQOJQGEZDGNBVGY3TQOJQ"


class AuthErr(Exception):
    pass


class ConnErr(Exception):
    pass


class TooMany(Exception):
    pass


class FakeGarmin:
    """Account me@x / right with MFA. Every password login is a new session
    for which Garmin sends a new code: "code-<session>"."""

    method = "email"
    calls: ClassVar[list] = []
    logins = 0
    totp_code: str | None = None

    def __init__(self, email=None, password=None, return_on_mfa=False):
        self.creds = (email, password)
        self.client = self
        self.data = None

    def login(self, tokenstore=None):
        if tokenstore:
            FakeGarmin.calls.append("token-login")
            return None, None
        FakeGarmin.calls.append("password-login")
        if self.creds != ("me@x", "right"):
            raise AuthErr("bad credentials")
        FakeGarmin.logins += 1
        return "needs_mfa", {"method": FakeGarmin.method, "session": FakeGarmin.logins}

    def resume_login(self, state, code):
        FakeGarmin.calls.append(("resume", state.get("session"), code))
        if state.get("session") is None:
            raise AuthErr("Invalid MFA client_state; start the login again")
        valid = FakeGarmin.totp_code or f"code-{state['session']}"
        if code != valid:
            raise AuthErr("Widget MFA failed: Enter MFA Code")
        self.data = {"di_token": "t", "di_refresh_token": "r", "di_client_id": "c"}
        return None, None

    def dump(self, path):
        from pathlib import Path

        Path(path, "garmin_tokens.json").write_text(json.dumps(self.data))


@pytest.fixture
def lib(monkeypatch):
    FakeGarmin.calls, FakeGarmin.logins = [], 0
    FakeGarmin.method, FakeGarmin.totp_code = "email", None
    mod = type(
        "M",
        (),
        dict(
            Garmin=FakeGarmin,
            GarminConnectAuthenticationError=AuthErr,
            GarminConnectConnectionError=ConnErr,
            GarminConnectTooManyRequestsError=TooMany,
            GarminConnectNotFoundError=type("NF", (ConnErr,), {}),
        ),
    )
    monkeypatch.setattr(garmin, "_lib", lambda: mod)
    return FakeGarmin


@pytest.fixture
def start(tmp_path, lib):
    st = State()

    def _start(code=None, **kw):
        base = dict(
            data_dir=tmp_path,
            garmin_email="me@x",
            garmin_password="right",
            garmin_mfa_code=code,
            garmin_mfa_code_source="GARMIN_MFA_CODE" if code else None,
        )
        base.update(kw)
        c = Config(**base)
        return bootstrap.ensure_tokens(c, st, bootstrap.Memo()), c

    _start.state = st
    return _start


def pending(tmp_path):
    return tmp_path / "tokens" / bootstrap.PENDING_FILE


def resumes(lib):
    return [x for x in lib.calls if isinstance(x, tuple)]


def test_two_starts(start, lib, tmp_path, caplog):
    # Start 1: Garmin sends a code; the half-finished login is saved.
    with caplog.at_level(logging.WARNING):
        how, c = start()
    assert how == bootstrap.MFA_PENDING
    assert "sent an MFA code by email" in caplog.text
    assert "Add GARMIN_MFA_CODE=<code>" in caplog.text
    p = pending(tmp_path)
    assert stat.S_IMODE(p.stat().st_mode) == 0o600
    assert stat.S_IMODE(c.token_dir.stat().st_mode) == 0o700
    # Start 2: the user added the code; the saved login is finished.
    caplog.clear()
    with caplog.at_level(logging.INFO):
        how, c = start(code="code-1")
    assert how == bootstrap.PASSWORD and c.token_file.is_file() and not p.exists()
    assert lib.logins == 1 and resumes(lib) == [("resume", 1, "code-1")]
    assert "code-1" not in caplog.text
    assert "Remove GARMIN_PASSWORD, GARMIN_EMAIL, GARMIN_MFA_CODE" in caplog.text
    # Start 3: the code is still set but tokens exist: nothing happens.
    assert start(code="code-1")[0] == bootstrap.PRESENT
    assert lib.logins == 1 and len(resumes(lib)) == 1


def test_restart_loop_without_a_code_requests_nothing(start, lib):
    start()
    for _ in range(20):  # e.g. Docker restarting the exited container
        assert start()[0] == bootstrap.MFA_PENDING
    assert lib.logins == 1 and resumes(lib) == []


def test_rejected_code_requests_a_new_one(start, lib, caplog):
    start()
    with caplog.at_level(logging.ERROR):
        assert start(code="typo")[0] == bootstrap.MFA_PENDING
    assert "rejected the MFA code" in caplog.text and "typo" not in caplog.text
    assert lib.logins == 2  # a new code was sent
    # Restart loop with the same bad code: never retried, no further requests.
    for _ in range(5):
        assert start(code="typo")[0] == bootstrap.MFA_PENDING
    assert lib.logins == 2 and [r[2] for r in resumes(lib)] == ["typo"]
    # The user sets the new code.
    assert start(code="code-2")[0] == bootstrap.PASSWORD


def test_expired_session_is_treated_like_a_rejected_code(start, lib, tmp_path):
    start()
    data = json.loads(pending(tmp_path).read_text())
    data["state"] = {}  # what an unusable saved login looks like to the library
    pending(tmp_path).write_text(json.dumps(data))
    assert start(code="code-1")[0] == bootstrap.MFA_PENDING
    assert lib.logins == 2
    assert start(code="code-2")[0] == bootstrap.PASSWORD


def test_code_left_from_an_earlier_login_is_not_spent(start, lib, caplog):
    start()
    start(code="code-1")  # logged in; GARMIN_MFA_CODE stays in the env
    start.state.auth_failed = True  # weeks later Garmin revokes the tokens
    with caplog.at_level(logging.WARNING):
        assert start(code="code-1")[0] == bootstrap.MFA_PENDING
    assert lib.logins == 2 and len(resumes(lib)) == 1  # old code not sent again
    assert "Replace GARMIN_MFA_CODE" in caplog.text
    assert start(code="code-1")[0] == bootstrap.MFA_PENDING
    assert len(resumes(lib)) == 1
    assert start(code="code-2")[0] == bootstrap.PASSWORD


def test_network_error_keeps_the_saved_login(start, lib, monkeypatch, tmp_path):
    start()

    def boom(*a, **k):
        raise garmin.TransientError("connection error")

    monkeypatch.setattr(garmin, "finish_login", boom)
    assert start(code="code-1")[0] == bootstrap.MFA_PENDING
    assert pending(tmp_path).exists() and lib.logins == 1
    monkeypatch.undo()
    monkeypatch.setattr(
        garmin,
        "_lib",
        lambda: type(
            "M",
            (),
            dict(
                Garmin=FakeGarmin,
                GarminConnectAuthenticationError=AuthErr,
                GarminConnectConnectionError=ConnErr,
                GarminConnectTooManyRequestsError=TooMany,
                GarminConnectNotFoundError=ConnErr,
            ),
        ),
    )
    assert start(code="code-1")[0] == bootstrap.PASSWORD  # same code, not marked used


def test_corrupt_saved_login_is_discarded(start, lib, tmp_path):
    (tmp_path / "tokens").mkdir(mode=0o700)
    pending(tmp_path).write_text("{not json")
    assert start(code="code-1")[0] == bootstrap.MFA_PENDING
    assert lib.logins == 1 and json.loads(pending(tmp_path).read_text())["state"]["session"] == 1


def test_wrong_password_with_mfa_account(start, lib, tmp_path):
    assert start(garmin_password="wrong")[0] == bootstrap.MISSING
    assert lib.calls.count("password-login") == 1 and not pending(tmp_path).exists()


def test_shell_login_clears_saved_login(start, tmp_path):
    start()
    (tmp_path / "tokens" / "garmin_tokens.json").write_text("{}")  # `gtrmnl login`
    assert start()[0] == bootstrap.PRESENT and not pending(tmp_path).exists()


def test_totp_is_fully_unattended(start, lib, tmp_path, caplog):
    totp = Totp.parse(SECRET)

    class Fixed(Totp):
        def fresh(self, now=None, margin=5.0, sleep=None):
            return totp.at(1_800_000_000)

    lib.method, lib.totp_code = "totp", totp.at(1_800_000_000)
    with caplog.at_level(logging.WARNING):
        assert start(garmin_totp=Fixed(totp.key))[0] == bootstrap.PASSWORD
    assert not pending(tmp_path).exists()
    assert "log in again by itself" in caplog.text
    start.state.auth_failed = True  # revoked later: no one needs to be around
    assert start(garmin_totp=Fixed(totp.key))[0] == bootstrap.PASSWORD
    assert lib.logins == 2


def test_run_exits_while_waiting_for_the_code(tmp_path, lib, caplog):
    c = Config(data_dir=tmp_path, garmin_email="me@x", garmin_password="right")
    with caplog.at_level(logging.WARNING):
        assert cli.cmd_run(c, None) == cli.EXIT_MFA
    assert "exiting until GARMIN_MFA_CODE is set" in caplog.text
    assert pending(tmp_path).exists()


def test_config_reads_code_and_totp(monkeypatch, tmp_path):
    monkeypatch.setenv("GARMIN_TOTP_SECRET", "not base32!")
    with pytest.raises(Exception, match="GARMIN_TOTP_SECRET: the TOTP secret is not valid base32"):
        Config.from_env()
    monkeypatch.setenv("GARMIN_TOTP_SECRET", SECRET)
    monkeypatch.setenv("GARMIN_MFA_CODE", "12 34 56")
    c = Config.from_env()
    assert c.garmin_totp is not None and c.garmin_mfa_code == "123456"
    assert c.garmin_mfa_code_source == "GARMIN_MFA_CODE"
    assert SECRET not in repr(c) and "123456" not in repr(c)
    (tmp_path / "code").write_text("777888\n")
    monkeypatch.setenv("GARMIN_MFA_CODE_FILE", str(tmp_path / "code"))
    c = Config.from_env()
    assert c.garmin_mfa_code == "777888" and c.garmin_mfa_code_source == "GARMIN_MFA_CODE_FILE"
