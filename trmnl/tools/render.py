"""Render the plugin markup locally into HTML pages (one per view or mashup).

    python trmnl/tools/render.py --framework ../trmnl-framework/public \
        --trmnl-liquid ext/trmnl-liquid/lib --out out/

The Liquid is rendered by tl_render.rb with TRMNL's own renderer, the
trmnl-liquid gem, so integer division, `split` and the `{% template %}` tag
behave exactly as on TRMNL's server. (A python-liquid stand-in used before
differed: it kept a trailing empty string after `split`, which added an
invisible bar to the splits chart.) See trmnl/README.md for the setup.

The framework assets come from a checkout of usetrmnl/trmnl-framework
(`public/` holds css/<version>, js/<version> and fonts/).
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
from pathlib import Path

HERE = Path(__file__).resolve().parent
SRC = HERE.parent / "src"
FIXTURES = HERE.parent / "fixtures"
TL_RENDER = HERE / "tl_render.rb"
LAYOUTS = ("full", "half_vertical", "half_horizontal", "quadrant")
DEFAULT_VERSION = "3.4.4"
OFFSET = 3600  # Europe/Warsaw in winter; matches trmnl.user.utc_offset

# Mashups the owner runs, plus the 2x2 grid of quadrants.
MASHUPS = {
    "1Lx2R": ("half_vertical", "quadrant", "quadrant"),
    "2Tx1B": ("quadrant", "quadrant", "half_horizontal"),
    "1Lx1R": ("half_vertical", "half_vertical"),
    "2x2": ("quadrant", "quadrant", "quadrant", "quadrant"),
}


def job(layout: str, data: dict, fields: dict | None = None) -> dict:
    """One render job: shared + layout markup and the context TRMNL builds."""
    shared = (SRC / "shared.liquid").read_text(encoding="utf-8")
    body = (SRC / f"{layout}.liquid").read_text(encoding="utf-8")
    ctx = {k: v for k, v in data.items() if not k.startswith("__")}
    ctx["trmnl"] = {
        "user": {"time_zone_iana": "Europe/Warsaw", "utc_offset": OFFSET, "locale": "en"},
        "device": {"width": 800, "height": 480, "model": "og_plus", "bit_depth": 2},
        "system": {"timestamp_utc": data.get("__now")},
        "plugin_settings": {"instance_name": "Garmin", "custom_fields_values": fields or {}},
    }
    return {"src": shared + "\n" + body, "ctx": ctx}


def render_all(jobs: list[dict], liquid_lib: Path) -> list[str]:
    """Render many jobs in one Ruby process with trmnl-liquid."""
    env = {**os.environ, "LANG": "C.UTF-8"}
    # Dev tool: `ruby` from PATH, fixed arguments, no shell.
    out = subprocess.run(  # noqa: S603
        ["ruby", "-E", "UTF-8", "-W0", "-I", str(liquid_lib), str(TL_RENDER)],  # noqa: S607
        input=json.dumps(jobs).encode(),
        capture_output=True,
        env=env,
        check=False,
    )
    if out.returncode != 0:
        raise SystemExit(f"tl_render.rb failed:\n{out.stderr.decode(errors='replace')}")
    return json.loads(out.stdout)


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
    ap.add_argument(
        "--trmnl-liquid",
        type=Path,
        default=os.environ.get("TRMNL_LIQUID_LIB"),
        help="lib/ directory of the trmnl-liquid gem (or TRMNL_LIQUID_LIB)",
    )
    ap.add_argument("--out", required=True, type=Path)
    ap.add_argument("--version", default=DEFAULT_VERSION)
    ap.add_argument("--fixtures", nargs="*", help="fixture names (default: all)")
    ap.add_argument("--extra", type=Path, help="extra fixture directory (e.g. private data)")
    ap.add_argument("--layouts", nargs="*", default=list(LAYOUTS))
    ap.add_argument("--no-mashups", action="store_true")
    args = ap.parse_args()
    if not args.trmnl_liquid or not (Path(args.trmnl_liquid) / "trmnl" / "liquid.rb").is_file():
        ap.error("--trmnl-liquid must point at the trmnl-liquid gem's lib/ directory")

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

    # Every page as (name, set, mashup, [(layout, fixture or None for an empty slot)]).
    plan = []
    for layout in args.layouts:
        solo = SOLO.get(layout)
        for n in fixtures:
            slots = [(layout, n)] + [(layout, None)] * ((SLOTS[solo] - 1) if solo else 0)
            plan.append((f"{layout}@{n}", layout, solo, slots))
    # Mashups: this plugin in every slot of a layout (worst case for density).
    for mashup, layouts in ({} if args.no_mashups else MASHUPS).items():
        for n in ("ride", "run", "indoor", "auth_error"):
            if n in fixtures:
                plan.append((f"{mashup}@{n}", mashup, mashup, [(s, n) for s in layouts]))

    keys = sorted({(lay, n) for *_, slots in plan for lay, n in slots if n is not None})
    html = dict(
        zip(
            keys,
            render_all([job(lay, fixtures[n]) for lay, n in keys], Path(args.trmnl_liquid)),
            strict=True,
        )
    )
    index = []
    for name, set_name, mashup, slots in plan:
        views = [(lay, html[(lay, n)] if n is not None else "") for lay, n in slots]
        (pages / f"{name}.html").write_text(page(views, mashup, args.version), encoding="utf-8")
        index.append({"name": name, "set": set_name})
    (out / "index.json").write_text(json.dumps(index, indent=1))
    print(f"{len(index)} pages ({len(keys)} views rendered with trmnl-liquid) in {pages}")


if __name__ == "__main__":
    main()
