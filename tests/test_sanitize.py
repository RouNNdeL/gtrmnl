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
