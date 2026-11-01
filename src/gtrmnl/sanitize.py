"""Scrub raw Garmin responses before they are saved as fixtures.

Fixtures may end up in a public repository, so this removes account and
device identifiers and moves every coordinate so the start of each track
lands on a fixed point (shapes are kept; home location is not). Activity
names are replaced by the sport, because Garmin names activities after the
place ("Nakatsugawa City Hiking"). Review the output before committing anyway.
"""

from __future__ import annotations

import re
from typing import Any

DROP_KEYS = {
    "ownerId",
    "ownerDisplayName",
    "ownerFullName",
    "ownerProfileImageUrlSmall",
    "ownerProfileImageUrlMedium",
    "ownerProfileImageUrlLarge",
    "userProfileId",
    "userProfilePK",
    "userProfilePk",
    "userPk",
    "userId",
    "displayName",
    "fullName",
    "deviceId",
    "deviceName",
    "unitId",
    "serialNumber",
    "uuid",
    "email",
    "profileImageUrlSmall",
    "profileImageUrlMedium",
    "profileImageUrlLarge",
    "locationName",
    "userRoles",
    "activityUUID",
    "manufacturer",
    "productDisplayName",
    "primaryDeviceId",
    "deviceIdList",
    "connectIQAppIds",
}
# Any key ending in lat/latitude or lon/lng/longitude (any case) is a
# coordinate: lat, startLatitude, minLat, maxLon, ...
_LAT_RE = re.compile(r"(?i)(lat|latitude)$")
_LON_RE = re.compile(r"(?i)(lon|lng|longitude)$")


def _is_lat(k: str) -> bool:
    return bool(_LAT_RE.search(k))


def _is_lon(k: str) -> bool:
    return bool(_LON_RE.search(k))


def _partner(lat_key: str) -> list[str]:
    """Candidate longitude keys for a latitude key."""
    out = []
    for a, b in (
        ("Latitude", "Longitude"),
        ("latitude", "longitude"),
        ("Lat", "Lon"),
        ("lat", "lon"),
        ("Lat", "Lng"),
        ("lat", "lng"),
    ):
        if lat_key.endswith(a):
            out.append(lat_key[: -len(a)] + b)
    return out


ORIGIN = (52.0, 21.0)  # where every sanitized track starts
_NUMERIC_KEY = re.compile(r"^\d{4,}$")


class Sanitizer:
    def __init__(self, origin: tuple[float, float] = ORIGIN):
        self.origin = origin
        self.ids: dict[Any, int] = {}
        self.offset: tuple[float, float] | None = None

    def fake_id(self, real: Any) -> int:
        if real not in self.ids:
            self.ids[real] = 1000 + len(self.ids)
        return self.ids[real]

    def _shift(self, lat: float, lon: float) -> tuple[float, float]:
        if self.offset is None:
            self.offset = (self.origin[0] - lat, self.origin[1] - lon)
        return round(lat + self.offset[0], 6), round(lon + self.offset[1], 6)

    def scrub(self, obj: Any, new_track: bool = True) -> Any:
        """Scrub one response. Each call starts a new coordinate offset."""
        if new_track:
            self.offset = None
        if isinstance(obj, list):
            return [self.scrub(x, False) for x in obj]
        if not isinstance(obj, dict):
            return obj
        if "metricDescriptors" in obj and "activityDetailMetrics" in obj:
            obj = self._scrub_metrics(obj)
        out: dict[str, Any] = {}
        for k, v in obj.items():
            if k in DROP_KEYS:
                continue
            key = f"device{self.fake_id(k)}" if _NUMERIC_KEY.match(str(k)) else k
            if k == "activityId" and v is not None:
                out[key] = self.fake_id(v)
            elif k == "activityName" and isinstance(v, str):
                tk = (obj.get("activityType") or {}).get("typeKey") or "activity"
                out[key] = tk.replace("_", " ").capitalize()
            elif isinstance(k, str) and (_is_lat(k) or _is_lon(k)):
                if not isinstance(v, (int, float)) or isinstance(v, bool):
                    out[key] = self.scrub(v, False)  # e.g. a nested {lat, lon} object
                # numbers are handled below as pairs
            else:
                out[key] = self.scrub(v, False)
        # Pair-wise shift. A coordinate without a partner is dropped, never kept.
        for k, v in obj.items():
            if not (isinstance(k, str) and _is_lat(k)) or not isinstance(v, (int, float)):
                continue
            for o in _partner(k):
                if isinstance(obj.get(o), (int, float)):
                    out[k], out[o] = self._shift(v, obj[o])
                    break
        return out

    def _scrub_metrics(self, details: dict) -> dict:
        idx = {d.get("key"): d.get("metricsIndex") for d in details.get("metricDescriptors") or []}
        ia, io = idx.get("directLatitude"), idx.get("directLongitude")
        if not isinstance(ia, int) or not isinstance(io, int):
            return details
        rows = []
        for s in details.get("activityDetailMetrics") or []:
            m = list(s.get("metrics") or [])
            if (
                max(ia, io) < len(m)
                and isinstance(m[ia], (int, float))
                and isinstance(m[io], (int, float))
            ):
                m[ia], m[io] = self._shift(m[ia], m[io])
            else:
                # Never keep half a coordinate unshifted.
                for i in (ia, io):
                    if i < len(m):
                        m[i] = None
            rows.append({**s, "metrics": m})
        return {**details, "activityDetailMetrics": rows}
