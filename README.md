# gtrmnl

Garmin Connect on a [TRMNL](https://trmnl.com) e-ink display, in the spirit of
STRMNL (which does the same for Strava): latest activity with route, HR chart
and splits; this week's run and ride totals against last week; body battery,
sleep, HRV, readiness and recovery; training status and VO2 max.

```
Garmin Connect ──(python-garminconnect)──> gtrmnl collector ──(HTTPS POST)──> TRMNL webhook ──> markup ──> device
                                           Docker on TrueNAS, no open ports
```

- `src/gtrmnl/`: the collector (Python 3.12+).
- `trmnl/`: the plugin markup, fixtures and render tools ([trmnl/README.md](trmnl/README.md)).
- `docs/design.md`: the design brief. `docs/payload.md`: the payload schema.

## Collector

| Command | What it does |
|---|---|
| `gtrmnl login` | Interactive: email, password (getpass, never stored), MFA code. Writes tokens to `/data/tokens` (0700/0600). |
| `gtrmnl run` | Service loop. Without tokens it logs "run `gtrmnl login`" and waits. After an auth failure it waits for a new login. |
| `gtrmnl once [--dry-run] [--force]` | One cycle. `--dry-run` prints the payload and its size and pushes nothing. |
| `gtrmnl fixtures [--out DIR]` | Saves sanitized raw responses and payloads (ids removed, tracks moved to a fixed origin). Review before committing. |
| `gtrmnl health` | Docker healthcheck: fails after an auth failure or when the last success is older than 2 intervals + 5 min. |

Each cycle makes about 7 Garmin calls, fetching an activity's details only once
per new activity (cached in `/data/cache`). It pushes when the content changes,
plus a heartbeat every `HEARTBEAT_HOURS`. On a 422 it learns the size limit from
TRMNL's message, stores it, refits and retries once. On a 429 it pauses pushes
for at least 15 min. If Garmin auth dies it sends `err: "auth"` once with
`deep_merge`, so the screen keeps its data and shows a banner.

### Configuration

| Variable | Default | |
|---|---|---|
| `TRMNL_WEBHOOK_URL_FILE` | | File holding the webhook URL (preferred) |
| `TRMNL_WEBHOOK_URL` | | Or the URL itself. It is a bearer credential and is never logged. |
| `INTERVAL_MINUTES` | 15 | Minimum 5 |
| `PAYLOAD_LIMIT` | 9800 | Bytes. Lowered automatically after a 422. |
| `HEARTBEAT_HOURS` | 3 | Push at least this often so the markup can spot stale data |
| `TZ` | Europe/Warsaw | Weeks start on Monday. Units are metric. |
| `RECENT_COUNT` | 5 | Recent activities in the payload |
| `LATEST_SKIP_COMMUTES` | false | Skip commutes when choosing the "latest activity" |
| `TOTALS_INCLUDE_COMMUTES` | true | Count commutes in the week totals |
| `DATA_DIR` | /data | Tokens, state, cache |
| `LOG_LEVEL` | INFO | |

### TrueNAS custom app

1. Create a dataset, e.g. `/mnt/tank/apps/gtrmnl`, owned by the apps user
   (UID 568 by default; check yours with `id apps`).
2. Write the webhook URL into `webhook_url` in that dataset, mode 0600.
3. Install via YAML with [compose.example.yaml](compose.example.yaml): image
   `ghcr.io/rounndel/gtrmnl:<tag>`, read-only root, tmpfs `/tmp`, all
   capabilities dropped, `no-new-privileges`, no ports.
4. Open the container shell and run `gtrmnl login` once.
5. `gtrmnl once --dry-run` shows what will be sent.

The image runs as a non-root user (UID 568, override with `user:`), contains
only hash-pinned wheels from `requirements.txt` plus this package, and needs
no capabilities.

## Development

```sh
python -m venv .venv && . .venv/bin/activate
pip install --require-hashes -r requirements.txt -r requirements-dev.txt
PYTHONPATH=src pytest && ruff check src tests trmnl/tools
```

`requirements*.txt` are generated with `pip-compile --generate-hashes` from
`requirements*.in`. Dependabot keeps them, the base image digest and the
Actions current.

CI (`.github/workflows/ci.yml`) runs ruff, pytest and a check that
`trmnl/fixtures` match the generator. `image.yml` builds amd64 + arm64 on every
PR without pushing, and on `main` and `v*` tags pushes to
`ghcr.io/rounndel/gtrmnl` (`latest`, semver, `sha-<commit>`) with an SBOM and
provenance attestation. **After the first push, make the GHCR package public**
(package settings), or TrueNAS cannot pull it anonymously.

## Status

- Response shapes are mapped from python-garminconnect 0.3.20 and its typed
  models, and tested against synthetic responses (`gtrmnl/synth.py`). They
  have not been checked against a real account yet: run `gtrmnl fixtures`
  and compare.
- Commute detection (`eventType.typeKey == "transportation"`) is unverified.
- Not implemented (extras from docs/design.md section 5, not chosen yet):
  elevation profile, route thumbnails, 12-week trend, body battery curve,
  sleep stages.
