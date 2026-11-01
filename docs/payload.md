# Payload schema v1

The collector POSTs `{"merge_variables": {...}}` to the TRMNL webhook (default
strategy: replace). Keys are short because the whole body counts against the
webhook limit (2 kB free, 10 kB TRMNL+ per the TRMNL docs; see
docs/design.md section 4). Missing values are omitted, never `null`, so Liquid
sees blank. Example states: `trmnl/fixtures/*.json`.

| Key | Type | Meaning |
|---|---|---|
| `v` | int | Schema version (1) |
| `ts` | epoch s | When the payload was built. The markup flags data older than the `stale_hours` custom field (default 4; heartbeat pushes happen every 3 h). |
| `err` | string | Only `"auth"`: Garmin tokens are dead. Sent with `merge_strategy: deep_merge`, so the last good data stays on screen. |
| `et` | epoch s | When `err` was raised |
| `a` | object | Latest activity (absent when there is none) |
| `wk` | object | Week totals |
| `d7` | string | km per day for the last 7 days, oldest first, all sports: `"0,12.4,45.2,0,8.1,21,81.2"` |
| `rec` | array | Recent activities after the latest: `[category, start epoch, metres, moving s]` |
| `w` | object | Wellness |

## `a`: latest activity

| Key | Meaning |
|---|---|
| `n` | Name (max 60 chars) |
| `sp` | Garmin `activityType.typeKey`, e.g. `gravel_cycling` |
| `c` | Category: `run`, `ride`, `hike`, `walk`, `swim`, `strength`, `ski`, `other` |
| `cm` | `1` when Garmin marks it as a commute (event type `transportation`; unverified) |
| `st` | Start, epoch s |
| `o` | UTC offset at the start, seconds (so DST is right for older activities) |
| `d` | Distance, m |
| `t`, `mt` | Elapsed and moving time, s |
| `hr`, `mx` | Average and max HR |
| `el` | Elevation gain, m |
| `kc` | kcal |
| `te` | `[aerobic, anaerobic]` training effect |
| `ld` | Training load |
| `z` | Seconds in HR zones 1-5 |
| `spl` | Splits, comma separated: seconds per `su` km (run, hike, walk) or km/h x10 (ride) |
| `su` | Split length, km: 1 or 5 on foot; 5, 10 or 25 on a bike, by distance |
| `r` | Route as relative SVG path data, pre-projected: the longer side is 380 units (1 unit = 1 px of the largest map box), y down |
| `rb` | `[width, height, end_x, end_y]` of the route box |
| `h` | HR chart: `"min,max,"` + one base64url char per bin (64 levels between min and max), binned over moving time |

## `wk`

`run`, `ride`: this week (Monday start, `TZ`) `[km, count, moving s]`.
`lrun`, `lride`: last week `[km, count, km to the same weekday and time]`.
Commutes count unless `TOTALS_INCLUDE_COMMUTES=false`.

## `w`

| Key | Meaning |
|---|---|
| `bb`, `bbh` | Body battery now, today's high |
| `sl` | Sleep score |
| `ss` | Sleep time, s |
| `hrv`, `hv` | HRV status (`BALANCED`, ...), last-night average ms |
| `tr` | Training readiness score |
| `rc` | Recovery time, h |
| `tst` | Training status (`PRODUCTIVE`, `MAINTAINING`, ...) |
| `vo`, `vc` | VO2 max running, cycling |
| `al` | Acute training load |
| `rhr` | Resting HR |

## Fitting to the limit

`payload.fit()` walks a ladder and keeps the first option that fits:
route tolerance 0.5 → 0.75 → 1 → 1.5 → 2 px, at most 12 then 8 splits, HR
chart 120 → 60 bins, recent activities 5 → 0, then emergency steps (route
3-6 px, no splits, 30-bin then no HR chart, no `d7`, no route). On a 422 the
collector parses the limit from TRMNL's message ("should be less than 2kb"
→ 2000), stores it in `/data/state.json`, refits and retries once.
