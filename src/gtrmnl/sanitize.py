"""Scrub raw Garmin responses before they are saved as fixtures.

Fixtures may end up in a public repository, so this removes account and
device identifiers and moves every coordinate so the start of each track
lands on a fixed point (shapes are kept; home location is not). Review the
output before committing anyway: activity names are kept as they are.
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
LAT_KEYS = {"lat", "latitude", "startLatitude", "endLatitude", "minLatitude", "maxLatitude"}
LON_KEYS = {"lon", "longitude", "startLongitude", "endLongitude", "minLongitude", "maxLongitude"}
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
        lat = next((k for k in obj if k in LAT_KEYS), None)
        lon = next((k for k in obj if k in LON_KEYS), None)
        out: dict[str, Any] = {}
        for k, v in obj.items():
            if k in DROP_KEYS:
                continue
            key = f"device{self.fake_id(k)}" if _NUMERIC_KEY.match(str(k)) else k
            if k == "activityId" and v is not None:
                out[key] = self.fake_id(v)
            elif k in LAT_KEYS or k in LON_KEYS:
                continue  # handled below as a pair
            else:
                out[key] = self.scrub(v, False)
        # Pair-wise shift; a lone coordinate is dropped.
        pairs = [
            ("lat", "lon"),
            ("latitude", "longitude"),
            ("startLatitude", "startLongitude"),
            ("endLatitude", "endLongitude"),
            ("minLatitude", "minLongitude"),
            ("maxLatitude", "maxLongitude"),
        ]
        if lat or lon:
            for a, o in pairs:
                if isinstance(obj.get(a), (int, float)) and isinstance(obj.get(o), (int, float)):
                    out[a], out[o] = self._shift(obj[a], obj[o])
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
            rows.append({**s, "metrics": m})
        return {**details, "activityDetailMetrics": rows}
