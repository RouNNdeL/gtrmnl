# TRMNL plugin markup

A private TRMNL plugin with the **webhook** strategy. The collector pushes
merge variables (docs/payload.md); this markup draws them on a TRMNL OG
(og_plus, 800x480, 4 greys).

```
src/shared.liquid           templates, computed values, styles (prepended to every layout)
src/full.liquid             route | name, 6 stats, HR chart, splits | 7 tiles (week + wellness)
src/half_vertical.liquid    route, name, 6 stats, HR chart, 4 tiles
src/half_horizontal.liquid  route | name, 3 stats, HR strip | week totals + 3 tiles
src/quadrant.liquid         name, 3 stats, 4 readiness tiles
fixtures/*.json             merge variables per state, plus "__now" to pin the clock
tools/                      fixture generator, local renderer, screenshot gate, contact sheets
```

Custom field read by the markup: `stale_hours` (default 4). Add it to the
plugin's form if you want to change it.

The route is drawn from pre-projected SVG path data (option (a) in
docs/design.md section 8): no JavaScript, no base map, identical everywhere.

## Render locally

```sh
git clone --depth 1 --branch v3.4.4 https://github.com/usetrmnl/trmnl-framework ../trmnl-framework
pip install python-liquid pillow
PYTHONPATH=src python trmnl/tools/make_fixtures.py            # regenerate fixtures
python trmnl/tools/render.py --framework ../trmnl-framework/public --out out
node trmnl/tools/shot.mjs out                                 # screenshots + gate, needs playwright
python trmnl/tools/sheet.py out                               # out/sheets/<layout>.png
```

`render.py --extra DIR` adds fixtures from another directory, for example
payloads built from your own activities that should not be committed.

**Renderer caveat.** `render.py` uses python-liquid, not TRMNL's Ruby
renderer. The markup sticks to standard Liquid plus TRMNL's `{% template %}`
tag (emulated by moving templates into partials). Before uploading, confirm
with TRMNL's own renderer: `tools/tl_render.rb` with the `trmnl-liquid` gem
(see .claude/skills/trmnl-plugin-dev/SKILL.md), or the connector's server
preview.

`shot.mjs` fails a page when: the framework runtime does not finish, a view
overflows or an element leaves its view, text is truncated or spills out of a
tile, the page text contains a Liquid error, `{{`, `{%`, `undefined` or `NaN`,
or an icon has no path data.

## Deploy

1. Create a private plugin, strategy **Webhook** (TRMNL web UI, or the TRMNL
   connector: `createPluginSetting`, then `updatePluginSettingFields`).
2. Paste `shared.liquid` into the shared markup and each layout into its tab.
3. Put the plugin's webhook URL into the collector's `TRMNL_WEBHOOK_URL_FILE`.
4. Preview on og_plus, then choose where it goes in the playlist.

Icons are from Material Design Icons (Apache 2.0).
