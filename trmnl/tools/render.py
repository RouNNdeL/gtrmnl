"""Render the plugin markup locally into HTML pages (one per view or mashup).

    python trmnl/tools/render.py --framework ../trmnl-framework/public --out out/

This uses python-liquid as a STAND-IN for TRMNL's Ruby renderer
(trmnl-liquid). The markup sticks to standard Liquid plus TRMNL's
`{% template %}` tag, which this script emulates by moving each template
into an in-memory partial. Server previews (TRMNL connector) or
`tl_render.rb` remain the authority; see trmnl/README.md.

The framework assets come from a checkout of usetrmnl/trmnl-framework
(`public/` holds css/<version>, js/<version> and fonts/).
"""

from __future__ import annotations

import argparse
import json
import re
import shutil
from pathlib import Path

from liquid import DictLoader, Environment

HERE = Path(__file__).resolve().parent
SRC = HERE.parent / "src"
FIXTURES = HERE.parent / "fixtures"
LAYOUTS = ("full", "half_vertical", "half_horizontal", "quadrant")
TEMPLATE_RE = re.compile(r"{%-?\s*template\s+(\w+)\s*-?%}(.*?){%\s*endtemplate\s*%}", re.S)
DEFAULT_VERSION = "3.4.4"
OFFSET = 3600  # Europe/Warsaw in winter; matches trmnl.user.utc_offset

# Mashups the owner runs, plus the 2x2 grid of quadrants.
MASHUPS = {
    "1Lx2R": ("half_vertical", "quadrant", "quadrant"),
    "2Tx1B": ("quadrant", "quadrant", "half_horizontal"),
    "1Lx1R": ("half_vertical", "half_vertical"),
    "2x2": ("quadrant", "quadrant", "quadrant", "quadrant"),
}


def split_templates(source: str) -> tuple[str, dict[str, str]]:
    templates = {m.group(1): m.group(2) for m in TEMPLATE_RE.finditer(source)}
    return TEMPLATE_RE.sub("", source), templates


def render_view(layout: str, data: dict, fields: dict | None = None) -> str:
    shared = (SRC / "shared.liquid").read_text(encoding="utf-8")
    body = (SRC / f"{layout}.liquid").read_text(encoding="utf-8")
    source, templates = split_templates(shared + "\n" + body)
    env = Environment(loader=DictLoader(templates))
    now = data.get("__now")
    ctx = {k: v for k, v in data.items() if not k.startswith("__")}
    ctx["trmnl"] = {
        "user": {"time_zone_iana": "Europe/Warsaw", "utc_offset": OFFSET, "locale": "en"},
        "device": {"width": 800, "height": 480, "model": "og_plus", "bit_depth": 2},
        "system": {"timestamp_utc": now},
        "plugin_settings": {"instance_name": "Garmin", "custom_fields_values": fields or {}},
    }
    return env.from_string(source).render(**ctx)


# A single half or quadrant view sits in its real slot, beside empty slots.
SOLO = {"half_vertical": "1Lx1R", "half_horizontal": "1Tx1B", "quadrant": "2x2"}
SLOTS = {"1Lx1R": 2, "1Tx1B": 2, "2x2": 4}


def page(views: list[tuple[str, str]], mashup: str | None, version: str) -> str:
    inner = "".join(f'<div class="view view--{layout}">{html}</div>' for layout, html in views)
    if mashup:
        inner = f'<div class="mashup mashup--{mashup}">{inner}</div>'
    return f"""<!DOCTYPE html>
<html><head><meta charset="utf-8">
<link rel="stylesheet" href="/css/{version}/plugins.css">
<script src="/js/{version}/plugins.js"></script>
<style>html,body{{margin:0;padding:0;width:800px;height:480px;overflow:hidden}}</style>
</head><body class="environment trmnl">
<div class="screen screen--ogv2 screen--2bit screen--md screen--landscape screen--density-1x">
{inner}
</div></body></html>"""


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--framework", required=True, type=Path, help="trmnl-framework/public")
    ap.add_argument("--out", required=True, type=Path)
    ap.add_argument("--version", default=DEFAULT_VERSION)
    ap.add_argument("--fixtures", nargs="*", help="fixture names (default: all)")
    ap.add_argument("--extra", type=Path, help="extra fixture directory (e.g. private data)")
    ap.add_argument("--layouts", nargs="*", default=list(LAYOUTS))
    ap.add_argument("--no-mashups", action="store_true")
    args = ap.parse_args()

    out: Path = args.out
    if out.exists():
        shutil.rmtree(out)
    pages = out / "pages"
    pages.mkdir(parents=True)
    for sub in ("css", "js", "fonts"):
        (out / sub).symlink_to((args.framework / sub).resolve())

    files = {p.stem: p for p in FIXTURES.glob("*.json")}
    if args.extra:
        files.update({p.stem: p for p in args.extra.glob("*.json")})
    names = args.fixtures or sorted(files)
    fixtures = {n: json.loads(files[n].read_text()) for n in names}
    index = []
    for layout in args.layouts:
        for n, data in fixtures.items():
            views = [(layout, render_view(layout, data))]
            solo = SOLO.get(layout)
            if solo:
                views += [(layout, "")] * (SLOTS[solo] - 1)
            html = page(views, solo, args.version)
            name = f"{layout}@{n}"
            (pages / f"{name}.html").write_text(html, encoding="utf-8")
            index.append({"name": name, "set": layout})
    # Mashups: this plugin in every slot of a layout (worst case for density).
    for mashup, slots in ({} if args.no_mashups else MASHUPS).items():
        for n in ("ride", "run", "indoor", "auth_error"):
            if n not in fixtures:
                continue
            views = [(s, render_view(s, fixtures[n])) for s in slots]
            name = f"{mashup}@{n}"
            (pages / f"{name}.html").write_text(page(views, mashup, args.version), "utf-8")
            index.append({"name": name, "set": mashup})
    (out / "index.json").write_text(json.dumps(index, indent=1))
    print(f"{len(index)} pages in {pages}")


if __name__ == "__main__":
    main()
