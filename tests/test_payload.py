import json

from conftest import NOW, TZ, scenario

from gtrmnl import normalize, payload


def build(kind, cfg):
    raw, series = scenario(kind)
    return normalize.build_model(raw, series, NOW, TZ, cfg)


def test_full_payload_shape(model):
    p = payload.build(model)
    assert p["v"] == 1 and p["ts"] == int(NOW.timestamp())
    a = p["a"]
    for k in (
        "n",
        "sp",
        "c",
        "st",
        "d",
        "t",
        "mt",
        "hr",
        "mx",
        "el",
        "kc",
        "te",
        "ld",
        "z",
        "spl",
        "su",
        "r",
        "rb",
        "h",
    ):
        assert k in a, k
    assert a["c"] == "ride" and a["su"] == 10 and len(a["rb"]) == 4
    assert set(p["wk"]) == {"run", "ride", "lrun", "lride"}
    assert len(p["d7"].split(",")) == 7
    assert len(p["rec"]) == 5 and p["rec"][0][0] == "ride"
    assert p["w"]["tst"] == "PRODUCTIVE"


def test_typical_size(model):
    size = len(payload.body_bytes(payload.build(model)))
    assert 900 < size < 5000


def test_fit_large_limit_keeps_finest(model):
    f = payload.fit(model, 9800)
    assert f.fits and f.options.route_tol == 0.5 and f.options.hr_bins == 120


def test_fit_small_limits(model):
    for limit in (2500, 2000, 1500, 1000):
        f = payload.fit(model, limit)
        assert f.size == len(payload.body_bytes(f.payload))
        assert f.fits, (limit, f.size, f.options)


def test_fit_monotone(model):
    sizes = [
        len(payload.body_bytes(payload.build(model, o))) for o in payload.ladder(payload.Options())
    ]
    assert sizes[-1] < sizes[0]


def test_long_ride_fits_10k(cfg):
    f = payload.fit(build("long", cfg), 9800)
    assert f.fits and "r" in f.payload["a"]


def test_indoor_has_no_route(cfg):
    p = payload.build(build("indoor", cfg))
    assert "r" not in p["a"] and "h" in p["a"]


def test_run_splits_are_seconds_per_km(cfg):
    p = payload.build(build("run", cfg))
    spl = [int(x) for x in p["a"]["spl"].split(",")]
    assert p["a"]["su"] == 1 and len(spl) >= 9
    assert all(200 < s < 600 for s in spl)


def test_hash_ignores_timestamp(model):
    p = payload.build(model)
    q = dict(p, ts=p["ts"] + 900)
    assert payload.content_hash(p) == payload.content_hash(q)
    q["w"] = dict(p["w"], bb=1)
    assert payload.content_hash(p) != payload.content_hash(q)


def test_no_activity(model):
    model["activity"] = None
    model["recent"] = []
    p = payload.build(model)
    assert "a" not in p and "rec" not in p
    json.dumps(p)
