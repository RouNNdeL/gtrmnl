"""Regenerate the icon template in trmnl/src/shared.liquid from @mdi/svg.

    npm pack @mdi/svg && tar -xzf mdi-svg-*.tgz             # -> mdi/package/svg
    npm pack simple-icons && tar -xzf simple-icons-*.tgz   # -> si/package/icons
    python trmnl/tools/icons.py mdi/package/svg si/package/icons

Writes, between the `ICONS:BEGIN` / `ICONS:END` markers:
- `{% template gt_sym %}` (args: n = icon name, size = px): exactly the path
  data of the Material Design Icons listed below (Apache 2.0);
- `{% template gt_logo %}` (arg: h = height px): Garmin's logo from Simple
  Icons (the SVG is CC0; the logo is Garmin's trademark), cropped to its bounds.
Run it again after adding or removing a name, so no path is typed by hand.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

SHARED = Path(__file__).resolve().parents[1] / "src" / "shared.liquid"

# icon name in the markup -> MDI file name
ICONS = {
    # sports (normalize.category)
    "run": "run",
    "ride": "bike",
    "hike": "hiking",
    "walk": "walk",
    "swim": "swim",
    "strength": "dumbbell",
    "ski": "ski",
    "other": "heart-pulse",
    # stats
    "time": "clock-outline",
    "distance": "map-marker-distance",
    "speed": "speedometer",
    "heart": "heart",
    "ascent": "elevation-rise",
    "calories": "fire",
    "load": "lightning-bolt",
    "battery": "battery-heart-variant",
    "sleep": "sleep",
    "hrv": "pulse",
    "readiness": "gauge",
    "recovery": "timer-sand",
    "status": "trending-up",
    "splits": "flag-checkered",
    "week": "calendar-week",
}

# Garmin logo: tight viewBox of the Simple Icons path (measured with getBBox).
LOGO_FILE, LOGO_VIEWBOX = "garmin", (0, 8.746, 24, 6.508)

BEGIN, END = "{% comment %}ICONS:BEGIN{% endcomment %}", "{% comment %}ICONS:END{% endcomment %}"


def path_data(svg_dir: Path, name: str) -> str:
    m = re.search(r' d="([^"]+)"', (svg_dir / f"{name}.svg").read_text())
    if not m:
        raise SystemExit(f"no path data in {name}.svg")
    return m.group(1)


def template(svg_dir: Path, logo_dir: Path) -> str:
    lines = [BEGIN, "{% template gt_sym %}{% liquid", "  case n"]
    for key, mdi in ICONS.items():
        lines += [f'  when "{key}"', f'    assign d = "{path_data(svg_dir, mdi)}"']
    lines += [
        "  else",
        f'    assign d = "{path_data(svg_dir, ICONS["other"])}"',
        "  endcase",
        '%}<svg class="gt-sym" viewBox="0 0 24 24" width="{{ size }}" height="{{ size }}">'
        '<path d="{{ d }}"/></svg>{% endtemplate %}',
    ]
    x, y, w, h = LOGO_VIEWBOX
    width_expr = f"{{{{ h | times: {w / h:.4f} | round }}}}"
    lines += [
        "{% template gt_logo %}"
        f'<svg class="gt-logo" viewBox="{x} {y} {w} {h}" height="{{{{ h }}}}" width="{width_expr}" '
        'role="img" aria-label="Garmin">'
        f'<path d="{path_data(logo_dir, LOGO_FILE)}"/></svg>{{% endtemplate %}}',
        END,
    ]
    return "\n".join(lines)


def main() -> None:
    if len(sys.argv) != 3:
        raise SystemExit("usage: icons.py <@mdi/svg svg dir> <simple-icons icons dir>")
    svg_dir, logo_dir = Path(sys.argv[1]), Path(sys.argv[2])
    src = SHARED.read_text(encoding="utf-8")
    i, j = src.index(BEGIN), src.index(END) + len(END)
    SHARED.write_text(src[:i] + template(svg_dir, logo_dir) + src[j:], encoding="utf-8")
    print(f"{len(ICONS)} icons written to {SHARED}")


if __name__ == "__main__":
    main()
