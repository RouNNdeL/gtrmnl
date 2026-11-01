"""One collection cycle: fetch, build, fit, push."""

from __future__ import annotations

import datetime as dt
import logging
import time
from dataclasses import dataclass
from typing import Any

from . import garmin, normalize, payload, trmnl
from .config import Config
from .state import State

log = logging.getLogger(__name__)
MIN_LIMIT = 1000
DEFAULT_BACKOFF_S = 15 * 60


@dataclass
class Outcome:
    pushed: bool
    reason: str
    size: int | None = None


def collect(client: Any, cfg: Config, now: dt.datetime) -> dict:
    """Fetch from Garmin and build the model."""
    today = now.astimezone(cfg.tz).date()
    raw = garmin.fetch_raw(client, today)
    cache = garmin.SeriesCache(cfg.cache_dir)
    series = garmin.latest_series(client, raw, cache, cfg.latest_skip_commutes)
    return normalize.build_model(raw, series, now, cfg.tz, cfg)


def deliver(
    model: dict,
    cfg: Config,
    st: State,
    now_ts: float,
    post=trmnl.push,
    force: bool = False,
) -> Outcome:
    """Fit, decide whether to push, push, learn from 422/429. Mutates st."""
    if cfg.webhook_url is None:
        return Outcome(False, "no TRMNL_WEBHOOK_URL configured")
    if st.backoff_until and now_ts < st.backoff_until and not force:
        return Outcome(False, "backoff")

    limit = st.effective_limit(cfg.payload_limit)
    fitted = payload.fit(model, limit, cfg.recent_count)
    h = payload.content_hash(fitted.payload)
    heartbeat_due = st.last_push is None or now_ts - st.last_push >= cfg.heartbeat_hours * 3600
    if h == st.last_hash and not heartbeat_due and not force:
        return Outcome(False, "unchanged", fitted.size)

    for attempt in (1, 2):
        res = post(cfg.webhook_url, payload.body_bytes(fitted.payload))
        if res.ok:
            st.last_hash, st.last_push, st.last_size = h, now_ts, fitted.size
            st.auth_flag_pushed = False
            st.backoff_until = None
            log.info(
                "pushed %d bytes (limit %d, route tol %s px, hr bins %d, recent %d)",
                fitted.size,
                limit,
                fitted.options.route_tol,
                fitted.options.hr_bins,
                fitted.options.recent,
            )
            return Outcome(True, "pushed", fitted.size)
        if res.status == 422 and attempt == 1:
            new = res.limit or max(MIN_LIMIT, int(min(limit, fitted.size) * 0.8))
            new = max(MIN_LIMIT, min(new, limit - 1))
            log.warning("TRMNL rejected %d bytes (%s); limit now %d", fitted.size, res.message, new)
            st.learned_limit = new
            limit = new
            fitted = payload.fit(model, limit, cfg.recent_count)
            continue
        if res.status == 429:
            wait = max(res.retry_after or 0, DEFAULT_BACKOFF_S)
            st.backoff_until = now_ts + wait
            log.warning("TRMNL rate limit (429); pausing pushes for %d s", wait)
            return Outcome(False, "429")
        log.error("push failed: HTTP %s %s", res.status, res.message)
        return Outcome(False, f"HTTP {res.status}")
    return Outcome(False, "422 after refit")


def push_auth_flag(cfg: Config, st: State, now_ts: float, post=trmnl.push) -> None:
    """Tell the screen that Garmin auth failed, once, keeping the old data.

    deep_merge keeps the last good payload and only adds the flag.
    """
    if cfg.webhook_url is None or st.auth_flag_pushed:
        return
    body = payload.dumps(
        {"merge_variables": {"err": "auth", "et": int(now_ts)}, "merge_strategy": "deep_merge"}
    ).encode()
    res = post(cfg.webhook_url, body)
    if res.ok:
        st.auth_flag_pushed = True
        st.last_hash = None  # force a full push (without the flag) once auth works
    else:
        log.warning("could not push auth flag: HTTP %s", res.status)


def run_once(
    cfg: Config, st: State, client: Any | None, force: bool = False
) -> tuple[Any, Outcome]:
    """One cycle. Returns the (possibly new) Garmin client and the outcome."""
    now = dt.datetime.now(dt.UTC)
    now_ts = now.timestamp()
    try:
        if client is None:
            client = garmin.connect(cfg.token_dir)
        model = collect(client, cfg, now)
    except garmin.AuthError as e:
        st.auth_failed = True
        st.last_error = f"auth: {e}"
        push_auth_flag(cfg, st, now_ts)
        return None, Outcome(False, "auth")
    except garmin.TransientError as e:
        st.last_error = str(e)
        log.warning("Garmin: %s", e)
        return client, Outcome(False, "garmin unavailable")
    st.auth_failed = False
    out = deliver(model, cfg, st, now_ts, force=force)
    if out.pushed or out.reason in ("unchanged", "429", "backoff"):
        st.last_success = now_ts
        st.last_error = None
    return client, out


def healthy(cfg: Config, st: State, now_ts: float | None = None) -> tuple[bool, str]:
    now_ts = now_ts or time.time()
    if st.auth_failed:
        return False, "Garmin auth failed: run `gtrmnl login`"
    if st.last_success is None:
        return False, "no successful run yet"
    age = now_ts - st.last_success
    if age > 2 * cfg.interval_minutes * 60 + 300:
        return False, f"last success {int(age // 60)} min ago"
    return True, "ok"
