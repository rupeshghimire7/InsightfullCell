"""The chart catalogue: which columns each chart kind takes, the SQL that aggregates its data,
and its Vega-Lite spec.

Phase A plans charts; Phase B's ``visualize`` stage will build them from these definitions and
check every claimed value against the cleaned data. The recipe uses the same SQL and specs, so
a downloaded recipe draws what the plan describes.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Final

from insightfulcell.engine.planning.language import Aggregate, ChartKind, ResolvedChart
from insightfulcell.engine.sql import ident
from insightfulcell.engine.types import CATEGORICAL_MEANINGS, NUMERIC_MEANINGS, Meaning

VEGA_LITE_SCHEMA: Final = "https://vega.github.io/schema/vega-lite/v5.json"
ACCENT: Final = "#2f6f62"
SAMPLE_ROWS: Final = 5_000
TOP_CATEGORIES: Final = 30
HISTOGRAM_BINS: Final = 20


@dataclass(frozen=True, slots=True)
class ChartSpec:
    label: str
    x: frozenset[Meaning]
    y: frozenset[Meaning] | None  # None: the chart takes no y
    y_required: bool = False
    needs_geo_stage: bool = False


CATALOGUE: Final[dict[ChartKind, ChartSpec]] = {
    ChartKind.HISTOGRAM: ChartSpec("Histogram", NUMERIC_MEANINGS, None),
    ChartKind.BAR: ChartSpec("Bar chart", CATEGORICAL_MEANINGS, NUMERIC_MEANINGS),
    ChartKind.LINE: ChartSpec("Line over time", frozenset({Meaning.DATETIME}), NUMERIC_MEANINGS),
    ChartKind.SCATTER: ChartSpec(
        "Scatter plot", NUMERIC_MEANINGS, NUMERIC_MEANINGS, y_required=True
    ),
    ChartKind.BOX: ChartSpec(
        "Box plot by group", CATEGORICAL_MEANINGS, NUMERIC_MEANINGS, y_required=True
    ),
    ChartKind.HEATMAP: ChartSpec(
        "Heatmap of two categories", CATEGORICAL_MEANINGS, CATEGORICAL_MEANINGS, y_required=True
    ),
    ChartKind.MAP: ChartSpec(
        "Map of regions", frozenset({Meaning.GEOGRAPHY}), NUMERIC_MEANINGS, needs_geo_stage=True
    ),
}

_AGG: Final = {
    Aggregate.COUNT: "count(*)",
    Aggregate.SUM: "sum({y})",
    Aggregate.MEAN: "avg({y})",
    Aggregate.MEDIAN: "median({y})",
}


def _agg(chart: ResolvedChart) -> str:
    if chart.y is None:
        return "count(*)"
    return _AGG[chart.aggregate or Aggregate.MEAN].format(y=f"CAST({ident(chart.y)} AS DOUBLE)")


def data_sql(chart: ResolvedChart, table: str = "cleaned") -> str | None:
    """The query whose rows are the chart's data, or ``None`` (maps wait for place codes)."""
    t, x = ident(table), ident(chart.x)
    group = f", {ident(chart.group)} AS grp" if chart.group else ""
    group_by = ", grp" if chart.group else ""
    if chart.kind is ChartKind.HISTOGRAM:
        n = HISTOGRAM_BINS
        return (
            f"WITH s AS (SELECT CAST({x} AS DOUBLE) AS v FROM {t} WHERE {x} IS NOT NULL), "
            f"b AS (SELECT min(v) AS lo, (max(v) - min(v)) / {n} AS w FROM s) "
            f"SELECT lo + w * least({n - 1}, floor((v - lo) / nullif(w, 0))) AS bin_start, "
            f"lo + w * (least({n - 1}, floor((v - lo) / nullif(w, 0))) + 1) AS bin_end, "
            f"count(*) AS n FROM s, b GROUP BY ALL ORDER BY bin_start"
        )
    if chart.kind is ChartKind.BAR:
        return (
            f"SELECT CAST({x} AS VARCHAR) AS category{group}, {_agg(chart)} AS value "
            f"FROM {t} WHERE {x} IS NOT NULL GROUP BY category{group_by} "
            f"ORDER BY value DESC LIMIT {TOP_CATEGORIES}"
        )
    if chart.kind is ChartKind.LINE:
        return (
            f"SELECT date_trunc('month', CAST({x} AS TIMESTAMP)) AS period{group}, "
            f"{_agg(chart)} AS value FROM {t} WHERE {x} IS NOT NULL "
            f"GROUP BY period{group_by} ORDER BY period"
        )
    if chart.kind in {ChartKind.SCATTER, ChartKind.BOX}:
        assert chart.y is not None  # noqa: S101  (validated: y is required for this chart)
        y = ident(chart.y)
        x_expr = (
            f"CAST({x} AS DOUBLE)" if chart.kind is ChartKind.SCATTER else f"CAST({x} AS VARCHAR)"
        )
        return (
            f"SELECT * FROM (SELECT {x_expr} AS x, CAST({y} AS DOUBLE) AS y{group} FROM {t} "
            f"WHERE {x} IS NOT NULL AND {y} IS NOT NULL) "
            f"USING SAMPLE reservoir({SAMPLE_ROWS} ROWS) REPEATABLE (42)"
        )
    if chart.kind is ChartKind.HEATMAP:
        assert chart.y is not None  # noqa: S101  (validated: y is required for this chart)
        y = ident(chart.y)
        return (
            f"SELECT CAST({x} AS VARCHAR) AS x, CAST({y} AS VARCHAR) AS y, count(*) AS n "
            f"FROM {t} WHERE {x} IS NOT NULL AND {y} IS NOT NULL GROUP BY ALL"
        )
    return None


def vega_lite(
    chart: ResolvedChart, values: list[dict[str, Any]], labels: dict[str, str]
) -> dict[str, Any]:
    """A Vega-Lite spec for the chart, with ``values`` inline and display names as titles."""
    x_title = labels.get(chart.x, chart.x)
    y_title = labels.get(chart.y, chart.y) if chart.y else "Rows"
    if chart.y and chart.aggregate and chart.aggregate is not Aggregate.COUNT:
        y_title = f"{chart.aggregate.value.title()} of {y_title}"
    color: dict[str, Any] = (
        {
            "color": {
                "field": "grp",
                "type": "nominal",
                "title": labels.get(chart.group, chart.group),
            }
        }
        if chart.group
        else {}
    )
    base: dict[str, Any] = {
        "$schema": VEGA_LITE_SCHEMA,
        "title": chart.title,
        "data": {"values": values},
        "width": "container",
        "height": 280,
        "config": {"mark": {"color": ACCENT}, "axis": {"labelFontSize": 11}},
    }
    if chart.kind is ChartKind.HISTOGRAM:
        return base | {
            "mark": "bar",
            "encoding": {
                "x": {
                    "field": "bin_start",
                    "bin": {"binned": True},
                    "type": "quantitative",
                    "title": x_title,
                },
                "x2": {"field": "bin_end"},
                "y": {"field": "n", "type": "quantitative", "title": "Rows"},
            },
        }
    if chart.kind is ChartKind.BAR:
        return base | {
            "mark": "bar",
            "encoding": {
                "y": {"field": "category", "type": "nominal", "sort": "-x", "title": x_title},
                "x": {"field": "value", "type": "quantitative", "title": y_title},
                **color,
            },
        }
    if chart.kind is ChartKind.LINE:
        return base | {
            "mark": {"type": "line", "point": True},
            "encoding": {
                "x": {"field": "period", "type": "temporal", "title": x_title},
                "y": {"field": "value", "type": "quantitative", "title": y_title},
                **color,
            },
        }
    if chart.kind is ChartKind.SCATTER:
        return base | {
            "mark": {"type": "point", "opacity": 0.6},
            "encoding": {
                "x": {"field": "x", "type": "quantitative", "title": x_title},
                "y": {"field": "y", "type": "quantitative", "title": y_title},
                **color,
            },
        }
    if chart.kind is ChartKind.BOX:
        return base | {
            "mark": "boxplot",
            "encoding": {
                "x": {"field": "x", "type": "nominal", "title": x_title},
                "y": {"field": "y", "type": "quantitative", "title": y_title},
            },
        }
    return base | {
        "mark": "rect",
        "encoding": {
            "x": {"field": "x", "type": "nominal", "title": x_title},
            "y": {"field": "y", "type": "nominal", "title": y_title},
            "color": {"field": "n", "type": "quantitative", "title": "Rows"},
        },
    }
