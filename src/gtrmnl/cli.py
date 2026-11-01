"""Command line: login, run, once, fixtures, health."""

from __future__ import annotations

import argparse
import datetime as dt
import getpass
import json
import logging
import signal
import sys
import threading
from pathlib import Path

from . import bootstrap, garmin, normalize, payload, service
from .config import Config, ConfigError
from .sanitize import Sanitizer
from .state import State

log = logging.getLogger("gtrmnl")
# Exit status while a login waits for GARMIN_MFA_CODE (EX_TEMPFAIL).
EXIT_MFA = 75


def _setup_logging(level: str) -> None:
    logging.basicConfig(
        level=getattr(logging, level, logging.INFO),
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    # urllib3 logs request lines that would include the webhook path.
    logging.getLogger("urllib3").setLevel(logging.WARNING)


def cmd_login(cfg: Config, args: argparse.Namespace) -> int:
    email = args.email or cfg.garmin_email or input("Garmin email: ").strip()
    password = getpass.getpass("Garmin password (not stored): ")
    try:
        garmin.interactive_login(cfg.token_dir, email, password, lambda: input("MFA code: "))
    except Exception as e:  # the library raises several types; none carry secrets
        print(f"login failed: {type(e).__name__}: {e}", file=sys.stderr)
        return 1
    finally:
        del password
    st = State.load(cfg.state_file)
    st.auth_failed = False
    st.last_error = None
    st.save(cfg.state_file)
    print(f"tokens saved in {cfg.token_dir}")
    return 0


def cmd_run(cfg: Config, args: argparse.Namespace) -> int:
    stop = threading.Event()
    for sig in (signal.SIGTERM, signal.SIGINT):
        signal.signal(sig, lambda *_: stop.set())
    if cfg.webhook_url is None:
        log.warning("TRMNL_WEBHOOK_URL is not set: collecting without pushing")
    if cfg.garmin_password_source == "GARMIN_PASSWORD":  # noqa: S105 (a variable name)
        log.warning("GARMIN_PASSWORD is set in the environment; GARMIN_PASSWORD_FILE is safer")
    client = None
    memo = bootstrap.Memo()
    while not stop.is_set():
        st = State.load(cfg.state_file)
        try:
            how = bootstrap.ensure_tokens(cfg, st, memo)
        except Exception:
            log.exception("token bootstrap failed")
            how = bootstrap.MISSING
        if how == bootstrap.MFA_PENDING:
            # The code Garmin sent comes back through the environment, which
            # only changes on a new start: exit and let the user restart.
            st.save(cfg.state_file)
            log.warning("exiting until GARMIN_MFA_CODE is set")
            return EXIT_MFA
        if how in (bootstrap.SEEDED, bootstrap.PASSWORD):
            client = None
            st.save(cfg.state_file)
        if not cfg.token_file.is_file() or st.auth_failed:
            # No tokens, or dead ones: wait for `gtrmnl login` or new env
            # instead of hammering Garmin's SSO.
            st.save(cfg.state_file)
            stop.wait(60)
            continue
        try:
            client, out = service.run_once(cfg, st, client)
            log.info("cycle: %s%s", out.reason, f" ({out.size} bytes)" if out.size else "")
        except Exception:
            log.exception("cycle failed")
            st.last_error = "unexpected error"
            client = None
        st.save(cfg.state_file)
        if st.auth_failed:
            continue  # the top of the loop waits, retrying env credentials once
        stop.wait(cfg.interval_minutes * 60)
    log.info("stopping")
    return 0


def _bootstrap_once(cfg: Config, st: State) -> None:
    how = bootstrap.ensure_tokens(cfg, st, bootstrap.Memo(warned_missing=True))
    st.save(cfg.state_file)
    if how == bootstrap.MFA_PENDING:
        raise garmin.AuthError("waiting for GARMIN_MFA_CODE (see the log above)")


def cmd_once(cfg: Config, args: argparse.Namespace) -> int:
    st = State.load(cfg.state_file)
    _bootstrap_once(cfg, st)
    if args.dry_run:
        client = garmin.connect(cfg.token_dir)
        now = dt.datetime.now(dt.UTC)
        model = service.collect(client, cfg, now)
        limit = st.effective_limit(cfg.payload_limit)
        fitted = payload.fit(model, limit, cfg.recent_count)
        print(json.dumps({"merge_variables": fitted.payload}, indent=1, ensure_ascii=False))
        print(
            f"\n{fitted.size} bytes, limit {limit}, fits: {fitted.fits}, options: {fitted.options}",
            file=sys.stderr,
        )
        return 0 if fitted.fits else 1
    _, out = service.run_once(cfg, st, None, force=args.force)
    st.save(cfg.state_file)
    print(out.reason, out.size or "")
    return 0 if out.pushed or out.reason == "unchanged" else 1


def cmd_fixtures(cfg: Config, args: argparse.Namespace) -> int:
    """Save sanitized raw responses plus the payloads built from them."""
    out = Path(args.out or cfg.data_dir / "fixtures")
    out.mkdir(parents=True, exist_ok=True)
    _bootstrap_once(cfg, State.load(cfg.state_file))
    client = garmin.connect(cfg.token_dir)
    now = dt.datetime.now(dt.UTC)
    today = now.astimezone(cfg.tz).date()
    raw = garmin.fetch_raw(client, today)
    for name in ("user_summary", "sleep", "hrv", "readiness", "training_status"):
        raw.setdefault(name, None)
    raw.setdefault("body_battery", garmin._call(client.get_body_battery, today.isoformat()))
    raw.setdefault("max_metrics", garmin._call(client.get_max_metrics, today.isoformat()))

    san = Sanitizer()
    acts = sorted(
        (a for a in raw["activities"] if isinstance(a, dict)),
        key=lambda a: normalize.start_epoch(a) or 0,
        reverse=True,
    )
    per_activity = {}
    for a in acts[: args.activities]:
        aid = a.get("activityId")
        if aid is None:
            continue
        per_activity[aid] = {
            "details": garmin.fetch_details(client, aid),
            "hr_zones": garmin.fetch_hr_zones(client, aid),
            "splits": garmin._call(client.get_activity_splits, str(aid)),
        }

    clean = {k: san.scrub(v) for k, v in raw.items()}
    (out / "raw.json").write_text(json.dumps(clean, indent=1, ensure_ascii=False))
    for aid, parts in per_activity.items():
        fid = san.fake_id(aid)
        clean_parts = {k: san.scrub(v) for k, v in parts.items()}
        (out / f"activity_{fid}.json").write_text(
            json.dumps(clean_parts, indent=1, ensure_ascii=False)
        )

    # Payloads from the sanitized data, the way the markup will see them.
    for aid, parts in per_activity.items():
        fid = san.fake_id(aid)
        details = san.scrub(parts["details"]) if parts["details"] else None
        cat = next(
            (normalize.summary(a)["cat"] for a in acts if a.get("activityId") == aid), "other"
        )
        moving = not normalize.uses_timer(cat)
        series = normalize.extract_series(details, moving) if details else None
        r = dict(clean)
        r["activities"] = [x for x in clean["activities"] if x.get("activityId") == fid] + [
            x for x in clean["activities"] if x.get("activityId") != fid
        ]
        # Make this activity the newest so it becomes "latest".
        r["activities"][0] = {**r["activities"][0], "beginTimestamp": int(now.timestamp() * 1000)}
        r["hr_zones"] = san.scrub(parts["hr_zones"])
        model = normalize.build_model(r, series, now, cfg.tz, cfg)
        fitted = payload.fit(model, cfg.payload_limit, cfg.recent_count)
        (out / f"payload_{fid}.json").write_text(
            json.dumps(fitted.payload, indent=1, ensure_ascii=False)
        )
        print(f"activity {fid}: payload {fitted.size} bytes, options {fitted.options}")
    print(f"fixtures written to {out}. Review them before committing anything.")
    return 0


def cmd_health(cfg: Config, args: argparse.Namespace) -> int:
    ok, why = service.healthy(cfg, State.load(cfg.state_file))
    print(why)
    return 0 if ok else 1


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="gtrmnl", description=__doc__)
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("login", help="log in to Garmin interactively and store tokens")
    p.add_argument("--email")
    sub.add_parser("run", help="service loop")
    p = sub.add_parser("once", help="one cycle")
    p.add_argument("--dry-run", action="store_true", help="print the payload, push nothing")
    p.add_argument("--force", action="store_true", help="push even if unchanged")
    p = sub.add_parser("fixtures", help="save sanitized responses and payloads")
    p.add_argument("--out")
    p.add_argument("--activities", type=int, default=5)
    sub.add_parser("health", help="Docker healthcheck")
    args = ap.parse_args(argv)
    try:
        cfg = Config.from_env()
    except ConfigError as e:
        print(f"configuration error: {e}", file=sys.stderr)
        return 2
    _setup_logging(cfg.log_level)
    handler = {
        "login": cmd_login,
        "run": cmd_run,
        "once": cmd_once,
        "fixtures": cmd_fixtures,
        "health": cmd_health,
    }[args.cmd]
    try:
        return handler(cfg, args)
    except garmin.AuthError as e:
        print(f"Garmin auth: {e}. Run `gtrmnl login`.", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        return 130
