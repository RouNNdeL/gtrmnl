# TRMNL plugin markup

A private TRMNL plugin with the **webhook** strategy. The collector pushes
merge variables (docs/payload.md); this markup draws them on a TRMNL OG
(og_plus, 800x480, 4 greys).

```
settings.yml                the settings form (Screen, Route map, stale_hours)
src/shared.liquid           templates, icons, computed values, styles (prepended to every layout)
src/full.liquid             activity list | the chosen screen's panel
src/half_vertical.liquid    compact panel, one column
src/half_horizontal.liquid  compact panel, one row
src/quadrant.liquid         the panel's essentials
fixtures/*.json             merge variables per state, plus "__now" to pin the clock
tools/                      fixtures, renderer, screenshot gate, contact sheets, icon generator
```

### Settings

`settings.yml` holds the plugin's settings form (TRMNL's `custom_fields`),
the same as the private plugin's `settings.yml` minus its instance id:

| Field | Values | |
|---|---|---|
| Screen (`screen`) | Latest activity, Splits, Heart rate, Week, Recovery | What the plugin shows |
| Route map (`map`) | Route line, Route on street map, Route on simple map, Off | How the latest activity's route is drawn |
| `stale_hours` | number, default 4 | Banner when the collector has not pushed for this long |

The route line is pre-projected SVG path data: no JavaScript, identical
everywhere. The two map options draw the route over OpenStreetMap tiles with
the framework's map engine (`TRMNLMaps`, MapLibre from TRMNL's mirror, tiles
from maps.trmnl.com, no key), from the encoded polyline `a.p`. If a payload
had to drop `a.p` to fit the size limit, the markup falls back to the line.

### Screens

Every full-size screen keeps the activity list on the left (framework
`item`s with sport icons) and puts the chosen panel on the right:

- **Latest activity:** route plus 6 stat tiles (8 without a route), chosen per
  sport (pace for runs, speed for rides, ascent for hikes, calories and heart
  rate for indoor sessions).
- **Splits:** bar chart (fastest in black) and a table with the fastest and
  slowest split marked, or a summary for long activities.
- **Heart rate:** HR over moving time (timer time for hikes) and time in zones.
- **Week:** a Monday-to-Sunday calendar of last week and this week with the
  day's sport icon and km, and run/ride/hike km this week vs last week.
- **Recovery:** body battery, sleep, HRV, training readiness, recovery time,
  training status with VO2 max and load.

The half and quadrant layouts show a compact version of the same panel.
Icons are Material Design Icons, generated into `shared.liquid` by
`tools/icons.py`; do not edit that block by hand.

## Render locally

Rendering uses TRMNL's own Liquid, the `trmnl-liquid` gem, through
`tools/tl_render.rb`, so the output matches TRMNL's server: Ruby integer
division, `split` dropping trailing empty strings, TRMNL's filters and the
`{% template %}` tag. (An earlier python-liquid stand-in differed on both:
it showed paces one second off and drew an extra, invisible splits bar.)

One-time setup. The gem requires Ruby 4; on Ruby 3.3 use its source directly
and replace the one Ruby 3.4+ construct:

```sh
gem fetch trmnl-liquid -v 0.8.2
mkdir -p ext/trmnl-liquid && tar -xOf trmnl-liquid-0.8.2.gem data.tar.gz | tar -xz -C ext/trmnl-liquid
sed -i 's/group_by { it\[key\] }/group_by { _1[key] }/' ext/trmnl-liquid/lib/trmnl/liquid/filters.rb
gem install liquid redcarpet rqrcode tzinfo
git clone --depth 1 --branch v3.4.4 https://github.com/usetrmnl/trmnl-framework ../trmnl-framework
pip install pillow
```

Then:

```sh
PYTHONPATH=src python trmnl/tools/make_fixtures.py            # regenerate fixtures
python trmnl/tools/render.py --framework ../trmnl-framework/public \
    --trmnl-liquid ext/trmnl-liquid/lib --out out              # or TRMNL_LIQUID_LIB=...
node trmnl/tools/shot.mjs out                                 # screenshots + gate, needs playwright
python trmnl/tools/sheet.py out                               # out/sheets/<layout>.png
```

`render.py` renders all views in one Ruby process. `--extra DIR` adds fixtures
from another directory, for example payloads built from your own activities
that should not be committed. TRMNL's server preview remains the final check.

`shot.mjs` fails a page when: the framework runtime does not finish, a view
overflows or an element leaves its view, text is truncated or spills out of a
tile, the page text contains a Liquid error, `{{`, `{%`, `undefined` or `NaN`,
or an icon has no path data.

## Deploy

1. Create a private plugin, strategy **Webhook** (TRMNL web UI, or the TRMNL
   connector: `createPluginSetting`, then `updatePluginSettingFields`).
2. Add the `custom_fields` from `settings.yml` to the plugin's settings form
   (or replace its files with the connector's `importPluginSettingFiles`,
   keeping the plugin's own `id` line), then pick a Screen and Route map.
3. Paste `shared.liquid` into the shared markup and each layout into its tab.
4. Put the plugin's webhook URL into the collector's `TRMNL_WEBHOOK_URL_FILE`.
5. Preview on og_plus, then choose where it goes in the playlist. The plugin
   shows one screen at a time; change the Screen setting to switch. (The
   collector pushes to a single webhook, so plugin copies would not get data.)

Icons are from Material Design Icons (Apache 2.0).
