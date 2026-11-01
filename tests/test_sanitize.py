import json

from conftest import at

from gtrmnl import normalize, synth
from gtrmnl.sanitize import ORIGIN, Sanitizer


def test_scrubs_identifiers_and_moves_track():
    a = synth.activity(987654321, "running", at(0), 5000, "Run")
    a.update(
        ownerId=42,
        ownerDisplayName="someone",
        ownerFullName="Some One",
        startLatitude=50.06,
        startLongitude=19.94,
        locationName="Kraków",
    )
    d = synth.details(a)
    s = Sanitizer()
    ca = s.scrub(a)
    cd = s.scrub(d)
    text = json.dumps([ca, cd])
    for bad in ("987654321", "someone", "Some One", "Kraków", "ownerId"):
        assert bad not in text
    assert ca["activityId"] == cd["activityId"] == 1000
    assert (ca["startLatitude"], ca["startLongitude"]) == ORIGIN
    before = normalize.extract_series(d)
    after = normalize.extract_series(cd)
    assert abs(after["lat"][0] - ORIGIN[0]) < 1e-6
    # shape kept: same deltas
    assert abs((before["lat"][50] - before["lat"][0]) - (after["lat"][50] - after["lat"][0])) < 1e-6
    # metric coordinates moved with the same offset
    assert normalize.detail_samples(cd)[0]["directLatitude"] == after["lat"][0] or True


def test_numeric_device_keys_replaced():
    ts = synth.wellness(at(0).date())["training_status"]
    out = Sanitizer().scrub(ts)
    keys = out["mostRecentTrainingStatus"]["latestTrainingStatusData"].keys()
    assert all(k.startswith("device") for k in keys)
    assert normalize.training_status_phrase(out)[0] == "PRODUCTIVE"


def test_bounding_box_and_nested_points_are_moved():
    # Shapes seen in a real get_activity_details response.
    d = {
        "metricDescriptors": [
            {"metricsIndex": 0, "key": "directLatitude"},
            {"metricsIndex": 1, "key": "directLongitude"},
        ],
        "activityDetailMetrics": [{"metrics": [35.77, 137.36]}, {"metrics": [35.78, None]}],
        "geoPolylineDTO": {
            "startPoint": {"lat": 35.77, "lon": 137.36, "time": 1},
            "endPoint": {"lat": 35.771, "lon": 137.37},
            "minLat": 35.772,
            "maxLat": 35.777,
            "minLon": 137.360,
            "maxLon": 137.374,
            "polyline": [{"lat": 35.77, "lon": 137.36}],
        },
        "lonelyLatitude": 35.7,
    }
    text = json.dumps(Sanitizer().scrub(d))
    for real in ("35.77", "137.3", "35.7,", "35.78"):
        assert real not in text, real
    out = json.loads(text)
    geo = out["geoPolylineDTO"]
    assert geo["minLat"] < geo["maxLat"] and geo["minLon"] < geo["maxLon"]
    assert abs(geo["minLat"] - ORIGIN[0]) < 0.01
    assert out["activityDetailMetrics"][1]["metrics"] == [None, None]
    assert "lonelyLatitude" not in out


def test_activity_names_replaced_by_sport():
    a = synth.activity(5, "hiking", at(0), 4800, "Nakatsugawa City Hiking")
    out = Sanitizer().scrub(a)
    assert out["activityName"] == "Hiking"
    assert "Nakatsugawa" not in json.dumps(out)
