import math
import re

from gtrmnl import encode, synth


def parse_path(d):
    m = re.match(r"M(-?\d+) (-?\d+)l(.*)$", d)
    x, y = int(m.group(1)), int(m.group(2))
    nums = [int(v) for v in re.findall(r"-?\d+", m.group(3))]
    pts = [(x, y)]
    for dx, dy in zip(nums[::2], nums[1::2], strict=True):
        x, y = x + dx, y + dy
        pts.append((x, y))
    return pts


def seg_dist(p, a, b):
    ax, ay = a
    bx, by = b
    px, py = p
    L2 = (bx - ax) ** 2 + (by - ay) ** 2
    if L2 == 0:
        return math.hypot(px - ax, py - ay)
    t = max(0, min(1, ((px - ax) * (bx - ax) + (py - ay) * (by - ay)) / L2))
    return math.hypot(px - ax - t * (bx - ax), py - ay - t * (by - ay))


def test_rdp_keeps_endpoints_and_corners():
    pts = [(0, 0), (1, 0.01), (2, 0), (2, 1), (2, 2)]
    assert encode.rdp(pts, 0.1) == [0, 2, 4]
    assert encode.rdp(pts[:2], 1) == [0, 1]


def test_route_svg_stays_close_to_original():
    lat, lon = synth.track(7, 33000)
    path, (w, h, ex, ey) = encode.route_svg(lat, lon, 1.0)
    pts = parse_path(path)
    assert max(w, h) == encode.MAP_PX
    assert all(-1 <= x <= w + 1 and -1 <= y <= h + 1 for x, y in pts)
    assert pts[-1] == (ex, ey)
    # every original point (in px) is within tolerance + rounding of the polyline
    proj = encode.project(lat, lon)
    span = max(max(p[0] for p in proj), max(p[1] for p in proj))
    s = encode.MAP_PX / span
    hpx = max(p[1] for p in proj) * s
    worst = 0
    for i in range(0, len(proj), 7):
        p = (proj[i][0] * s, hpx - proj[i][1] * s)
        worst = max(worst, min(seg_dist(p, a, b) for a, b in zip(pts, pts[1:])))
    assert worst < 1.0 + 1.5


def test_route_coarser_is_smaller():
    lat, lon = synth.track(3, 80000)
    sizes = [len(encode.route_svg(lat, lon, t)[0]) for t in (0.5, 1, 2)]
    assert sizes[0] > sizes[1] > sizes[2]
    assert sizes[0] < 4000


def test_route_degenerate():
    assert encode.route_svg([52.0], [21.0], 1) is None
    assert encode.route_svg([52.0, 52.0], [21.0, 21.0], 1) is None


def test_path_number_separators():
    path, _ = encode.route_svg([0.0, 0.001, 0.0005], [0.0, 0.0, 0.002], 0.5)
    assert " -" not in path and "--" not in path


def test_resample_and_b64_roundtrip():
    t = list(range(0, 1000))
    v = [100 + (i % 200) / 4 for i in t]
    out = encode.resample(t, v, 120)
    assert len(out) == 120
    s = encode.series_b64(out)
    lo, hi, levels = encode.decode_series_b64(s)
    assert len(levels) == 120 and min(levels) == 0 and max(levels) == 63
    assert len(s) <= 128
    for lev, x in zip(levels, out):
        assert abs(lo + lev / 63 * (hi - lo) - x) <= (hi - lo) / 63 + 1


def test_resample_interpolates_gaps():
    out = encode.resample([0, 1, 9, 10], [100, 100, 200, 200], 10)
    assert out[0] == 100 and out[-1] == 200
    assert 100 < out[5] < 200


def test_compact_number():
    assert encode.compact_number(12.0) == "12"
    assert encode.compact_number(12.34) == "12.3"
    assert encode.compact_number(0.0) == "0"
