"""Quantize screenshots to the OG's 4 greys and compose contact sheets.

    python trmnl/tools/sheet.py out/

Writes out/png4/<name>.png (0/85/170/255, no dithering, like the device)
and out/sheets/<set>.png, one sheet per layout or mashup.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

from PIL import Image, ImageDraw

LEVELS = (0, 85, 170, 255)


def quantize(img: Image.Image) -> Image.Image:
    g = img.convert("L")
    lut = [min(LEVELS, key=lambda v: abs(v - i)) for i in range(256)]
    return g.point(lut)


def main() -> None:
    out = Path(sys.argv[1] if len(sys.argv) > 1 else "out")
    index = json.loads((out / "index.json").read_text())
    gate = {r["name"]: r["problems"] for r in json.loads((out / "gate.json").read_text())}
    (out / "png4").mkdir(exist_ok=True)
    (out / "sheets").mkdir(exist_ok=True)
    sets: dict[str, list[str]] = {}
    for item in index:
        sets.setdefault(item["set"], []).append(item["name"])
        img = quantize(Image.open(out / "png" / f"{item['name']}.png"))
        img.save(out / "png4" / f"{item['name']}.png")
    for set_name, names in sets.items():
        cols = 2 if set_name in ("full", "1Lx2R", "2Tx1B", "1Lx1R", "2x2") else 3
        cell_w, cell_h, pad, cap = 800, 480, 16, 28
        rows = (len(names) + cols - 1) // cols
        sheet = Image.new(
            "L", (cols * (cell_w + pad) + pad, rows * (cell_h + cap + pad) + pad), 255
        )
        draw = ImageDraw.Draw(sheet)
        for i, name in enumerate(names):
            x = pad + (i % cols) * (cell_w + pad)
            y = pad + (i // cols) * (cell_h + cap + pad)
            mark = "  FAIL: " + "; ".join(gate.get(name, []))[:90] if gate.get(name) else ""
            draw.text((x, y + 4), name + mark, fill=0)
            img = Image.open(out / "png4" / f"{name}.png")
            sheet.paste(img, (x, y + cap))
            draw.rectangle((x - 1, y + cap - 1, x + cell_w, y + cap + cell_h), outline=170)
        sheet.save(out / "sheets" / f"{set_name}.png")
    print(f"{len(sets)} sheets in {out / 'sheets'}")


if __name__ == "__main__":
    main()
