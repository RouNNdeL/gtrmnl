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
2. Paste `shared.liquid` into the shared markup and each layout into its tab.
3. Put the plugin's webhook URL into the collector's `TRMNL_WEBHOOK_URL_FILE`.
4. Preview on og_plus, then choose where it goes in the playlist.

Icons are from Material Design Icons (Apache 2.0).
