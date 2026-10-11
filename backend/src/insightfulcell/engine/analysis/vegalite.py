"""A chart's Vega-Lite spec, for downloading and redrawing elsewhere.

The page and the HTML export draw charts from ``ChartData`` themselves (accessible, no script
evaluation); this spec carries the same values inline, so a person can open a chart in any
Vega-Lite tool and get exactly what the report shows.
"""

from __future__ import annotations

from typing import Any, Final

from insightfulcell.engine.analysis.facts import ChartData

VEGA_LITE_SCHEMA: Final = "https://vega.github.io/schema/vega-lite/v5.json"
MARK_COLOR: Final = "#1a8a6f"
_FORMATS: Final = {
    "percent": ".1~%",
    "signed_percent": "+.1~%",
    "count": ",d",
    "strength": ".2f",
    "number": ",.3~g",
}


def spec(chart: ChartData) -> dict[str, Any]:
    values = [
        {
            "label": p.label,
            "value": p.value,
            **({"n": p.n} if p.n is not None else {}),
            **({"x": p.x} if p.x is not None else {}),
            **({"x2": p.x2} if p.x2 is not None else {}),
        }
        for p in chart.points
    ]
    fmt = _FORMATS[chart.value_format]
    y_title = chart.y_label + (f" ({chart.unit})" if chart.unit and chart.unit != "%" else "")
    value = {"field": "value", "type": "quantitative", "title": y_title, "format": fmt}
    tooltip: list[dict[str, Any]] = [
        {"field": "label", "type": "nominal", "title": chart.x_label},
        {"field": "value", "type": "quantitative", "title": y_title, "format": fmt},
    ]
    if any(p.n is not None for p in chart.points):
        tooltip.append({"field": "n", "type": "quantitative", "title": "Rows", "format": ",d"})
    base: dict[str, Any] = {
        "$schema": VEGA_LITE_SCHEMA,
        "title": chart.title,
        "description": chart.note or chart.title,
        "data": {"values": values},
        "width": "container",
        "config": {"mark": {"color": MARK_COLOR}, "axis": {"labelFontSize": 11}},
    }
    if chart.kind == "bar":
        return base | {
            "height": {"step": 22},
            "mark": {"type": "bar", "cornerRadiusEnd": 3},
            "encoding": {
                "y": {"field": "label", "type": "nominal", "sort": None, "title": chart.x_label},
                "x": {**value, "axis": {"format": fmt}},
                "tooltip": tooltip,
            },
        }
    if chart.kind == "line":
        return base | {
            "height": 260,
            "mark": {"type": "line", "point": True},
            "encoding": {
                "x": {"field": "label", "type": "ordinal", "sort": None, "title": chart.x_label},
                "y": {**value, "axis": {"format": fmt}},
                "tooltip": tooltip,
            },
        }
    if chart.kind == "histogram":
        return base | {
            "height": 260,
            "mark": {"type": "bar"},
            "encoding": {
                "x": {
                    "field": "x",
                    "type": "quantitative",
                    "bin": {"binned": True},
                    "title": chart.x_label,
                },
                "x2": {"field": "x2"},
                "y": {**value, "axis": {"format": fmt}},
                "tooltip": tooltip,
            },
        }
    return base | {
        "height": 280,
        "mark": {"type": "point", "filled": True, "opacity": 0.7},
        "encoding": {
            "x": {"field": "x", "type": "quantitative", "title": chart.x_label},
            "y": {**value, "axis": {"format": fmt}},
            "tooltip": [{"field": "label", "type": "nominal", "title": "Point"}],
        },
    }
