"""Build the merge-variable payload and fit it to the webhook size limit.

Schema v1 (keys are short because every byte counts against the limit):

  v   schema version          ts  collector run time (epoch s)
  a   latest activity         wk  week totals        d7  km per day, 7 days
  rec recent activities       w   wellness           err error flag (optional)

See docs/payload.md for every field.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Iterator
from dataclasses import dataclass, replace
from typing import Any

from . import encode
from .normalize import HIKE, RIDE, RUN, split_unit_km, splits

SCHEMA_VERSION = 1
ROUTE_TOLERANCES = (0.5, 0.75, 1.0, 1.5, 2.0)
ROUTE_TOLERANCES_EMERGENCY = (3.0, 4.0, 6.0)


@dataclass(frozen=True)
class Options:
    route_tol: float | None = 0.5  # None drops the route
    max_splits: int | None = None  # None = all
    hr_bins: int = 120  # 0 drops the HR chart
    recent: int = 5
    d7: bool = True
    polyline: bool = True  # encoded route for the optional base map


def dumps(obj: Any) -> str:
    return json.dumps(obj, separators=(",", ":"), ensure_ascii=False)


def body_bytes(payload: dict) -> bytes:
    """Exactly what is POSTed to the webhook."""
    return dumps({"merge_variables": payload}).encode("utf-8")


def _i(x: float | None) -> int | None:
    return None if x is None else round(x)


def _r1(x: float | None) -> float | None:
    if x is None:
        return None
    v = round(x, 1)
    return int(v) if v == int(v) else v


def _clean(d: dict) -> dict:
    """Drop None values so missing data costs nothing and Liquid sees blank."""
    return {k: v for k, v in d.items() if v is not None}


# One letter per category in d14: run, bike, hike, walk, swim, gym, ski, other.
DAY_LETTER = {
    "run": "r",
    "ride": "b",
    "hike": "h",
    "walk": "w",
    "swim": "s",
    "strength": "g",
    "ski": "k",
    "other": "o",
}


def _day(d: Any) -> str:
    """d14 entry: '' rest day, '.' future, else letter + km ('r8.2'; 'g' without km)."""
    if d is None:
        return ""
    if d == "future":
        return "."
    cat, km = d
    letter = DAY_LETTER.get(cat, "o")
    return letter + (encode.compact_number(km) if km >= 0.05 else "")


def activity_block(act: dict, opt: Options) -> dict:
    s = act.get("series") or {}
    a: dict[str, Any] = {
        "n": act["name"][:60] or None,
        "sp": act["type"],
        "c": act["cat"],
        "cm": 1 if act["commute"] else None,
        "st": act["start"],
        "o": act.get("offset"),
        "d": _i(act["dist"]),
        "t": _i(act["elapsed"]),
        "mt": _i(act["moving"]),
        "hr": _i(act["avg_hr"]),
        "mx": _i(act["max_hr"]),
        "el": _i(act["elev"]),
        "kc": _i(act["kcal"]),
        "ld": _i(act["load"]),
    }
    te = act.get("te") or [None, None]
    if te[0] is not None:
        a["te"] = [_r1(te[0]), _r1(te[1] or 0)]
    if act.get("zones"):
        a["z"] = [round(z or 0) for z in act["zones"]]

    # Splits: seconds per unit (run, hike, walk) or km/h x10 (ride).
    if s.get("t") and opt.max_splits != 0:
        unit_km = split_unit_km(act["cat"], act["dist"])
        sp = splits(s["t"], s["d"], unit_km * 1000)
        if sp and len(sp) >= 2:
            if opt.max_splits:
                sp = sp[: opt.max_splits]
            if act["cat"] == RIDE:
                vals = [round(unit_km * 3600 / x * 10) for x in sp if x > 0]
            else:
                vals = [round(x) for x in sp]
            a["spl"] = ",".join(str(v) for v in vals)
            a["su"] = unit_km

    if opt.route_tol is not None and len(s.get("lat") or []) >= 2:
        enc = encode.route_svg(s["lat"], s["lon"], opt.route_tol)
        if enc:
            a["r"], a["rb"] = enc
        if opt.polyline:
            # Street-map detail does not need sub-pixel accuracy.
            a["p"] = encode.route_polyline(s["lat"], s["lon"], max(opt.route_tol, 1.0))

    if opt.hr_bins and s.get("t"):
        pairs = [(t, h) for t, h in zip(s["t"], s["hr"], strict=True) if h is not None]
        if len(pairs) >= 10:
            vals = encode.resample([p[0] for p in pairs], [p[1] for p in pairs], opt.hr_bins)
            if vals:
                a["h"] = encode.series_b64(vals)
    return _clean(a)


DEFAULT = Options()


def build(model: dict, opt: Options = DEFAULT) -> dict:
    p: dict[str, Any] = {"v": SCHEMA_VERSION, "ts": model["now"]}
    if model.get("activity"):
        p["a"] = activity_block(model["activity"], opt)

    t = model["totals"]
    wk = {}
    for cat in (RUN, RIDE, HIKE):
        km, n, sec = t[cat]
        lkm, ln, _ = t["l" + cat]
        wk[cat] = [_r1(km), n, round(sec)]
        wk["l" + cat] = [_r1(lkm), ln, _r1(t["l" + cat + "_td"])]
    p["wk"] = wk
    if opt.d7:
        p["d7"] = ",".join(encode.compact_number(x) for x in t["d7"])
        p["d14"] = ",".join(_day(d) for d in t["d14"])

    rec = model.get("recent") or []
    if opt.recent and rec:
        p["rec"] = [
            [
                r["cat"],
                r["start"],
                round(r["dist"]),
                round(r["moving"]),
                (r["name"] or "")[:40],
                _i(r["elev"]) or 0,
            ]
            for r in rec[: opt.recent]
        ]

    w = model.get("wellness") or {}
    p["w"] = _clean(
        {
            "bb": _i(w.get("bb")),
            "bbh": _i(w.get("bb_high")),
            "sl": _i(w.get("sleep_score")),
            "ss": _i(w.get("sleep_s")),
            "hrv": w.get("hrv_status"),
            "hv": _i(w.get("hrv")),
            "tr": _i(w.get("readiness")),
            "rc": _i(w.get("recovery_h")),
            "tst": w.get("status"),
            "vo": _r1(w.get("vo2")),
            "vc": _r1(w.get("vo2_ride")),
            "al": _i(w.get("acute")),
            "rhr": _i(w.get("rhr")),
        }
    )
    return p


def ladder(start: Options) -> Iterator[Options]:
    """Successively smaller payload options, in the agreed order of sacrifice.

    0. drop the base-map polyline; 1. coarsen the route (0.5 -> 2 px);
    2./3. thumbnails and elevation
    (not implemented yet); 4. fewer splits; 5. HR chart 120 -> 60 bins;
    6. fewer recent activities; then emergency steps.
    """
    o = start
    if o.polyline:
        yield o  # everything, including the base-map polyline
        # 0. drop the polyline: without it the markup draws the route line.
        o = replace(o, polyline=False)
        yield o
    for tol in ROUTE_TOLERANCES:
        if o.route_tol is not None and tol >= o.route_tol:
            o = replace(o, route_tol=tol)
            yield o
    if o.route_tol is None:
        yield o
    for ms in (12, 8):
        o = replace(o, max_splits=ms)
        yield o
    if o.hr_bins > 60:
        o = replace(o, hr_bins=60)
        yield o
    for r in range(min(o.recent, 5) - 1, -1, -1):
        o = replace(o, recent=r)
        yield o
    # Emergency: the limit is far below what was planned for (e.g. 2 kB).
    for tol in ROUTE_TOLERANCES_EMERGENCY:
        o = replace(o, route_tol=tol)
        yield o
    yield from (
        replace(o, max_splits=0),
        replace(o, max_splits=0, hr_bins=30),
        replace(o, max_splits=0, hr_bins=0),
        replace(o, max_splits=0, hr_bins=0, d7=False),
        replace(o, max_splits=0, hr_bins=0, d7=False, route_tol=None),
    )


@dataclass
class Fitted:
    payload: dict
    size: int
    options: Options
    fits: bool


def fit(model: dict, limit: int, recent: int = 5) -> Fitted:
    """Return the richest payload whose POST body is at most `limit` bytes."""
    last = None
    for opt in ladder(Options(recent=recent)):
        p = build(model, opt)
        size = len(body_bytes(p))
        last = Fitted(p, size, opt, size <= limit)
        if last.fits:
            return last
    assert last is not None
    return last


def content_hash(payload: dict) -> str:
    """Hash of everything except the run timestamp, to detect real changes."""
    body = {k: v for k, v in payload.items() if k != "ts"}
    return hashlib.sha256(dumps(body).encode()).hexdigest()[:16]
