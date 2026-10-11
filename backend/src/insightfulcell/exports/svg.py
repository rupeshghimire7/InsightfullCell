"""Charts and maps as static SVG, for the HTML export.

The same data the page draws (``ChartData``, ``MapData``), drawn without script so the file
opens anywhere, prints, and can be saved as PDF. Every piece of text is escaped here; the
template inserts the returned markup as is.
"""

from __future__ import annotations

import math
from collections.abc import Sequence
from typing import Final

from markupsafe import Markup, escape

from insightfulcell.engine.analysis import facts as fmt
from insightfulcell.engine.analysis.facts import ChartData, MapData, ValueFormat

WIDTH: Final = 640
PLOT_HEIGHT: Final = 220
BAR_ROW: Final = 22
LABEL_WIDTH: Final = 190
INK: Final = "#1f2421"
MUTED: Final = "#5e625e"
GRID: Final = "#dedcd6"
MARK: Final = "#1a8a6f"
#: Five steps from light to the chart colour, for maps (quantised by value).
STEPS: Final = ("#d5ebe3", "#a6d4c3", "#6fb9a1", "#3c9d80", "#1a7a60")
NO_ROWS: Final = "#eceae5"
_CURRENCY: Final = {"$", "€", "£", "¥"}


_WRITERS: Final = {
    "percent": fmt.share,
    "signed_percent": fmt.signed_share,
    "count": fmt.count,
    "strength": fmt.coefficient,
}


def format_value(value: float, value_format: ValueFormat, unit: str | None) -> str:
    """A chart value written the way the facts write numbers."""
    if not math.isfinite(value):
        return "n/a"
    writer = _WRITERS.get(value_format)
    if writer is not None:
        return str(writer(value))
    text = fmt.plain(value)
    if not unit or unit == "%":
        return text
    if unit in _CURRENCY:
        return f"-{unit}{text[1:]}" if text.startswith("-") else f"{unit}{text}"
    return f"{text} {unit}"


def _t(text: object) -> str:
    return str(escape(str(text)))


def _n(value: float) -> str:
    return f"{value:.1f}"


def _clip(text: str, limit: int) -> str:
    return text if len(text) <= limit else text[: limit - 1] + "…"


def nice_ticks(low: float, high: float, count: int = 4) -> list[float]:
    if low == high:
        return [low]
    span = high - low
    power = 10 ** math.floor(math.log10(span / count))
    step = next((m * power for m in (1, 2, 2.5, 5, 10) if span / (m * power) <= count), power * 10)
    start = math.ceil(low / step) * step
    ticks: list[float] = []
    tick = start
    while tick <= high + step * 1e-9:
        ticks.append(float(f"{tick:.12g}"))
        tick += step
    return ticks


# --------------------------------------------------------------------------- charts


def chart_svg(chart: ChartData) -> Markup:
    label = f"{chart.title}. {chart.y_label} by {chart.x_label}."
    if chart.kind == "bar":
        body, height = _bars(chart)
    else:
        body, height = _plot(chart)
    return Markup(  # noqa: S704  (every text in ``body`` is escaped by the helpers above)
        f'<svg class="chart" role="img" aria-label="{_t(label)}" viewBox="0 0 {WIDTH} {height}" '
        f'xmlns="http://www.w3.org/2000/svg" font-family="inherit" font-size="11">'
        f"<title>{_t(chart.title)}</title>{body}</svg>"
    )


def _bars(chart: ChartData) -> tuple[str, int]:
    values = [p.value for p in chart.points]
    low, high = min(0.0, *values), max(0.0, *values)
    span = (high - low) or 1.0
    left, right = LABEL_WIDTH + 8, WIDTH - 70
    scale = (right - left) / span
    zero = left + (0 - low) * scale
    parts: list[str] = []
    for i, point in enumerate(chart.points):
        y = 6 + i * BAR_ROW
        x0 = zero if point.value >= 0 else zero + point.value * scale
        width = abs(point.value) * scale
        text = format_value(point.value, chart.value_format, chart.unit)
        after = x0 + width + 4 if point.value >= 0 else x0 - 4
        parts.append(
            f'<text x="{LABEL_WIDTH}" y="{y + 12}" text-anchor="end" fill="{INK}">'
            f"<title>{_t(point.label)}</title>{_t(_clip(point.label, 30))}</text>"
            f'<rect x="{_n(x0)}" y="{y + 3}" width="{_n(max(width, 0.8))}" height="13" rx="2" '
            f'fill="{MARK}"/>'
            f'<text x="{_n(after)}" '
            f'y="{y + 13}" text-anchor="{"start" if point.value >= 0 else "end"}" '
            f'fill="{MUTED}">{_t(text)}</text>'
        )
    if low < 0:
        bottom = 6 + len(chart.points) * BAR_ROW
        parts.append(
            f'<line x1="{_n(zero)}" x2="{_n(zero)}" y1="4" y2="{bottom}" stroke="{GRID}"/>'
        )
    return "".join(parts), 12 + len(chart.points) * BAR_ROW


def _plot(chart: ChartData) -> tuple[str, int]:
    points = list(chart.points)
    values = [p.value for p in points] or [0.0]
    low = min(values + ([0.0] if chart.kind == "histogram" else []))
    high = max(values + ([0.0] if chart.kind == "histogram" else []))
    if low == high:
        low, high = low - 1, high + 1
    ticks = nice_ticks(low, high)
    y_low, y_high = min(low, ticks[0]), max(high, ticks[-1])
    top, bottom = 10, PLOT_HEIGHT
    left, right = 64, WIDTH - 12

    def y_of(v: float) -> float:
        return top + (1 - (v - y_low) / ((y_high - y_low) or 1)) * (bottom - top)

    parts = [
        f'<line x1="{left}" x2="{right}" y1="{_n(y_of(t))}" y2="{_n(y_of(t))}" stroke="{GRID}"/>'
        f'<text x="{left - 6}" y="{_n(y_of(t) + 4)}" text-anchor="end" fill="{MUTED}">'
        f"{_t(format_value(t, chart.value_format, chart.unit))}</text>"
        for t in ticks
    ]
    if chart.kind == "scatter":
        xs = [p.x if p.x is not None else 0.0 for p in points] or [0.0]
        x_low, x_high = min(xs), max(xs)

        def x_of(v: float) -> float:
            return left + (v - x_low) / ((x_high - x_low) or 1) * (right - left)

        parts += [
            f'<circle cx="{_n(x_of(x))}" cy="{_n(y_of(p.value))}" r="3" fill="{MARK}" '
            f'fill-opacity="0.7"><title>{_t(p.label)}</title></circle>'
            for p, x in zip(points, xs, strict=False)
        ]
        x_ticks = nice_ticks(x_low, x_high, 4)
        parts += [
            f'<text x="{_n(x_of(t))}" y="{bottom + 16}" text-anchor="middle" fill="{MUTED}">'
            f"{_t(format_value(t, 'number', None))}</text>"
            for t in x_ticks
        ]
    else:
        step = (right - left) / max(len(points), 1)
        centres = [left + step * (i + 0.5) for i in range(len(points))]
        if chart.kind == "histogram":
            base = y_of(0)
            parts += [
                f'<rect x="{_n(c - step / 2 + 1)}" y="{_n(y_of(p.value))}" '
                f'width="{_n(max(step - 2, 1))}" height="{_n(max(base - y_of(p.value), 0))}" '
                f'fill="{MARK}"><title>{_t(p.label)}: '
                f"{_t(format_value(p.value, chart.value_format, chart.unit))}</title></rect>"
                for p, c in zip(points, centres, strict=True)
            ]
        else:
            path = " ".join(
                f"{'M' if i == 0 else 'L'}{_n(c)},{_n(y_of(p.value))}"
                for i, (p, c) in enumerate(zip(points, centres, strict=True))
            )
            parts.append(f'<path d="{path}" fill="none" stroke="{MARK}" stroke-width="2"/>')
            parts += [
                f'<circle cx="{_n(c)}" cy="{_n(y_of(p.value))}" r="3" fill="{MARK}">'
                f"<title>{_t(p.label)}: "
                f"{_t(format_value(p.value, chart.value_format, chart.unit))}</title></circle>"
                for p, c in zip(points, centres, strict=True)
            ]
        picks = sorted({0, (len(points) - 1) // 2, len(points) - 1}) if points else []
        for k, i in enumerate(picks):
            label = points[i].label
            if chart.kind == "histogram":
                label = label.split(" to ")[0]
            anchor = "start" if k == 0 else ("end" if k == len(picks) - 1 else "middle")
            x = left if anchor == "start" else (right if anchor == "end" else centres[i])
            parts.append(
                f'<text x="{_n(x)}" y="{bottom + 16}" text-anchor="{anchor}" fill="{MUTED}">'
                f"{_t(_clip(label, 28))}</text>"
            )
    return "".join(parts), bottom + 24


# --------------------------------------------------------------------------- maps


def _thresholds(values: Sequence[float]) -> list[float]:
    """Upper bounds of the five colour steps: equal-count groups of the values shown."""
    ordered = sorted(values)
    if not ordered:
        return []
    return [
        ordered[min(len(ordered) - 1, math.ceil(len(ordered) * k / 5) - 1)] for k in range(1, 5)
    ]


def step_of(value: float, thresholds: Sequence[float]) -> int:
    return next((i for i, bound in enumerate(thresholds) if value <= bound), len(STEPS) - 1)


def map_svg(data: MapData) -> Markup:
    shown = [r.value for r in data.regions if r.status == "value" and r.value is not None]
    thresholds = _thresholds(shown)
    parts = [
        '<defs><pattern id="few" width="6" height="6" patternUnits="userSpaceOnUse" '
        'patternTransform="rotate(45)"><rect width="6" height="6" fill="#f4f3f1"/>'
        f'<line x1="0" y1="0" x2="0" y2="6" stroke="{MUTED}" stroke-width="1.4"/></pattern></defs>'
    ]
    for region in data.regions:
        if region.status == "value" and region.value is not None:
            fill = STEPS[step_of(region.value, thresholds)]
            tip = (
                f"{region.name}: {format_value(region.value, data.value_format, data.unit)} "
                f"({fmt.count(region.n or 0)} rows)"
            )
        elif region.status == "few_rows":
            fill, tip = "url(#few)", f"{region.name}: fewer than ten rows"
        else:
            fill, tip = NO_ROWS, f"{region.name}: no rows"
        parts.append(
            f'<path d="{_t(region.path)}" fill="{fill}" stroke="#ffffff" stroke-width="0.6" '
            f'fill-rule="evenodd"><title>{_t(tip)}</title></path>'
        )
    label = f"{data.title}. Map; the numbers are in the table below."
    return Markup(  # noqa: S704  (text escaped above; paths are numbers and letters we built)
        f'<svg class="map" role="img" aria-label="{_t(label)}" '
        f'viewBox="0 0 {_n(data.width)} {_n(data.height)}" xmlns="http://www.w3.org/2000/svg">'
        f"<title>{_t(data.title)}</title>{''.join(parts)}</svg>"
    )


def map_legend(data: MapData) -> list[tuple[str, str]]:
    """(colour, words) for each step that has regions, low to high, labelled with the range
    of the values in it."""
    shown = [r.value for r in data.regions if r.status == "value" and r.value is not None]
    thresholds = _thresholds(shown)
    out: list[tuple[str, str]] = []
    for i, colour in enumerate(STEPS):
        members = [v for v in shown if step_of(v, thresholds) == i]
        if not members:
            continue
        a = format_value(min(members), data.value_format, data.unit)
        b = format_value(max(members), data.value_format, data.unit)
        out.append((colour, a if a == b else f"{a} to {b}"))
    return out
