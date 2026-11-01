"""Synthetic Garmin-shaped responses for tests and markup fixtures.

Shapes mirror python-garminconnect 0.3.x responses. They are NOT real data:
replace or complement them with `gtrmnl fixtures` output from a real account.
"""

from __future__ import annotations

import datetime as dt
import math
import random
from typing import Any

SPORTS = {
    # typeKey: (speed m/s, hr base, hr span)
    "running": (3.3, 140, 30),
    "gravel_cycling": (7.0, 128, 35),
    "road_biking": (8.3, 132, 35),
    "cycling": (5.0, 110, 20),
    "hiking": (1.2, 112, 25),
    "indoor_cycling": (0.0, 135, 30),
    "strength_training": (0.0, 105, 30),
}


def track(
    seed: int, dist_m: float, lat0: float = 52.23, lon0: float = 21.01, loop: bool = True
) -> tuple[list[float], list[float]]:
    """A smooth wandering path of roughly dist_m, one point per ~10 m."""
    rnd = random.Random(seed)
    n = max(50, int(dist_m / 10))
    heading = rnd.uniform(0, 2 * math.pi)
    turn = 0.0
    x = y = 0.0
    xs, ys = [0.0], [0.0]
    step = dist_m / n
    for i in range(1, n):
        # Mostly straight roads, with an occasional bend or junction.
        if rnd.random() < 0.004:
            turn = rnd.uniform(-0.25, 0.25)
        turn *= 0.85
        turn += rnd.gauss(0, 0.004)
        if loop and i > n * 0.55 and math.hypot(x, y) > 400:
            # steer back towards the start
            target = math.atan2(-y, -x)
            diff = (target - heading + math.pi) % (2 * math.pi) - math.pi
            turn += 0.04 * diff
        heading += turn
        x += step * math.cos(heading)
        y += step * math.sin(heading)
        xs.append(x)
        ys.append(y)
    k = 6371000.0
    lat = [lat0 + math.degrees(v / k) for v in ys]
    lon = [lon0 + math.degrees(u / (k * math.cos(math.radians(lat0)))) for u in xs]
    return lat, lon


def activity(
    aid: int,
    type_key: str,
    start: dt.datetime,
    dist_m: float,
    name: str,
    commute: bool = False,
    moving_s: float | None = None,
    seed: int | None = None,
    utc_offset_h: float | None = None,
) -> dict[str, Any]:
    speed, hr0, span = SPORTS.get(type_key, (2.0, 120, 20))
    rnd = random.Random(seed if seed is not None else aid)
    if moving_s is None:
        moving_s = dist_m / speed if speed else 3600.0
    elapsed = moving_s * rnd.uniform(1.02, 1.12)
    avg_hr = hr0 + span * 0.4
    zones = [moving_s * f for f in (0.05, 0.3, 0.4, 0.2, 0.05)]
    begin = int(start.timestamp() * 1000)
    out: dict[str, Any] = {
        "activityId": aid,
        "activityName": name,
        "startTimeGMT": start.astimezone(dt.UTC).strftime("%Y-%m-%d %H:%M:%S"),
        "beginTimestamp": begin,
        **(
            {
                "startTimeLocal": (
                    start.astimezone(dt.UTC) + dt.timedelta(hours=utc_offset_h)
                ).strftime("%Y-%m-%d %H:%M:%S")
            }
            if utc_offset_h is not None
            else {}
        ),
        "activityType": {"typeKey": type_key, "typeId": 1, "parentTypeId": 17},
        "eventType": {"typeKey": "transportation" if commute else "uncategorized"},
        "distance": dist_m,
        "duration": moving_s,
        "elapsedDuration": elapsed,
        "movingDuration": moving_s,
        "elevationGain": round(dist_m / 1000 * rnd.uniform(4, 12)),
        "averageSpeed": dist_m / moving_s if moving_s else 0,
        "averageHR": round(avg_hr),
        "maxHR": round(hr0 + span * 1.1),
        "calories": round(moving_s / 3600 * (650 if speed else 400)),
        "aerobicTrainingEffect": round(rnd.uniform(2.2, 4.2), 1),
        "anaerobicTrainingEffect": round(rnd.uniform(0.0, 2.0), 1),
        "activityTrainingLoad": round(moving_s / 60 * rnd.uniform(0.9, 1.6), 1),
        "hasPolyline": speed > 0,
    }
    for i, z in enumerate(zones, 1):
        out[f"hrTimeInZone_{i}"] = round(z, 1)
    return out


def details(
    act: dict,
    seed: int | None = None,
    pause_at: float | None = 0.4,
    max_chart: int = 2000,
    max_poly: int = 4000,
) -> dict[str, Any]:
    """get_activity_details-shaped response for a synthetic activity."""
    rnd = random.Random(seed if seed is not None else act["activityId"])
    tk = act["activityType"]["typeKey"]
    speed, hr0, span = SPORTS.get(tk, (2.0, 120, 20))
    moving = act["movingDuration"]
    dist = act["distance"]
    gps = speed > 0
    lat, lon = track(act["activityId"], dist) if gps else ([], [])
    n = min(max_chart, max(60, int(moving / 5)))
    keys = [
        "directTimestamp",
        "sumDuration",
        "sumMovingDuration",
        "sumElapsedDuration",
        "directHeartRate",
        "sumDistance",
        "directSpeed",
        "directElevation",
    ]
    if gps:
        keys += ["directLatitude", "directLongitude"]
    descriptors = [{"metricsIndex": i, "key": k, "unit": {}} for i, k in enumerate(keys)]
    rows = []
    begin = act["beginTimestamp"]
    pause_len = 420.0
    hr = hr0
    for i in range(n):
        f = i / (n - 1)
        mt = moving * f
        et = mt + (pause_len if pause_at is not None and f > pause_at else 0)
        # HR: warm-up ramp, effort waves, noise
        target = hr0 + span * (min(1, f * 6) * 0.6 + 0.4 * math.sin(f * 9) ** 2)
        hr += (target - hr) * 0.3 + rnd.gauss(0, 1.5)
        d = dist * (f + 0.02 * math.sin(f * 17) * f * (1 - f))
        row = [
            begin + et * 1000,
            mt,
            mt,
            et,
            round(hr),
            d,
            speed * rnd.uniform(0.8, 1.2),
            100 + 30 * math.sin(f * 5),
        ]
        if gps:
            j = min(len(lat) - 1, int(f * (len(lat) - 1)))
            row += [lat[j], lon[j]]
        rows.append({"metrics": row})
    out: dict[str, Any] = {
        "activityId": act["activityId"],
        "measurementCount": len(keys),
        "metricsCount": n,
        "metricDescriptors": descriptors,
        "activityDetailMetrics": rows,
    }
    if gps:
        step = max(1, math.ceil(len(lat) / max_poly))
        out["geoPolylineDTO"] = {
            "polyline": [
                {"lat": a, "lon": o} for a, o in zip(lat[::step], lon[::step], strict=True)
            ],
        }
    return out


def wellness(day: dt.date, missing: bool = False) -> dict[str, Any]:
    if missing:
        return {
            "user_summary": {},
            "sleep": None,
            "hrv": None,
            "readiness": [],
            "training_status": {},
        }
    iso = day.isoformat()
    return {
        "user_summary": {
            "calendarDate": iso,
            "restingHeartRate": 48,
            "bodyBatteryMostRecentValue": 64,
            "bodyBatteryHighestValue": 92,
        },
        "sleep": {
            "dailySleepDTO": {
                "calendarDate": iso,
                "sleepTimeSeconds": 26280,
                "sleepScores": {"overall": {"value": 81, "qualifierKey": "GOOD"}},
            }
        },
        "hrv": {
            "hrvSummary": {
                "calendarDate": iso,
                "status": "BALANCED",
                "lastNightAvg": 58,
                "weeklyAvg": 61,
            }
        },
        "readiness": [
            {
                "calendarDate": iso,
                "timestamp": f"{iso}T05:10:00.0",
                "score": 66,
                "level": "MODERATE",
                "recoveryTime": 1500,
            },
            {
                "calendarDate": iso,
                "timestamp": f"{iso}T06:40:00.0",
                "score": 72,
                "level": "HIGH",
                "recoveryTime": 1860,
            },
        ],
        "training_status": {
            "mostRecentVO2Max": {
                "generic": {"vo2MaxPreciseValue": 54.3, "vo2MaxValue": 54},
                "cycling": {"vo2MaxPreciseValue": 57.1, "vo2MaxValue": 57},
            },
            "mostRecentTrainingStatus": {
                "latestTrainingStatusData": {
                    "3400000001": {
                        "timestamp": 1793500000000,
                        "primaryTrainingDevice": True,
                        "trainingStatus": 7,
                        "trainingStatusFeedbackPhrase": "PRODUCTIVE_3",
                        "acuteTrainingLoadDTO": {"dailyTrainingLoadAcute": 642},
                    },
                    "3400000002": {
                        "timestamp": 1793400000000,
                        "primaryTrainingDevice": False,
                        "trainingStatusFeedbackPhrase": "MAINTAINING_1",
                    },
                }
            },
        },
    }


# --- scenarios used by tests and trmnl/fixtures --------------------------------

SCENARIOS = (
    "ride",
    "run",
    "hike",
    "indoor",
    "long",
    "commute",
    "quiet_week",
    "no_wellness",
    "stale",
    "auth_error",
    "no_activity",
    "abroad",
)


def scenario(kind: str, now: dt.datetime) -> tuple[dict, dict | None]:
    """Raw responses and the latest activity's series for one named state."""
    tz = now.tzinfo

    def at(days_ago: int, hour: int = 7) -> dt.datetime:
        d = now.date() - dt.timedelta(days=days_ago)
        return dt.datetime(d.year, d.month, d.day, hour, 0, tzinfo=tz)

    latest = {
        "ride": [activity(11, "gravel_cycling", at(0, 9), 81240, "Morning Gravel Ride")],
        "run": [activity(12, "running", at(0, 7), 10020, "Easy Run")],
        "hike": [activity(15, "hiking", at(0, 10), 14300, "Tatra ridge hike")],
        "indoor": [activity(14, "indoor_cycling", at(0, 7), 0, "Zwift", moving_s=3600)],
        "long": [activity(13, "gravel_cycling", at(0, 6), 214000, "Bikepacking day 2")],
        "commute": [activity(16, "cycling", at(0, 17), 7400, "Commute home", commute=True)],
        # 03:00 in Warsaw is 11:00 in Japan: shown as "Today 11:00".
        "abroad": [activity(17, "hiking", at(0, 3), 4800, "Ridge hike", utc_offset_h=9)],
    }.get(kind, [])
    history = [
        activity(21, "cycling", at(1, 8), 7400, "Commute", commute=True),
        activity(22, "running", at(2, 7), 12100, "Tempo"),
        activity(25, "cycling", at(3, 8), 7350, "Commute", commute=True),
        activity(26, "running", at(4, 7), 8200, "Recovery run"),
        activity(23, "running", at(8, 7), 9000, "Long run"),
        activity(24, "road_biking", at(10, 9), 60000, "Saturday ride"),
    ]
    if kind == "quiet_week":
        history = [a for a in history if a["activityId"] in (23, 24)]
    if kind == "no_activity":
        history = []
    acts = latest + history
    raw = {"activities": acts, **wellness(now.date(), missing=kind == "no_wellness")}
    series = None
    if acts:
        newest = max(acts, key=lambda a: a["beginTimestamp"])
        from .normalize import category, extract_series, uses_timer

        moving = not uses_timer(category(newest["activityType"]["typeKey"]))
        series = extract_series(details(newest), moving)
    return raw, series
