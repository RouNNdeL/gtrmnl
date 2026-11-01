"""Compact encoders for routes and time series.

Pure Python (no numpy): tracks are a few thousand points at most, and keeping
the image small and the dependency list short matters more than speed here.
"""

from __future__ import annotations

import itertools
import math
from collections.abc import Sequence

EARTH_R = 6371000.0
# Largest map box in any layout, in device pixels. Route coordinates are sent
# in these units, so one unit is one pixel at full size.
MAP_PX = 380
B64 = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-_"


# --- route -------------------------------------------------------------------


def project(lat: Sequence[float], lon: Sequence[float]) -> list[tuple[float, float]]:
    """lat/lon -> local metres (equirectangular around the mean latitude), y up."""
    lat0 = math.radians(sum(lat) / len(lat))
    k = math.cos(lat0)
    xs = [math.radians(o) * EARTH_R * k for o in lon]
    ys = [math.radians(a) * EARTH_R for a in lat]
    mx, my = min(xs), min(ys)
    return [(x - mx, y - my) for x, y in zip(xs, ys, strict=True)]


def rdp(pts: Sequence[tuple[float, float]], eps: float) -> list[int]:
    """Iterative Ramer-Douglas-Peucker; returns the indices of kept points."""
    n = len(pts)
    if n <= 2:
        return list(range(n))
    keep = [False] * n
    keep[0] = keep[-1] = True
    stack = [(0, n - 1)]
    while stack:
        a, b = stack.pop()
        if b <= a + 1:
            continue
        px, py = pts[a]
        qx, qy = pts[b]
        sx, sy = qx - px, qy - py
        seg = math.hypot(sx, sy)
        best, best_i = -1.0, -1
        for i in range(a + 1, b):
            x, y = pts[i]
            d = math.hypot(x - px, y - py) if seg == 0 else abs(sx * (y - py) - sy * (x - px)) / seg
            if d > best:
                best, best_i = d, i
        if best > eps:
            keep[best_i] = True
            stack.append((a, best_i))
            stack.append((best_i, b))
    return [i for i, k in enumerate(keep) if k]


def _num(v: int, first: bool) -> str:
    # In SVG path data a minus sign separates numbers, so a space is only
    # needed before non-negative values.
    s = str(v)
    return s if first or v < 0 else " " + s


def route_svg(
    lat: Sequence[float], lon: Sequence[float], tol_px: float, map_px: int = MAP_PX
) -> tuple[str, list[int]] | None:
    """Simplify a track and encode it as relative SVG path data.

    Coordinates are pre-projected into a box whose longer side is `map_px`
    units (one unit = one device pixel at the largest map size), y pointing
    down. Returns (path, [width, height, end_x, end_y]) or None when the track
    has no extent.
    """
    if len(lat) < 2:
        return None
    pts = project(lat, lon)
    w_m = max(p[0] for p in pts)
    h_m = max(p[1] for p in pts)
    span = max(w_m, h_m)
    if span < 1:  # under a metre: nothing to draw
        return None
    m_per_px = span / map_px
    keep = rdp(pts, tol_px * m_per_px)
    scale = map_px / span
    h_px = h_m * scale
    xy: list[tuple[int, int]] = []
    for i in keep:
        x = round(pts[i][0] * scale)
        y = round(h_px - pts[i][1] * scale)
        if not xy or xy[-1] != (x, y):
            xy.append((x, y))
    if len(xy) < 2:
        return None
    x0, y0 = xy[0]
    out = [f"M{x0} {y0}l"]
    first = True
    for (ax, ay), (bx, by) in itertools.pairwise(xy):
        out.append(_num(bx - ax, first))
        out.append(_num(by - ay, False))
        first = False
    ex, ey = xy[-1]
    return "".join(out), [max(1, round(w_m * scale)), max(1, round(h_px)), ex, ey]


# --- series ------------------------------------------------------------------


def resample(t: Sequence[float], v: Sequence[float], n: int) -> list[float] | None:
    """Mean of v in n equal bins of t; empty bins are linearly interpolated."""
    pairs = sorted(
        (float(a), float(b)) for a, b in zip(t, v, strict=True) if a is not None and b is not None
    )
    if len(pairs) < 2 or n < 2:
        return None
    t0, t1 = pairs[0][0], pairs[-1][0]
    if t1 <= t0:
        return None
    sums = [0.0] * n
    cnts = [0] * n
    width = (t1 - t0) / n
    for a, b in pairs:
        i = min(int((a - t0) / width), n - 1)
        sums[i] += b
        cnts[i] += 1
    vals: list[float | None] = [s / c if c else None for s, c in zip(sums, cnts, strict=True)]
    known = [i for i, x in enumerate(vals) if x is not None]
    out: list[float] = []
    for i in range(n):
        x = vals[i]
        if x is not None:
            out.append(x)
            continue
        lo = max((k for k in known if k < i), default=None)
        hi = min((k for k in known if k > i), default=None)
        if lo is None:
            out.append(vals[hi])  # type: ignore[arg-type]
        elif hi is None:
            out.append(vals[lo])  # type: ignore[arg-type]
        else:
            f = (i - lo) / (hi - lo)
            out.append(vals[lo] + f * (vals[hi] - vals[lo]))  # type: ignore[operator]
    return out


def series_b64(vals: Sequence[float]) -> str:
    """'min,max,' + one base64url char per value, 64 levels between min and max."""
    ints = [round(x) for x in vals]
    lo, hi = min(ints), max(ints)
    span = max(hi - lo, 1)
    chars = "".join(B64[round((x - lo) / span * 63)] for x in vals)
    return f"{lo},{hi},{chars}"


def decode_series_b64(s: str) -> tuple[int, int, list[int]]:
    """Inverse of series_b64, as levels 0..63 (used by tests and tools)."""
    lo, hi, chars = s.split(",", 2)
    return int(lo), int(hi), [B64.index(c) for c in chars]


def compact_number(x: float, decimals: int = 1) -> str:
    """12.0 -> '12', 12.34 -> '12.3'."""
    s = f"{x:.{decimals}f}"
    if "." in s:
        s = s.rstrip("0").rstrip(".")
    return "0" if s in {"-0", ""} else s
