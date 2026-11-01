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
