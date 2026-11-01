"""MFA paths of bootstrap.ensure_tokens, against a fake python-garminconnect."""

import json
import logging
import stat
from typing import ClassVar

import pytest

from gtrmnl import bootstrap, garmin
from gtrmnl.config import Config
from gtrmnl.state import State
from gtrmnl.totp import Totp

SECRET = "GEZDGNBVGY3TQOJQGEZDGNBVGY3TQOJQ"
T0 = 1_800_000_000.0


class AuthErr(Exception):
    pass


class ConnErr(Exception):
    pass


class TooMany(Exception):
    pass


class FakeGarmin:
    """Account `me@x` / `right` with MFA. The valid code is FakeGarmin.code;
    `login` hands out a fresh state each time, like Garmin sending a new code."""

    code = "123456"
    method = "email"
    calls: ClassVar[list] = []
    logins = 0

    def __init__(self, email=None, password=None, return_on_mfa=False):
        self.creds = (email, password)
        self.client = self
        self.data = None

    # library surface used by gtrmnl.garmin
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
        if code != FakeGarmin.code:
            raise AuthErr("Widget MFA failed: Enter MFA Code")
        self.data = {"di_token": "t", "di_refresh_token": "r", "di_client_id": "c"}
        return None, None

    def dump(self, path):
        from pathlib import Path

        Path(path, "garmin_tokens.json").write_text(json.dumps(self.data))


@pytest.fixture
def lib(monkeypatch):
    FakeGarmin.calls, FakeGarmin.logins = [], 0
    FakeGarmin.code, FakeGarmin.method = "123456", "email"
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


def cfg(tmp_path, **kw):
    base = dict(data_dir=tmp_path, garmin_email="me@x", garmin_password="right")
    base.update(kw)
    return Config(**base)


def codefile(tmp_path, code=None):
    p = tmp_path / "mfa_code"
    if code is not None:
        p.write_text(code + "\n")
    return p


def pending(c):
    return c.token_dir / bootstrap.PENDING_FILE


def test_code_file_flow_without_restart(tmp_path, lib, caplog):
    f = codefile(tmp_path)
    c, st, memo = cfg(tmp_path, garmin_mfa_code_file=f), State(), bootstrap.Memo()
    with caplog.at_level(logging.INFO):
        assert bootstrap.ensure_tokens(c, st, memo, T0) == bootstrap.MFA_PENDING
    assert "by email" in caplog.text and str(f) in caplog.text
    p = pending(c)
    assert stat.S_IMODE(p.stat().st_mode) == 0o600
    assert stat.S_IMODE(c.token_dir.stat().st_mode) == 0o700
    # Still waiting: no new login, no code requested again.
    assert bootstrap.ensure_tokens(c, st, memo, T0 + 30) == bootstrap.MFA_PENDING
    assert lib.calls.count("password-login") == 1
    f.write_text("123 456\n")  # spaces are fine
    with caplog.at_level(logging.INFO):
        assert bootstrap.ensure_tokens(c, st, memo, T0 + 60) == bootstrap.PASSWORD
    assert c.token_file.is_file() and not p.exists()
    assert "123456" not in caplog.text and "123 456" not in caplog.text
    assert "Remove GARMIN_PASSWORD, GARMIN_EMAIL, GARMIN_MFA_CODE_FILE" in caplog.text


def test_env_code_flow_across_restart(tmp_path, lib):
    # First start: no code yet. Garmin sends one; pending login saved.
    st = State()
    assert bootstrap.ensure_tokens(cfg(tmp_path), st, bootstrap.Memo(), T0) == bootstrap.MFA_PENDING
    # Restart with GARMIN_MFA_CODE set: a new process resumes the saved login.
    c2 = cfg(tmp_path, garmin_mfa_code_env="123456")
    assert bootstrap.ensure_tokens(c2, st, bootstrap.Memo(), T0 + 120) == bootstrap.PASSWORD
    assert ("resume", 1, "123456") in lib.calls and lib.calls.count("password-login") == 1


def test_wrong_code_then_right_code(tmp_path, lib, caplog):
    f = codefile(tmp_path)
    c, st, memo = cfg(tmp_path, garmin_mfa_code_file=f), State(), bootstrap.Memo()
    bootstrap.ensure_tokens(c, st, memo, T0)
    f.write_text("000000")
    with caplog.at_level(logging.ERROR):
        assert bootstrap.ensure_tokens(c, st, memo, T0 + 10) == bootstrap.MFA_PENDING
    assert "rejected the MFA code" in caplog.text and "000000" not in caplog.text
    # The same wrong code is never sent again.
    assert bootstrap.ensure_tokens(c, st, memo, T0 + 20) == bootstrap.MFA_PENDING
    assert sum(1 for x in lib.calls if isinstance(x, tuple) and x[2] == "000000") == 1
    f.write_text("123456")
    assert bootstrap.ensure_tokens(c, st, memo, T0 + 30) == bootstrap.PASSWORD


def test_stale_code_not_used_for_a_new_login(tmp_path, lib):
    # The code from an earlier login is still in the env when tokens die later.
    c, st = cfg(tmp_path, garmin_mfa_code_env="654321"), State()
    assert bootstrap.ensure_tokens(c, st, bootstrap.Memo(), T0) == bootstrap.MFA_PENDING
    assert not any(isinstance(x, tuple) for x in lib.calls)  # 654321 never tried
    assert bootstrap.ensure_tokens(c, st, bootstrap.Memo(), T0 + 10) == bootstrap.MFA_PENDING
    assert not any(isinstance(x, tuple) for x in lib.calls)


def test_pending_expires_and_a_new_code_is_requested(tmp_path, lib):
    c, st, memo = cfg(tmp_path, mfa_wait_minutes=15), State(), bootstrap.Memo()
    bootstrap.ensure_tokens(c, st, memo, T0)
    assert json.loads(pending(c).read_text())["state"]["session"] == 1
    assert bootstrap.ensure_tokens(c, st, memo, T0 + 14 * 60) == bootstrap.MFA_PENDING
    assert lib.logins == 1
    assert bootstrap.ensure_tokens(c, st, memo, T0 + 16 * 60) == bootstrap.MFA_PENDING
    assert lib.logins == 2 and json.loads(pending(c).read_text())["state"]["session"] == 2


def test_code_requests_capped_per_process(tmp_path, lib, caplog):
    c, st, memo = cfg(tmp_path, mfa_wait_minutes=5), State(), bootstrap.Memo()
    t = T0
    with caplog.at_level(logging.ERROR):
        for _ in range(6):
            bootstrap.ensure_tokens(c, st, memo, t)
            t += 6 * 60  # each pending login expires before the next call
    assert lib.logins == bootstrap.MAX_MFA_REQUESTS
    assert caplog.text.count("not asking again") == 1
    # A restart (new Memo) may ask again.
    bootstrap.ensure_tokens(c, st, bootstrap.Memo(), t)
    assert lib.logins == bootstrap.MAX_MFA_REQUESTS + 1


def test_invalid_saved_state_is_discarded(tmp_path, lib):
    c, st = cfg(tmp_path, garmin_mfa_code_env="123456"), State()
    c.token_dir.mkdir(mode=0o700)
    pending(c).write_text(json.dumps({"created": T0, "state": {}, "tried": []}))
    assert bootstrap.ensure_tokens(c, st, bootstrap.Memo(), T0 + 1) == bootstrap.MISSING
    assert not pending(c).exists()


def test_corrupt_pending_file_is_discarded(tmp_path, lib):
    c = cfg(tmp_path)
    c.token_dir.mkdir(mode=0o700)
    pending(c).write_text("{not json")
    assert bootstrap.ensure_tokens(c, State(), bootstrap.Memo(), T0) == bootstrap.MFA_PENDING
    assert json.loads(pending(c).read_text())["state"]["session"] == 1


def test_totp_is_fully_unattended(tmp_path, lib, caplog):
    totp = Totp.parse(SECRET)
    lib.code, lib.method = None, "totp"

    class Clock(Totp):
        def fresh(self, now=None, margin=5.0, sleep=None):
            code = totp.at(T0)
            lib.code = code
            return code

    c, st = cfg(tmp_path, garmin_totp=Clock(totp.key)), State()
    with caplog.at_level(logging.INFO):
        assert bootstrap.ensure_tokens(c, st, bootstrap.Memo(), T0) == bootstrap.PASSWORD
    assert not pending(c).exists() and c.token_file.is_file()
    assert "log in again by itself" in caplog.text
    # Tokens revoked later: logs in again without anyone around.
    st.auth_failed = True
    assert bootstrap.ensure_tokens(c, st, bootstrap.Memo(), T0 + 3600) == bootstrap.PASSWORD
    assert lib.logins == 2


def test_wrong_password_with_mfa_account_stops(tmp_path, lib):
    c, memo = cfg(tmp_path, garmin_password="wrong"), bootstrap.Memo()
    for i in range(3):
        assert bootstrap.ensure_tokens(c, State(), memo, T0 + i) == bootstrap.MISSING
    assert lib.calls.count("password-login") == 1 and not pending(c).exists()


def test_successful_shell_login_clears_pending(tmp_path, lib):
    c, st = cfg(tmp_path), State()
    bootstrap.ensure_tokens(c, st, bootstrap.Memo(), T0)
    c.token_file.write_text("{}")  # `gtrmnl login` in a shell finished meanwhile
    assert bootstrap.ensure_tokens(c, st, bootstrap.Memo(), T0 + 5) == bootstrap.PRESENT
    assert not pending(c).exists()


def test_config_mfa_env(monkeypatch, tmp_path):
    monkeypatch.setenv("GARMIN_TOTP_SECRET", "not base32!")
    with pytest.raises(Exception, match="GARMIN_TOTP_SECRET: the TOTP secret is not valid base32"):
        Config.from_env()
    monkeypatch.setenv("GARMIN_TOTP_SECRET", SECRET)
    monkeypatch.setenv("GARMIN_MFA_CODE", "12 34 56")
    c = Config.from_env()
    assert c.garmin_totp is not None and c.mfa_code() == "123456"
    assert SECRET not in repr(c) and "123456" not in repr(c)
    monkeypatch.setenv("GARMIN_MFA_CODE_FILE", str(tmp_path / "code"))
    c = Config.from_env()
    assert c.mfa_code() is None  # file wins over the env value, and may not exist yet
    (tmp_path / "code").write_text("777888\n")
    assert c.mfa_code() == "777888"
