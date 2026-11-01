"""Generate trmnl/fixtures/*.json from synthetic scenarios.

Each fixture is the merge-variable payload the collector would push, plus
"__now" (epoch) to pin the clock when rendering. Run from the repo root:

    PYTHONPATH=src python trmnl/tools/make_fixtures.py

CI regenerates them and fails if the committed files differ.
"""

from __future__ import annotations

import datetime as dt
import json
from pathlib import Path
from zoneinfo import ZoneInfo

from gtrmnl import normalize, payload, synth
from gtrmnl.config import Config

ROOT = Path(__file__).resolve().parents[2]
OUT = ROOT / "trmnl" / "fixtures"
TZ = ZoneInfo("Europe/Warsaw")
NOW = dt.datetime(2026, 11, 1, 18, 0, tzinfo=TZ)  # a Sunday evening


def build(kind: str, limit: int = 9800) -> dict:
    cfg = Config(data_dir=OUT)
    raw, series = synth.scenario(kind, NOW)
    model = normalize.build_model(raw, series, NOW, TZ, cfg)
    p = payload.fit(model, limit, cfg.recent_count).payload
    now = int(NOW.timestamp())
    if kind == "stale":
        p["ts"] = now - 6 * 3600
    if kind == "auth_error":
        p["ts"] = now - 9 * 3600
        p["err"] = "auth"
        p["et"] = now - 8 * 3600
    return {"__now": now, **p}


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    for kind in synth.SCENARIOS:
        data = build(kind)
        text = json.dumps(data, indent=1, ensure_ascii=False) + "\n"
        (OUT / f"{kind}.json").write_text(text, encoding="utf-8")
    # The long ride again, squeezed hard (shows what the fitting ladder drops).
    data = build("long", limit=1100)
    (OUT / "long_tight.json").write_text(
        json.dumps(data, indent=1, ensure_ascii=False) + "\n", encoding="utf-8"
    )


if __name__ == "__main__":
    main()
