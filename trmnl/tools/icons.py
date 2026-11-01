"""Regenerate the icon template in trmnl/src/shared.liquid from @mdi/svg.

    npm pack @mdi/svg && tar -xzf mdi-svg-*.tgz
    python trmnl/tools/icons.py package/svg

Writes `{% template gt_sym %}` (args: n = icon name, size = px) between the
`ICONS:BEGIN` / `ICONS:END` markers, with exactly the path data of the
Material Design Icons listed below (Apache 2.0). Run it again after adding or
removing a name, so no path is ever typed by hand.
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

BEGIN, END = "{% comment %}ICONS:BEGIN{% endcomment %}", "{% comment %}ICONS:END{% endcomment %}"


def path_data(svg_dir: Path, name: str) -> str:
    m = re.search(r' d="([^"]+)"', (svg_dir / f"{name}.svg").read_text())
    if not m:
        raise SystemExit(f"no path data in {name}.svg")
    return m.group(1)


def template(svg_dir: Path) -> str:
    lines = [BEGIN, "{% template gt_sym %}{% liquid", "  case n"]
    for key, mdi in ICONS.items():
        lines += [f'  when "{key}"', f'    assign d = "{path_data(svg_dir, mdi)}"']
    lines += [
        "  else",
        f'    assign d = "{path_data(svg_dir, ICONS["other"])}"',
        "  endcase",
        '%}<svg class="gt-sym" viewBox="0 0 24 24" width="{{ size }}" height="{{ size }}">'
        '<path d="{{ d }}"/></svg>{% endtemplate %}',
        END,
    ]
    return "\n".join(lines)


def main() -> None:
    svg_dir = Path(sys.argv[1] if len(sys.argv) > 1 else "package/svg")
    src = SHARED.read_text(encoding="utf-8")
    i, j = src.index(BEGIN), src.index(END) + len(END)
    SHARED.write_text(src[:i] + template(svg_dir) + src[j:], encoding="utf-8")
    print(f"{len(ICONS)} icons written to {SHARED}")


if __name__ == "__main__":
    main()
