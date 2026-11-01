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
| `gtrmnl run` | Service loop. Without tokens it tries the environment (below), otherwise logs "run `gtrmnl login`" and waits. After an auth failure it waits for a new login or new credentials. |
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
| `GARMIN_TOKENS_FILE` | | Optional: a `garmin_tokens.json` to seed `/data/tokens` (or `GARMIN_TOKENS` with the JSON itself) |
| `GARMIN_EMAIL` | | Optional: for an unattended password login, and the default for `gtrmnl login` |
| `GARMIN_PASSWORD_FILE` | | Optional: file with the Garmin password (or `GARMIN_PASSWORD`, which logs a warning) |

Every secret has a `_FILE` variant that wins over the plain variable. Use
the `_FILE` form so the value does not show up in `docker inspect`.

### Garmin login: three ways

Tokens live in `/data/tokens/garmin_tokens.json` and refresh on their own. A
new login is needed only when Garmin revokes them. Get the first set in one of
these ways:

1. **Interactive (default):** open the container shell and run
   `gtrmnl login`. Works with MFA. Nothing but the tokens is stored.
2. **Seed tokens:** run `gtrmnl login` anywhere (for example
   `docker run --rm -it --user "$(id -u):$(id -g)" -v "$PWD/t:/data" ghcr.io/rounndel/gtrmnl login`), then
   mount the resulting `garmin_tokens.json` and point `GARMIN_TOKENS_FILE` at
   it. The collector copies it into `/data/tokens` on start. Changing the
   seed file (new content) seeds again; the same file is not re-applied, so it
   never overwrites the refreshed copy. Works with MFA, and the password never
   reaches the deployment.
3. **Password:** set `GARMIN_EMAIL` and `GARMIN_PASSWORD_FILE`. When there
   are no working tokens the collector logs in once and saves tokens, then
   logs a reminder to remove the variables. Accounts with **MFA cannot use
   this**: the login stops with an "MFA" error instead of waiting for a code.
   A rejected password is not retried until the container restarts, so a typo
   cannot lock the account. Nothing derived from the password is written to
   disk. If the variables stay set, the collector logs in again by itself
   after Garmin revokes the tokens, at the cost of keeping the password in
   the app's configuration (python-garminconnect advises against that).

If both a seed and a password are set, the seed is used first.

### TrueNAS custom app

1. Create a dataset, e.g. `/mnt/tank/apps/gtrmnl`, owned by the apps user
   (UID 568 by default; check yours with `id apps`).
2. Write the webhook URL into `webhook_url` in that dataset, mode 0600.
3. Install via YAML with [compose.example.yaml](compose.example.yaml): image
   `ghcr.io/rounndel/gtrmnl:<tag>`, read-only root, tmpfs `/tmp`, all
   capabilities dropped, `no-new-privileges`, no ports.
4. Log in: open the container shell and run `gtrmnl login` once, or use one of
   the environment options above.
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
