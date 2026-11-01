"""POST merge variables to a TRMNL webhook.

The webhook URL is a credential. It is never logged; errors mention only the
HTTP status and TRMNL's message.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass

import requests

log = logging.getLogger(__name__)

TIMEOUT = 30
_LIMIT_RE = re.compile(r"less\s+than\s+(\d+(?:\.\d+)?)\s*(kb|kib|bytes|b)\b", re.I)
_SIZE_RE = re.compile(r"\((\d+)\s*bytes\)", re.I)


@dataclass
class PushResult:
    ok: bool
    status: int
    message: str = ""
    limit: int | None = None  # parsed from a 422
    retry_after: float | None = None  # from a 429


def parse_limit(message: str) -> int | None:
    """Byte limit from TRMNL's 422 text.

    Example: "Large payload received (4391 bytes), should be less than 2kb.
    Subscribe to TRMNL+ for higher limit." -> 2000. kB is read as 1000 bytes,
    the conservative reading.
    """
    m = _LIMIT_RE.search(message or "")
    if not m:
        return None
    val, unit = float(m.group(1)), m.group(2).lower()
    if unit in ("kb", "kib"):
        val *= 1000
    return int(val) if val >= 100 else None


def parse_size(message: str) -> int | None:
    m = _SIZE_RE.search(message or "")
    return int(m.group(1)) if m else None


def _message(resp: requests.Response) -> str:
    try:
        data = resp.json()
    except ValueError:
        return resp.text[:500]
    if isinstance(data, dict):
        for k in ("error", "message", "errors"):
            if data.get(k):
                return str(data[k])[:500]
    return str(data)[:500]


def push(url: str, body: bytes, session: requests.Session | None = None) -> PushResult:
    s = session or requests
    try:
        resp = s.post(
            url,
            data=body,
            headers={"Content-Type": "application/json", "User-Agent": "gtrmnl"},
            timeout=TIMEOUT,
        )
    except requests.RequestException as e:
        # str(e) can contain the URL; report the exception type only.
        return PushResult(False, 0, f"network error: {type(e).__name__}")
    if 200 <= resp.status_code < 300:
        return PushResult(True, resp.status_code)
    msg = _message(resp)
    res = PushResult(False, resp.status_code, msg)
    if resp.status_code == 422:
        res.limit = parse_limit(msg)
    elif resp.status_code == 429:
        ra = resp.headers.get("Retry-After")
        try:
            res.retry_after = float(ra) if ra else None
        except ValueError:
            res.retry_after = None
    return res
