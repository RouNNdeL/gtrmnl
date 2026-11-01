"""Turn raw Garmin Connect responses into a plain model.

Everything here is a pure function of the raw responses, so it can be tested
against saved fixtures without a network. Response shapes follow
python-garminconnect 0.3.x; every lookup is defensive because Garmin omits
fields freely (no HR strap, indoor activity, no sleep recorded, ...).
"""

from __future__ import annotations

import datetime as dt
import itertools
import math
from collections.abc import Iterable
from typing import Any
from zoneinfo import ZoneInfo

RUN, RIDE, HIKE, WALK, SWIM, STRENGTH, SKI, OTHER = (
    "run",
    "ride",
    "hike",
    "walk",
    "swim",
    "strength",
    "ski",
    "other",
)


def g(obj: Any, *path: Any, default: Any = None) -> Any:
    """Nested get that tolerates None, missing keys and short lists."""
    cur = obj
    for key in path:
        if cur is None:
            return default
        if isinstance(key, int):
            if not isinstance(cur, list) or not -len(cur) <= key < len(cur):
                return default
            cur = cur[key]
        else:
            if not isinstance(cur, dict):
                return default
            cur = cur.get(key)
    return default if cur is None else cur


def num(x: Any) -> float | None:
    if isinstance(x, bool) or x is None:
        return None
    try:
        v = float(x)
    except (TypeError, ValueError):
        return None
    return v if math.isfinite(v) else None


def category(type_key: str | None) -> str:
    k = (type_key or "").lower()
    if "run" in k:
        return RUN
    if any(s in k for s in ("cycling", "biking", "ride", "bmx", "cyclocross", "e_bike")):
        return RIDE
    if "hik" in k or "mountaineering" in k:
        return HIKE
    if "walk" in k:
        return WALK
    if "swim" in k:
        return SWIM
    if "strength" in k:
        return STRENGTH
    if "ski" in k or "snowboard" in k:
        return SKI
    return OTHER


def is_commute(act: dict) -> bool:
    # Unverified against a real account: Garmin marks commutes with the
    # "transportation" event type. Confirm with `gtrmnl fixtures`.
    ev = (g(act, "eventType", "typeKey") or "").lower()
    tk = (g(act, "activityType", "typeKey") or "").lower()
    return ev in {"transportation", "commute"} or "commut" in tk


def start_epoch(act: dict) -> int | None:
    ms = num(act.get("beginTimestamp"))
    if ms:
        return int(ms // 1000)
    gmt = act.get("startTimeGMT")
    if isinstance(gmt, str):
        try:
            t = dt.datetime.strptime(gmt[:19], "%Y-%m-%d %H:%M:%S")
        except ValueError:
            return None
        return int(t.replace(tzinfo=dt.UTC).timestamp())
    return None


def _parse_ts(v: Any) -> dt.datetime | None:
    if not isinstance(v, str):
        return None
    try:
        return dt.datetime.strptime(v[:19].replace("T", " "), "%Y-%m-%d %H:%M:%S")
    except ValueError:
        return None


def local_offset(act: dict) -> int | None:
    """UTC offset where the activity happened: startTimeLocal - startTimeGMT.

    Rounded to 15 minutes; None when either time is missing or the result is
    not a real offset.
    """
    loc, gmt = _parse_ts(act.get("startTimeLocal")), _parse_ts(act.get("startTimeGMT"))
    if loc is None or gmt is None:
        return None
    off = round((loc - gmt).total_seconds() / 900) * 900
    return off if abs(off) <= 14 * 3600 else None


# Garmin's moving-time threshold counts slow climbing as stopped (a 2.5 h hike
# reports 50 min moving), so on foot off-road the timer time is used instead.
TIMER_SPORTS = ("hike", "walk")


def uses_timer(cat: str) -> bool:
    return cat in TIMER_SPORTS


def summary(act: dict) -> dict:
    """Fields of one activity list entry that the payload needs."""
    tk = g(act, "activityType", "typeKey") or "other"
    cat = category(tk)
    timer = num(act.get("duration"))
    moving = num(act.get("movingDuration"))
    if uses_timer(cat):
        moving = timer or moving
    zones = [num(act.get(f"hrTimeInZone_{i}")) for i in range(1, 6)]
    return {
        "id": act.get("activityId"),
        "name": (act.get("activityName") or "").strip(),
        "type": tk,
        "cat": cat,
        "commute": is_commute(act),
        "start": start_epoch(act),
        "offset": local_offset(act),
        "dist": num(act.get("distance")) or 0.0,
        "elapsed": num(act.get("elapsedDuration")) or num(act.get("duration")) or 0.0,
        "moving": moving or timer or 0.0,
        "avg_hr": num(act.get("averageHR")),
        "max_hr": num(act.get("maxHR")),
        "elev": num(act.get("elevationGain")),
        "kcal": num(act.get("calories")),
        "te": [num(act.get("aerobicTrainingEffect")), num(act.get("anaerobicTrainingEffect"))],
        "load": num(act.get("activityTrainingLoad")),
        "zones": zones if any(z is not None for z in zones) else None,
    }


def zones_from_timezones(raw: Any) -> list[float] | None:
    """get_activity_hr_in_timezones -> seconds in zones 1..5."""
    if not isinstance(raw, list):
        return None
    out = [0.0] * 5
    seen = False
    for z in raw:
        n = num(g(z, "zoneNumber"))
        s = num(g(z, "secsInZone"))
        if n is not None and s is not None and 1 <= n <= 5:
            out[int(n) - 1] = s
            seen = True
    return out if seen else None


# --- activity details --------------------------------------------------------


def detail_samples(details: dict) -> list[dict[str, Any]]:
    """Positional metrics -> list of {key: value} (same as the library helper)."""
    idx: dict[int, str] = {}
    for d in g(details, "metricDescriptors", default=[]):
        k, i = g(d, "key"), g(d, "metricsIndex")
        if isinstance(k, str) and isinstance(i, int) and not isinstance(i, bool) and i >= 0:
            idx[i] = k
    out = []
    for s in g(details, "activityDetailMetrics", default=[]):
        m = g(s, "metrics", default=[])
        out.append({k: m[i] for i, k in idx.items() if i < len(m)})
    return out


def _valid_ll(lat: Any, lon: Any) -> bool:
    a, o = num(lat), num(lon)
    if a is None or o is None:
        return False
    return -90 <= a <= 90 and -180 <= o <= 180 and (a, o) != (0, 0)


def extract_series(details: dict, moving: bool = True) -> dict:
    """Track and time series from get_activity_details.

    Time is moving time when the device reports it, so pauses do not draw a
    straight line across the HR chart. With moving=False (hikes, walks) it is
    timer time, because Garmin counts slow climbing as stopped.
    """
    samples = detail_samples(details)
    lat: list[float] = []
    lon: list[float] = []
    for p in g(details, "geoPolylineDTO", "polyline", default=[]):
        if _valid_ll(g(p, "lat"), g(p, "lon")):
            lat.append(float(p["lat"]))
            lon.append(float(p["lon"]))
    if len(lat) < 2:
        lat, lon = [], []
        for s in samples:
            if _valid_ll(s.get("directLatitude"), s.get("directLongitude")):
                lat.append(float(s["directLatitude"]))
                lon.append(float(s["directLongitude"]))

    time_key = None
    order = ("sumMovingDuration", "sumDuration", "sumElapsedDuration")
    if not moving:
        order = ("sumDuration", "sumElapsedDuration", "sumMovingDuration")
    for k in order:
        if any(num(s.get(k)) is not None for s in samples):
            time_key = k
            break
    t: list[float] = []
    hr: list[float | None] = []
    dist: list[float | None] = []
    t0 = None
    for s in samples:
        if time_key:
            tv = num(s.get(time_key))
        else:
            ts = num(s.get("directTimestamp"))
            if ts is not None and t0 is None:
                t0 = ts
            tv = None if ts is None or t0 is None else (ts - t0) / 1000.0
        if tv is None:
            continue
        t.append(tv)
        h = num(s.get("directHeartRate"))
        hr.append(h if h and 20 <= h <= 250 else None)
        dist.append(num(s.get("sumDistance")))
    return {
        "lat": lat,
        "lon": lon,
        "t": t,
        "hr": hr,
        "d": dist,
        "moving_time": time_key == "sumMovingDuration",
    }


def split_unit_km(cat: str, dist_m: float) -> int:
    if cat == RUN or cat in (HIKE, WALK):
        return 1 if dist_m <= 25000 else 5
    if dist_m <= 60000:
        return 5
    if dist_m <= 150000:
        return 10
    return 25


def splits(t: list[float], d: list[float | None], unit_m: float) -> list[float]:
    """Time per split (seconds) at each multiple of unit_m, interpolated.

    A trailing partial split is kept, scaled to a full unit, when it covers at
    least 30 % of one.
    """
    pts = [(tt, dd) for tt, dd in zip(t, d, strict=True) if dd is not None]
    if len(pts) < 2 or unit_m <= 0:
        return []
    out: list[float] = []
    mark = unit_m
    last_t = pts[0][0]
    for (ta, da), (tb, db) in itertools.pairwise(pts):
        while db >= mark and db > da:
            tm = ta + (tb - ta) * (mark - da) / (db - da)
            out.append(tm - last_t)
            last_t = tm
            mark += unit_m
    end_t, end_d = pts[-1]
    rest = end_d - (mark - unit_m)
    if rest >= 0.3 * unit_m and end_t > last_t:
        out.append((end_t - last_t) * unit_m / rest)
    return out


# --- wellness ----------------------------------------------------------------


def _last_bb(entries: Any) -> float | None:
    for e in reversed(entries if isinstance(entries, list) else []):
        for row in reversed(g(e, "bodyBatteryValuesArray", default=[])):
            if not isinstance(row, list):
                continue
            v = row[1] if len(row) == 2 else (row[2] if len(row) >= 3 else None)
            v = num(v)
            if v is not None:
                return v
    return None


def _latest(items: Any, key: str = "timestamp") -> dict | None:
    if isinstance(items, dict):
        return items
    if not isinstance(items, list) or not items:
        return None
    dicts = [x for x in items if isinstance(x, dict)]
    if not dicts:
        return None
    return max(dicts, key=lambda x: str(x.get(key) or x.get("timestampLocal") or ""))


def training_status_phrase(ts: Any) -> tuple[str | None, float | None]:
    latest = g(ts, "mostRecentTrainingStatus", "latestTrainingStatusData", default={})
    best = None
    if isinstance(latest, dict):
        cands = [v for v in latest.values() if isinstance(v, dict)]
        prim = [v for v in cands if v.get("primaryTrainingDevice")]
        pool = prim or cands
        if pool:
            best = max(pool, key=lambda v: num(v.get("timestamp")) or 0)
    phrase = g(best, "trainingStatusFeedbackPhrase")
    if isinstance(phrase, str):
        # "PRODUCTIVE_2" -> "PRODUCTIVE"
        head, _, tail = phrase.rpartition("_")
        phrase = head if head and tail.isdigit() else phrase
    acute = num(g(best, "acuteTrainingLoadDTO", "dailyTrainingLoadAcute"))
    return (phrase.upper() if isinstance(phrase, str) else None), acute


def vo2max(ts: Any, mm: Any) -> tuple[float | None, float | None]:
    def pick(d: Any) -> float | None:
        return num(g(d, "vo2MaxPreciseValue")) or num(g(d, "vo2MaxValue"))

    run = pick(g(ts, "mostRecentVO2Max", "generic"))
    ride = pick(g(ts, "mostRecentVO2Max", "cycling"))
    mm_entry = mm[-1] if isinstance(mm, list) and mm else mm
    run = run or pick(g(mm_entry, "generic"))
    ride = ride or pick(g(mm_entry, "cycling"))
    return run, ride


def wellness(raw: dict) -> dict:
    us = raw.get("user_summary") or {}
    bb = num(us.get("bodyBatteryMostRecentValue"))
    if bb is None:
        bb = _last_bb(raw.get("body_battery"))
    sleep = g(raw.get("sleep"), "dailySleepDTO", default={})
    hrv = g(raw.get("hrv"), "hrvSummary", default={})
    tr = _latest(raw.get("readiness"))
    rec_min = num(g(tr, "recoveryTime"))
    if g(tr, "recoveryTimeChangePhrase") == "REACHED_ZERO":
        rec_min = 0
    status, acute = training_status_phrase(raw.get("training_status"))
    vo_run, vo_ride = vo2max(raw.get("training_status"), raw.get("max_metrics"))
    return {
        "bb": bb,
        "bb_high": num(us.get("bodyBatteryHighestValue")),
        "sleep_score": num(g(sleep, "sleepScores", "overall", "value")),
        "sleep_s": num(g(sleep, "sleepTimeSeconds")),
        "hrv_status": hrv.get("status") if isinstance(hrv.get("status"), str) else None,
        "hrv": num(hrv.get("lastNightAvg")),
        "readiness": num(g(tr, "score")),
        "recovery_h": None if rec_min is None else rec_min / 60.0,
        "status": status,
        "vo2": vo_run,
        "vo2_ride": vo_ride,
        "acute": acute,
        "rhr": num(us.get("restingHeartRate")),
    }


# --- totals ------------------------------------------------------------------


def week_start(day: dt.date) -> dt.date:
    return day - dt.timedelta(days=day.weekday())  # Monday


def totals(
    acts: Iterable[dict], now: dt.datetime, tz: ZoneInfo, include_commutes: bool = True
) -> dict:
    """This week's and last week's run/ride totals, km per day for 7 days."""
    today = now.astimezone(tz).date()
    ws = week_start(today)
    lws = ws - dt.timedelta(days=7)
    out = {
        "run": [0.0, 0, 0.0],
        "ride": [0.0, 0, 0.0],
        "lrun": [0.0, 0, 0.0],
        "lride": [0.0, 0, 0.0],
        # last week up to the same weekday and time, for a fair comparison
        "lrun_td": 0.0,
        "lride_td": 0.0,
    }
    d7 = [0.0] * 7
    now_offset = now - dt.datetime.combine(ws, dt.time(), tz)
    for a in acts:
        if a["start"] is None:
            continue
        local = dt.datetime.fromtimestamp(a["start"], tz)
        day = local.date()
        idx = (today - day).days
        if 0 <= idx < 7:
            d7[6 - idx] += a["dist"] / 1000
        if a["commute"] and not include_commutes:
            continue
        if a["cat"] not in (RUN, RIDE):
            continue
        if day >= ws:
            key = a["cat"]
        elif day >= lws:
            key = "l" + a["cat"]
            if local - dt.datetime.combine(lws, dt.time(), tz) <= now_offset:
                out["l" + a["cat"] + "_td"] += a["dist"] / 1000
        else:
            continue
        out[key][0] += a["dist"] / 1000
        out[key][1] += 1
        out[key][2] += a["moving"]
    out["d7"] = d7
    return out


def pick_latest(acts: list[dict], skip_commutes: bool) -> dict | None:
    for a in acts:
        if skip_commutes and a["commute"]:
            continue
        return a
    return None


def build_model(raw: dict, series: dict | None, now: dt.datetime, tz: ZoneInfo, cfg: Any) -> dict:
    acts = sorted(
        (summary(a) for a in raw.get("activities") or [] if isinstance(a, dict)),
        key=lambda a: a["start"] or 0,
        reverse=True,
    )
    latest = pick_latest(acts, cfg.latest_skip_commutes)
    if latest is not None:
        latest = dict(latest)
        if latest["zones"] is None:
            latest["zones"] = zones_from_timezones(raw.get("hr_zones"))
        latest["series"] = series
        if latest.get("offset") is None and latest["start"] is not None:
            local = dt.datetime.fromtimestamp(latest["start"], tz)
            latest["offset"] = int(local.utcoffset().total_seconds())
    recent = [a for a in acts if latest is None or a["id"] != latest["id"]]
    return {
        "now": int(now.timestamp()),
        "activity": latest,
        "totals": totals(acts, now, tz, cfg.totals_include_commutes),
        "recent": recent[: max(cfg.recent_count, 0)],
        "wellness": wellness(raw),
    }
