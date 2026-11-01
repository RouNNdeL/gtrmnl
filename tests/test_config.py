import pytest

from gtrmnl.config import Config, ConfigError


def test_defaults(monkeypatch):
    for k in ("TRMNL_WEBHOOK_URL", "TRMNL_WEBHOOK_URL_FILE", "PAYLOAD_LIMIT", "TZ"):
        monkeypatch.delenv(k, raising=False)
    c = Config.from_env()
    assert c.webhook_url is None and c.payload_limit == 9800 and c.interval_minutes == 15
    assert str(c.tz) == "Europe/Warsaw"


def test_url_from_file(monkeypatch, tmp_path):
    f = tmp_path / "url"
    f.write_text("https://trmnl.com/api/custom_plugins/abc\n")
    monkeypatch.setenv("TRMNL_WEBHOOK_URL_FILE", str(f))
    c = Config.from_env()
    assert c.webhook_url.endswith("/abc") and "abc" not in repr(c)


def test_rejects_plain_http(monkeypatch):
    monkeypatch.delenv("TRMNL_WEBHOOK_URL_FILE", raising=False)
    monkeypatch.setenv("TRMNL_WEBHOOK_URL", "http://trmnl.com/x")
    with pytest.raises(ConfigError):
        Config.from_env()


def test_bad_int(monkeypatch):
    monkeypatch.setenv("INTERVAL_MINUTES", "soon")
    with pytest.raises(ConfigError):
        Config.from_env()


def test_garmin_secrets_from_files(monkeypatch, tmp_path):
    (tmp_path / "pw").write_text("hunter2\n")
    (tmp_path / "tok").write_text('{"di_token": "x"}\n')
    monkeypatch.setenv("GARMIN_PASSWORD_FILE", str(tmp_path / "pw"))
    monkeypatch.setenv("GARMIN_PASSWORD", "ignored")
    monkeypatch.setenv("GARMIN_TOKENS_FILE", str(tmp_path / "tok"))
    monkeypatch.setenv("GARMIN_EMAIL", "me@example.com")
    c = Config.from_env()
    assert c.garmin_password == "hunter2" and c.garmin_password_source == "GARMIN_PASSWORD_FILE"
    assert c.garmin_tokens == '{"di_token": "x"}' and c.garmin_email == "me@example.com"
    assert "hunter2" not in repr(c)


def test_garmin_password_plain_env(monkeypatch):
    monkeypatch.delenv("GARMIN_PASSWORD_FILE", raising=False)
    monkeypatch.setenv("GARMIN_PASSWORD", "pw")
    c = Config.from_env()
    assert c.garmin_password == "pw" and c.garmin_password_source == "GARMIN_PASSWORD"


def test_missing_secret_file_is_an_error(monkeypatch, tmp_path):
    monkeypatch.setenv("GARMIN_TOKENS_FILE", str(tmp_path / "absent"))
    with pytest.raises(ConfigError):
        Config.from_env()
