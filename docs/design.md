# gtrmnl design brief

The handoff brief this repository was built from (sections 1 to 9 and Appendix A, written 2026-11-01). Each fact is marked **verified** or **proposed**. See the README for what is implemented and what is still open.

## 1. Goal

Build a TRMNL plugin for Garmin Connect, modelled on STRMNL (strmnl.app), which does the same for Strava.

STRMNL's free tier shows the latest activity with a route map, sport-specific stats and icons, in all four layouts. Its PRO tier adds photos, a splits table and chart, an HR chart, rest-day icons, segment efforts, a weekly comparison and calories.

**Chosen content (verified, user's selection):**

- **Latest activity:** sport-specific stats, route, HR chart, splits.
- **Weekly totals:** run and ride this week vs last week, plus recent activities.
- **Recovery and readiness:** body battery, sleep score, HRV status, training readiness, recovery time.
- **Training status:** VO2 max, training status, acute load.

**Layouts:** full, half_vertical, half_horizontal and quadrant. The user decides playlist placement after seeing renders.

**Target device:** TRMNL OG (model `og_plus`), 800×480, 2-bit (4 greys).

## 2. Decisions so far (verified)

| Decision | Choice | Why |
|---|---|---|
| Data path | Standalone collector container pushing to a TRMNL **webhook** | See below. |
| Host | **TrueNAS**, as a Docker app | User's choice. Not an LXC. |
| Repo | **Public** `github.com/RouNNdeL/gtrmnl` | User's choice. No secrets in the repo. |
| Image | `ghcr.io/rounndel/gtrmnl`, built by GitHub Actions | TrueNAS can pull a public image without credentials. |
| Garmin library | `python-garminconnect` | The only maintained option since Garmin's March 2026 auth change broke `garth`. |

The Home Assistant Garmin Connect integration (cyberjunky, HACS) was considered and rejected:

- It keeps a route only for the **last** activity, and keeps summaries for only the **50 most recent** activities.
- It has **no laps, splits or HR time series**, only time in HR zones.
- Its data would reach TRMNL through an HA automation, which would need trimming logic inside HA.

Polling was rejected because TRMNL's servers would need an internet-exposed endpoint to poll.

## 3. Garmin access

**Verified** against the python-garminconnect repo at v0.3.20, commit `82bc110` (2026-10-31):

- **Requirements:** Python ≥ 3.12. Dependencies are `curl_cffi`, `requests` and `ua-generator`. Since 0.3.0 the library no longer uses `garth`.
- **Login:** `Garmin(email, password, return_on_mfa=True).login(tokenstore)` returns `("needs_mfa", client_state)` when MFA is on. Finish with `resume_login(client_state, code)`.
- **Tokens:** stored in `<tokenstore>/garmin_tokens.json` (file mode 0600, directory mode 0700) and refreshed automatically. A full re-login is needed only when the refresh token expires or is revoked. The library rejects symlinked token-store paths.
- **Password handling:** the library's README advises against keeping the Garmin password in a long-lived environment variable.

**Methods** (names verified; response shapes **not** yet checked against a real account):

| Need | Methods |
|---|---|
| Activity list | `get_activities(start, limit)`, `get_activities_by_date(...)` |
| One activity | `get_activity(id)` (summary with basic splits), `get_activity_details(id, maxchart=2000, maxpoly=4000)` (chart series and `geoPolylineDTO`), `get_activity_polyline(id)` (encoded `gPolyline`) |
| Splits and laps | `get_activity_splits`, `get_activity_typed_splits`, `get_activity_split_summaries`, `get_activity_laps` |
| HR zones | `get_activity_hr_in_timezones(id)` |
| Totals | `get_weekly_activity_stats(start, end, group_by_activity_type)`, `get_progress_summary_between_dates(start, end, metric, groupbyactivities)` |
| Wellness | `get_body_battery`, `get_sleep_data`, `get_hrv_data`, `get_training_readiness`, `get_morning_training_readiness`, `get_training_status`, `get_max_metrics` (VO2 max), `get_endurance_score`, `get_hill_score`, `get_race_predictions`, `get_user_summary`, `get_stats` |

**First job:** run these once against the owner's account and save sanitized responses as fixtures (`gtrmnl fixtures`, see section 7). Every field mapping depends on them.

## 4. TRMNL constraints

**Verified:**

- **Webhook limits** (TRMNL docs, fetched 2026-11-01): 5 kB per push on the free tier and **10 kB with TRMNL+**; 12 pushes an hour free and 30 with TRMNL+. An oversized push gets a **422 response that reports the measured size**.
- **Merge strategies:** `deep_merge` merges keys but replaces arrays; `stream` appends to top-level arrays, capped by `stream_limit`.
- **Polling strategy:** up to 100 kB, with an optional JavaScript transform. Not used here.
- **Render allowance:** each render spends from an hourly allowance, so push only when the data changed.

**Unresolved:** the owner reports having TRMNL+. But on 2026-10-30 another webhook plugin on this account was rejected with "Large payload received (4391 bytes), should be less than 2kb. Subscribe to TRMNL+ for higher limit."

So the collector must **not** hardcode a limit:

- Make `PAYLOAD_LIMIT` configurable.
- On a 422, parse the limit from the error message, store it in `/data/state.json`, refit the payload and retry once.

## 5. Encoding, tested on real recordings (verified)

**Test set:** seven recordings from the `python-fitparse` and `gpxpy` test suites: a 9 km run and rides of 33–114 km. The script was `budget.py`; its core is in Appendix A.

**Route**

- Full tracks encode to **2–49 kB** each as Google polylines.
- The collector simplifies each track with Ramer–Douglas–Peucker, in projected metres, at a tolerance of **0.5 px of the largest map box (380 px)**. That gives **91–386 points, 380–1,513 bytes**. At 1 px tolerance it gives 255–914 bytes.
- At device scale, every simplified route stays inside a 7 px trace of the original.
- To fit a budget, try tolerances of 0.5, 0.75, 1, 1.5 and 2 px, and keep the finest one that fits.

**HR chart**

- Resample by time into **120 bins** (one per ~3 px of a 360 px chart), averaging each bin and interpolating gaps.
- Quantise each bin to **64 levels** between that activity's minimum and maximum HR.
- Encode as `"min,max,"` followed by one base64url character per bin. That's **about 128 bytes**, against about 480 for comma-separated integers.
- Caveat: pauses currently become straight lines bridging the gap. Plot against moving time, or break the line at stops.

**Whole payload**

- Everything except the route measured **about 860 bytes** of compact JSON. That covers latest-activity stats, splits, HR zones, week totals, 7-day bars, 5 recent activities and wellness.
- A typical total is about 1.7 kB. The worst case at 0.5 px tolerance is about 2.4 kB.
- So the payload fits easily under 5 kB or 10 kB, and fits 2 kB only with adaptive tolerance.

**Extras that a 10 kB budget allows** (proposed to the user, not chosen):

- An elevation profile
- Route thumbnails for the recent activities
- A finer HR chart and a splits chart
- A 12-week distance trend
- Today's body battery curve and last night's sleep stages

## 6. Payload schema (proposed draft)

The draft that was measured:

```json
{"merge_variables": {
  "v": 1, "ts": 1793546000,
  "a": {"n": "Morning Gravel Ride", "sp": "gravel_cycling", "st": 1793520000,
        "d": 81240, "t": 11530, "mt": 10980, "hr": 142, "mx": 178, "el": 412, "kc": 1840,
        "te": [3.4, 1.2], "ld": 186, "z": [610, 3820, 4410, 1980, 160],
        "spl": "283,279,291,301,276", "r": "<route>", "h": "<hr chart>"},
  "wk": {"run": [28.4, 4, 9180], "ride": [146.2, 9, 22100], "lrun": [35.1, 4], "lride": [118.0, 8]},
  "d7": "0,12.4,45.2,0,8.1,21.0,81.2",
  "rec": [["run", 1793430000, 10020, 3060], ["ride", 1793350000, 20600, 3900]],
  "w": {"bb": 64, "bbh": 92, "sl": 81, "hrv": "BALANCED", "hv": 58, "tr": 72, "rc": 31,
        "tst": "PRODUCTIVE", "vo": 54, "vc": 57, "al": 642, "rhr": 48}
}}
```

| Key | Meaning |
|---|---|
| `v` | Schema version |
| `ts` | Collector run time (epoch seconds), for the stale check |
| `a` | Latest activity: name, sport, start (epoch), distance (m), elapsed and moving time (s), average and max HR, elevation gain (m), kcal, aerobic and anaerobic training effect, training load, seconds in HR zones 1–5, splits (s/km for runs, speed for rides), route, HR chart |
| `wk` | Totals: this week's [km, count, seconds] per sport, and last week's [km, count] |
| `d7` | km per day for the last 7 days |
| `rec` | Recent activities: [sport, start, metres, seconds] |
| `w` | Wellness: body battery now and today's high, sleep score, HRV status and last-night average, training readiness, recovery hours, training status, VO2 max (run and cycling), acute load, resting HR |

## 7. Collector design (proposed)

**Commands**

- `gtrmnl login`: interactive. Prompts for email, password (via getpass) and MFA code, then writes tokens to `/data/tokens`. The password is never stored. On TrueNAS, run it from the container's shell.
- `gtrmnl run`: the service loop. If there are no tokens yet, it logs "run `gtrmnl login`" and keeps waiting rather than crash-looping.
- `gtrmnl once --dry-run`: prints the payload and its size, and pushes nothing.
- `gtrmnl fixtures`: saves sanitized raw responses and payloads for markup development.
- `gtrmnl health`: the Docker healthcheck. Fails when the last success is older than about twice the interval, or when Garmin auth has failed.

**Modules**

- `config` (environment)
- `garmin` (fetch and normalise; fetch an activity's details only once per new activity id, cached in `/data`)
- `encode` (route, HR and series)
- `payload` (build, then fit to the budget)
- `trmnl` (POST; on 422 parse the limit and refit; on 429 back off)
- `state` (last payload hash, learned limit, last success)
- `cli`

**Fitting to the budget.** In order:

1. Coarsen the route tolerance.
2. Drop thumbnails.
3. Drop the elevation profile.
4. Reduce split detail.
5. Halve the HR chart resolution (120 to 60 bins).
6. Shorten the recent-activities list.

**Pushing**

- Push when the content hash changes.
- Also push a heartbeat every few hours, so the markup can tell stale data from unchanged data.
- A 15-minute interval means at most 4 pushes an hour, well under the rate limits.

**Configuration**

- `TRMNL_WEBHOOK_URL`, or `TRMNL_WEBHOOK_URL_FILE` to read it from a file.
- `INTERVAL_MINUTES` (default 15).
- `PAYLOAD_LIMIT` (default just under 10 kB, lowered automatically on a 422).
- `TZ=Europe/Warsaw`. Weeks start on Monday. Units are metric.
- `LOG_LEVEL`.

**Security** (expect scrutiny):

- No exposed ports.
- Runs as a non-root user with a configurable UID, so a TrueNAS dataset owned by the apps user works. Check that user's UID on the box.
- Works with a read-only root filesystem plus a tmpfs `/tmp`, all capabilities dropped and `no-new-privileges`.
- The webhook URL is effectively a bearer token: anyone holding it can overwrite the screen. Never log it.
- Pin dependencies with hashes (pip-compile) and enable Dependabot.

**CI**

- `ruff` and `pytest` (encoders, fitting to the budget, the 422 parser, against fixtures, no network) on every PR.
- A multi-arch build on PRs, without pushing.
- On `main` and on tags, push to GHCR as `latest`, the semver tag and the commit SHA, using `GITHUB_TOKEN` with `packages: write`.
- Optionally attach an SBOM and provenance.
- **After the first push, check the GHCR package's visibility.** If it isn't public, TrueNAS can't pull it anonymously.
- If building arm64, confirm `curl_cffi` has wheels for it.

**TrueNAS custom app**

- Image `ghcr.io/rounndel/gtrmnl` (pin a tag).
- A host-path dataset mounted at `/data`.
- Environment variables `TRMNL_WEBHOOK_URL` and `TZ`.
- Run `gtrmnl login` once from the container shell.

## 8. TRMNL plugin plan (proposed)

**Plugin**

- A private plugin using the **webhook** strategy. Create it in TRMNL's web UI, or through the TRMNL connector (`createPluginSetting`, then set the strategy with `updatePluginSettingFields`).
- The webhook URL goes into the TrueNAS app's configuration.

**Files in the repo**

- `trmnl/src/{shared,full,half_vertical,half_horizontal,quadrant}.liquid`
- `trmnl/fixtures/*.json`: generated by `gtrmnl fixtures`, plus synthetic states:
  - run, ride, hike, and an indoor activity with no GPS
  - a very long ride
  - nothing yet this week
  - wellness missing
  - stale data
  - an auth-error flag

**Route rendering (undecided; ask the user)**

- **Option (a):** the collector sends the route as SVG path data, pre-projected into a normalised viewBox. Liquid draws it with no JavaScript, it renders identically locally and on TRMNL's server, and there's no base map.
- **Option (b):** the collector sends a Google polyline, and the TRMNL framework's map engine draws it over a street base map, like STRMNL. The framework 3.3 release notes describe this: MapLibre plus `decodePolyline()`, `route()` and `fit()`, with base-map presets. Its default tiles come from OpenStreetMap's community endpoint (fine for light use only), and there is also a TRMNL-hosted `trmnl` preset. Local renders may not reach the tile servers, so verify with server previews.

**Layout sketch** (a starting point only):

- **full:** route on the left; on the right a stats grid and the HR chart; along the bottom, week totals and readiness.
- **half_vertical:** route on top, then stats and HR.
- **half_horizontal:** a small route, plus stats and week totals.
- **quadrant:** sport icon, key stats and readiness.

The plugin-dev skill's "black tile = problem" pattern came from the Home plugin. Ask whether low readiness or long recovery should be shown that way.

**Workflow:** follow Appendix B. Render locally with `trmnl-liquid`, pass the layout gate, and **show contact sheets before uploading**. Upload through the TRMNL connector if the session has it. Never type the user's TRMNL API key anywhere; if the connector isn't available, the user uploads the markup. Don't add the plugin to a playlist until the user says so.

## 9. Open questions for the owner

1. A street base map under the route (option b) or route-only (option a)?
2. Is TRMNL+ active on the account the TRMNL connector uses? (See the 2 kB rejection in section 4.)
3. Which of the extras from section 5 to include?
4. Should short bike commutes count as the "latest activity", and should they count in weekly totals?
5. Should poor readiness or recovery be highlighted (black tiles), and at what thresholds?
---

## Appendix A — reference encoders (Python, from the size test)

```python
import math
import numpy as np


def project(lat, lon):
    """lat/lon arrays -> local metres (equirectangular around the mean latitude)."""
    lat0 = np.mean(lat)
    x = np.radians(lon) * 6371000 * math.cos(math.radians(lat0))
    y = np.radians(lat) * 6371000
    return x - x.min(), y - y.min()


def rdp(xy, eps):
    """Iterative Ramer-Douglas-Peucker on an (n, 2) array; returns the kept indices."""
    keep = np.zeros(len(xy), bool)
    keep[0] = keep[-1] = True
    stack = [(0, len(xy) - 1)]
    while stack:
        a, b = stack.pop()
        if b <= a + 1:
            continue
        p, q = xy[a], xy[b]
        seg = q - p
        L = np.hypot(*seg)
        pts = xy[a + 1:b]
        if L == 0:
            d = np.hypot(*(pts - p).T)
        else:
            d = np.abs(seg[0] * (pts[:, 1] - p[1]) - seg[1] * (pts[:, 0] - p[0])) / L
        i = int(np.argmax(d))
        if d[i] > eps:
            m = a + 1 + i
            keep[m] = True
            stack += [(a, m), (m, b)]
    return np.flatnonzero(keep)


def enc_signed(v):
    v = ~(v << 1) if v < 0 else (v << 1)
    out = ""
    while v >= 0x20:
        out += chr((0x20 | (v & 0x1F)) + 63)
        v >>= 5
    return out + chr(v + 63)


def polyline(lat, lon, prec=5):
    """Google encoded polyline."""
    f, out, pa, po = 10 ** prec, "", 0, 0
    for a, o in zip(lat, lon):
        A, O = int(round(a * f)), int(round(o * f))
        out += enc_signed(A - pa) + enc_signed(O - po)
        pa, po = A, O
    return out


def fit_route(lat, lon, map_px=380, budget=1150, tolerances=(0.5, 0.75, 1.0, 1.5, 2.0)):
    """Finest tolerance (in map pixels) whose encoded route fits the byte budget."""
    x, y = project(lat, lon)
    m_per_px = max(x.max(), y.max()) / map_px
    xy = np.c_[x, y]
    for tol in tolerances:
        k = rdp(xy, tol * m_per_px)
        enc = polyline(lat[k], lon[k])
        if len(enc) <= budget:
            break
    return enc, tol


def hr_series(t, hr, n=120):
    """Mean HR in n equal time bins; empty bins (pauses) interpolated."""
    t, h = np.asarray(t, float), np.asarray(hr, float)
    edges = np.linspace(t[0], t[-1], n + 1)
    idx = np.clip(np.searchsorted(edges, t, side="right") - 1, 0, n - 1)
    vals = np.array([h[idx == i].mean() if (idx == i).any() else np.nan for i in range(n)])
    good = ~np.isnan(vals)
    vals = np.interp(np.arange(n), np.flatnonzero(good), vals[good])
    return np.round(vals).astype(int)


B64 = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-_"


def hr_b64(v):
    """'min,max,' + one char per bin, 64 levels between min and max."""
    lo, hi = int(v.min()), int(v.max())
    span = max(hi - lo, 1)
    q = np.round((v - lo) / span * 63).astype(int)
    return f"{lo},{hi}," + "".join(B64[i] for i in q)
```

One idea, not yet tested: Liquid could decode a base64url character `c` without JavaScript as `B64 | split: c | first | size`, the length of the alphabet before `c`. Check it with `trmnl-liquid` before relying on it.

## Sources

- STRMNL: https://strmnl.app/
- python-garminconnect: https://github.com/cyberjunky/python-garminconnect
- garth deprecation notice: https://github.com/matin/garth/discussions/222
- Home Assistant Garmin Connect integration: https://github.com/cyberjunky/home-assistant-garmin_connect and https://github.com/cyberjunky/ha-garmin
- TRMNL webhook docs: https://docs.trmnl.com/go/private-plugins/webhooks
- TRMNL framework releases (maps in 3.3): https://github.com/usetrmnl/trmnl-framework/releases
- Test recordings: https://github.com/dtcooper/python-fitparse (tests/files) and https://github.com/tkrajina/gpxpy (test_files)
