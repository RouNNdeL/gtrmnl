import json
import logging
import types

import pytest

from gtrmnl import bootstrap, garmin
from gtrmnl.config import Config
from gtrmnl.state import State

TOKENS = json.dumps(
    {"di_token": "tok-secret", "di_refresh_token": "ref-secret", "di_client_id": "c"}
)


class AuthErr(Exception):
    pass


class ConnErr(Exception):
    pass


class TooMany(Exception):
    pass


def fake_lib(accounts, calls):
    """accounts: {(email, password): "ok" | "mfa"}; anything else is rejected."""

    class Client:
        def __init__(self, owner):
            self.owner = owner
            self.data = None

        def loads(self, text):
            data = json.loads(text)
            if not data.get("di_token"):
                raise ConnErr("structurally failed")
            self.data = data

        def dump(self, path):
            from pathlib import Path

            Path(path, "garmin_tokens.json").write_text(json.dumps(self.data))

    class Garmin:
        def __init__(self, email=None, password=None, return_on_mfa=False):
            self.creds = (email, password)
            self.client = Client(self)

        def login(self, tokenstore=None):
            if tokenstore:
                calls.append("token-login")
                return None, None
            calls.append(("password-login", self.creds[0]))
            result = accounts.get(self.creds)
            if result is None:
                raise AuthErr("bad credentials")
            self.client.data = {"di_token": "new", "di_refresh_token": "r", "di_client_id": "c"}
            return ("needs_mfa", {}) if result == "mfa" else (None, None)

        def resume_login(self, state, code):
            calls.append(("mfa", code))

    return types.SimpleNamespace(
        Garmin=Garmin,
        GarminConnectAuthenticationError=AuthErr,
        GarminConnectConnectionError=ConnErr,
        GarminConnectTooManyRequestsError=TooMany,
        GarminConnectNotFoundError=type("NF", (ConnErr,), {}),
    )


@pytest.fixture
def lib(monkeypatch):
    accounts, calls = {("me@x", "right"): "ok", ("mfa@x", "right"): "mfa"}, []
    monkeypatch.setattr(garmin, "_lib", lambda: fake_lib(accounts, calls))
    return calls


def cfg(tmp_path, **kw):
    return Config(data_dir=tmp_path, **kw)


def test_nothing_configured(tmp_path, lib, caplog):
    c, st, memo = cfg(tmp_path), State(), bootstrap.Memo()
    with caplog.at_level(logging.WARNING):
        assert bootstrap.ensure_tokens(c, st, memo) == bootstrap.MISSING
        assert bootstrap.ensure_tokens(c, st, memo) == bootstrap.MISSING
    assert caplog.text.count("no Garmin tokens") == 1 and lib == []


def test_seed_writes_tokens_once(tmp_path, lib, caplog):
    c, st, memo = cfg(tmp_path, garmin_tokens=TOKENS), State(), bootstrap.Memo()
    with caplog.at_level(logging.DEBUG):
        assert bootstrap.ensure_tokens(c, st, memo) == bootstrap.SEEDED
    assert json.loads(c.token_file.read_text())["di_token"] == "tok-secret"
    assert (c.token_dir.stat().st_mode & 0o777) == 0o700
    assert st.seed_hash and "secret" not in caplog.text and "secret" not in st.seed_hash
    # The library refreshes the stored copy; the same seed must not overwrite it.
    c.token_file.write_text(json.dumps({"di_token": "refreshed"}))
    assert bootstrap.ensure_tokens(c, st, memo) == bootstrap.PRESENT
    assert "refreshed" in c.token_file.read_text()


def test_new_seed_replaces_tokens(tmp_path, lib):
    st, memo = State(), bootstrap.Memo()
    bootstrap.ensure_tokens(cfg(tmp_path, garmin_tokens=TOKENS), st, memo)
    st.auth_failed = True
    new = TOKENS.replace("tok-secret", "tok-2")
    c = cfg(tmp_path, garmin_tokens=new)
    assert bootstrap.ensure_tokens(c, st, memo) == bootstrap.SEEDED
    assert not st.auth_failed and "tok-2" in c.token_file.read_text()


def test_bad_seed_logged_once(tmp_path, lib, caplog):
    c, st, memo = cfg(tmp_path, garmin_tokens='{"nope": 1}'), State(), bootstrap.Memo()
    with caplog.at_level(logging.ERROR):
        assert bootstrap.ensure_tokens(c, st, memo) == bootstrap.MISSING
        assert bootstrap.ensure_tokens(c, st, memo) == bootstrap.MISSING
    assert caplog.text.count("not a valid garmin_tokens.json") == 1
    assert st.seed_hash is None


def test_password_login(tmp_path, lib, caplog):
    c = cfg(
        tmp_path,
        garmin_email="me@x",
        garmin_password="right",
        garmin_password_source="GARMIN_PASSWORD_FILE",
    )
    st, memo = State(), bootstrap.Memo()
    with caplog.at_level(logging.DEBUG):
        assert bootstrap.ensure_tokens(c, st, memo) == bootstrap.PASSWORD
    assert c.token_file.is_file() and "right" not in caplog.text
    assert "Remove GARMIN_PASSWORD_FILE" in caplog.text
    assert "right" not in json.dumps(st.__dict__)
    # Tokens now present: no second password login.
    assert bootstrap.ensure_tokens(c, st, memo) == bootstrap.PRESENT
    assert lib.count(("password-login", "me@x")) == 1


def test_password_retried_after_auth_failure(tmp_path, lib):
    c = cfg(tmp_path, garmin_email="me@x", garmin_password="right")
    st, memo = State(), bootstrap.Memo()
    bootstrap.ensure_tokens(c, st, memo)
    st.auth_failed = True  # tokens revoked later
    assert bootstrap.ensure_tokens(c, st, memo) == bootstrap.PASSWORD
    assert not st.auth_failed


def test_wrong_password_not_retried(tmp_path, lib):
    c = cfg(tmp_path, garmin_email="me@x", garmin_password="wrong")
    st, memo = State(), bootstrap.Memo()
    for _ in range(3):
        assert bootstrap.ensure_tokens(c, st, memo) == bootstrap.MISSING
    assert lib.count(("password-login", "me@x")) == 1


def test_mfa_account_waits_for_a_code(tmp_path, lib):
    # Without a code or TOTP secret, the login is parked until a code arrives
    # (details in test_bootstrap_mfa.py); no code is invented or prompted for.
    c = cfg(tmp_path, garmin_email="mfa@x", garmin_password="right")
    assert bootstrap.ensure_tokens(c, State(), bootstrap.Memo()) == bootstrap.MFA_PENDING
    assert (c.token_dir / bootstrap.PENDING_FILE).is_file() and not c.token_file.exists()
    assert not any(isinstance(x, tuple) and x[0] == "mfa" for x in lib)


def test_seed_wins_over_password(tmp_path, lib):
    c = cfg(tmp_path, garmin_tokens=TOKENS, garmin_email="me@x", garmin_password="right")
    assert bootstrap.ensure_tokens(c, State(), bootstrap.Memo()) == bootstrap.SEEDED
    assert not any(isinstance(x, tuple) for x in lib)


def test_secrets_not_in_repr(tmp_path):
    c = cfg(tmp_path, garmin_tokens=TOKENS, garmin_email="me@x", garmin_password="right")
    r = repr(c)
    assert "tok-secret" not in r and "'right'" not in r and "me@x" not in r
