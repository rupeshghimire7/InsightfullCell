"""SVG charts and maps as PNG images, for the Word and PDF exports.

resvg draws the same SVG the HTML export embeds. Only the fonts bundled with this package are
used (never the machine's), so an export looks the same on every server.
"""

from __future__ import annotations

from functools import cache
from importlib.resources import files
from pathlib import Path
from typing import Final

import resvg_py

FONT_FAMILY: Final = "DejaVu Sans"
#: Pixels per SVG unit: charts are 640 units wide, so 1,280 px (sharp on paper at 6 inches).
SCALE: Final = 2.0


@cache
def font_path(bold: bool = False) -> Path:
    name = "DejaVuSans-Bold.ttf" if bold else "DejaVuSans.ttf"
    return Path(str(files("insightfulcell.exports") / "fonts" / name))


def svg_to_png(svg: str) -> bytes:
    return bytes(
        resvg_py.svg_to_bytes(
            svg_string=svg,
            zoom=SCALE,
            background="#ffffff",
            skip_system_fonts=True,
            font_files=[str(font_path()), str(font_path(bold=True))],
            font_family=FONT_FAMILY,
            sans_serif_family=FONT_FAMILY,
        )
    )
