import datetime as dt

from conftest import NOW, TZ, at, scenario

from gtrmnl import normalize, synth
from gtrmnl.config import Config


def test_category():
    c = normalize.category
    assert c("running") == c("trail_running") == c("treadmill_running") == "run"
    assert c("gravel_cycling") == c("road_biking") == c("indoor_cycling") == "ride"
    assert c("mountain_biking") == c("virtual_ride") == "ride"
    assert c("hiking") == "hike" and c("walking") == "walk"
    assert c("lap_swimming") == "swim" and c(None) == "other"


def test_summary_fields():
    a = synth.activity(5, "running", at(0), 10000, "Run")
    s = normalize.summary(a)
    assert s["cat"] == "run" and s["dist"] == 10000 and s["zones"] and len(s["zones"]) == 5
    assert s["start"] == int(at(0).timestamp())
    del a["beginTimestamp"]
    assert normalize.summary(a)["start"] == int(at(0).timestamp())


def test_extract_series_uses_moving_time():
    a = synth.activity(5, "running", at(0), 10000, "Run")
    s = normalize.extract_series(synth.details(a))
    assert s["moving_time"]
    assert len(s["lat"]) > 100 and len(s["t"]) == len(s["hr"]) == len(s["d"])
    assert max(s["t"]) <= a["movingDuration"] + 1


def test_extract_series_indoor_has_no_track():
    a = synth.activity(5, "indoor_cycling", at(0), 0, "Zwift", moving_s=3600)
    s = normalize.extract_series(synth.details(a))
    assert s["lat"] == [] and len(s["hr"]) > 10


def test_extract_series_falls_back_to_metric_coordinates():
    a = synth.activity(5, "running", at(0), 5000, "Run")
    d = synth.details(a)
    del d["geoPolylineDTO"]
    assert len(normalize.extract_series(d)["lat"]) > 100


def test_splits_even_pace():
    t = [i * 5.0 for i in range(0, 601)]  # 3000 s
    d = [i * 5.0 * 3.0 for i in range(0, 601)]  # 3 m/s -> 9 km
    sp = normalize.splits(t, d, 1000)
    assert len(sp) == 9
    assert all(abs(x - 333.33) < 0.5 for x in sp)


def test_splits_partial_last():
    t = [0, 1000, 1500]
    d = [0, 3000, 3500]
    sp = normalize.splits(t, d, 1000)
    assert len(sp) == 4  # 3 full + 0.5 km partial scaled to 1 km
    assert abs(sp[-1] - 1000) < 1


def test_wellness():
    w = normalize.wellness(synth.wellness(NOW.date()))
    assert w["bb"] == 64 and w["bb_high"] == 92 and w["sleep_score"] == 81
    assert w["hrv_status"] == "BALANCED" and w["hrv"] == 58
    assert w["readiness"] == 72 and w["recovery_h"] == 31  # latest snapshot, minutes -> hours
    assert w["status"] == "PRODUCTIVE" and w["acute"] == 642
    assert w["vo2"] == 54.3 and w["vo2_ride"] == 57.1 and w["rhr"] == 48


def test_wellness_missing_everything():
    w = normalize.wellness(synth.wellness(NOW.date(), missing=True))
    assert all(v is None for v in w.values())


def test_wellness_body_battery_fallbacks():
    raw = {"body_battery": [{"bodyBatteryValuesArray": [[1, 50], [2, 47]]}]}
    assert normalize.wellness(raw)["bb"] == 47
    raw = {"body_battery": [{"bodyBatteryValuesArray": [[1, "MEASURED", 33, 2.0]]}]}
    assert normalize.wellness(raw)["bb"] == 33


def test_recovery_reached_zero():
    raw = {
        "readiness": [
            {
                "timestamp": "x",
                "score": 90,
                "recoveryTime": 600,
                "recoveryTimeChangePhrase": "REACHED_ZERO",
            }
        ]
    }
    assert normalize.wellness(raw)["recovery_h"] == 0


def test_totals_weeks():
    raw, _ = scenario("ride")
    acts = [normalize.summary(a) for a in raw["activities"]]
    t = normalize.totals(acts, NOW, TZ)
    # NOW is Sunday: days 0..6 back are this week
    assert t["run"][1] == 2 and t["ride"][1] == 3  # gravel + 2 commutes
    assert t["lrun"][1] == 1 and t["lride"][1] == 1
    assert round(t["ride"][0], 2) == round((81240 + 7400 + 7350) / 1000, 2)
    assert len(t["d7"]) == 7 and t["d7"][-1] > 80
    t2 = normalize.totals(acts, NOW, TZ, include_commutes=False)
    assert t2["ride"][1] == 1
    # NOW is Sunday evening, so last week to date is all of last week
    assert t["lrun_td"] == 9.0 and t["lride_td"] == 60.0
    early = NOW - dt.timedelta(days=4)  # Wednesday: the Thursday ride is not included
    t3 = normalize.totals(acts, early, TZ)
    assert t3["lride_td"] == 0 and t3["lride"][1] == 1


def test_week_starts_monday():
    assert normalize.week_start(dt.date(2026, 11, 1)) == dt.date(2026, 10, 26)
    assert normalize.week_start(dt.date(2026, 10, 26)) == dt.date(2026, 10, 26)


def test_latest_skips_commutes_when_asked(tmp_path):
    raw, _ = scenario("ride")
    raw["activities"].append(synth.activity(30, "cycling", at(0, 17), 7000, "Home", commute=True))
    m = normalize.build_model(raw, None, NOW, TZ, Config(data_dir=tmp_path))
    assert m["activity"]["id"] == 30
    m = normalize.build_model(
        raw, None, NOW, TZ, Config(data_dir=tmp_path, latest_skip_commutes=True)
    )
    assert m["activity"]["id"] == 11
    assert all(r["id"] != 11 for r in m["recent"])
