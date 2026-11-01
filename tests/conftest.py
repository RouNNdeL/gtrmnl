import datetime as dt
from zoneinfo import ZoneInfo

import pytest

from gtrmnl import normalize, synth
from gtrmnl.config import Config

TZ = ZoneInfo("Europe/Warsaw")
# Sunday 2026-11-01 18:00 Warsaw
NOW = dt.datetime(2026, 11, 1, 18, 0, tzinfo=TZ)


def at(days_ago: int, hour: int = 7) -> dt.datetime:
    d = NOW.date() - dt.timedelta(days=days_ago)
    return dt.datetime(d.year, d.month, d.day, hour, 0, tzinfo=TZ)


def scenario(kind: str = "ride"):
    return synth.scenario(kind, NOW)


@pytest.fixture
def cfg(tmp_path):
    return Config(
        data_dir=tmp_path, webhook_url="https://example.invalid/api/custom_plugins/secret-uuid"
    )


@pytest.fixture
def model(cfg):
    raw, series = scenario("ride")
    return normalize.build_model(raw, series, NOW, TZ, cfg)
