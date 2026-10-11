"""Phase 6: every chart a miner draws carries claims, and the ``visualize`` check recomputes each
of them from the data; a chart with a wrong value is rejected."""

from __future__ import annotations

import random
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import date, timedelta

import duckdb
import pytest

from insightfulcell.engine.analysis import table as analysis_table
from insightfulcell.engine.analysis.charts import check_chart
from insightfulcell.engine.analysis.claims import GroupClaim, PointClaim, ShareClaim
from insightfulcell.engine.analysis.facts import ChartData
from insightfulcell.engine.analysis.miners import Finding, mine
from insightfulcell.engine.analysis.places import place_findings
from insightfulcell.engine.geo.resolve import resolve_plan
from insightfulcell.engine.planning.language import (
    MinerKind,
    ResolvedTarget,
    Role,
    Source,
    TargetKind,
)
from insightfulcell.engine.types import Meaning
from tests.unit.engine_support import table
from tests.unit.test_engine_analysis import column, plan_with

COLUMNS = [
    ("state", "VARCHAR"),
    ("segment", "VARCHAR"),
    ("member", "VARCHAR"),
    ("sales", "DOUBLE"),
    ("visits", "DOUBLE"),
    ("discount", "DOUBLE"),
    ("ordered", "DATE"),
    ("tier", "VARCHAR"),
]
STATES = ["NSW", "New South Wales", "VIC", "Victoria", "QLD", "WA", "Tasmania"]


def _rows(n: int = 900) -> list[tuple[object, ...]]:
    rnd = random.Random(42)
    start = date(2024, 1, 1)
    rows: list[tuple[object, ...]] = []
    for i in range(n):
        state = rnd.choice(STATES) if i % 50 else "Tasmania"
        segment = rnd.choice(["Retail", "Retail", "Online", "Wholesale"])
        # A few tiny groups: pooled as "Other groups" or left out.
        if i % 97 == 0:
            segment = f"Pop-up {i}"
        member = rnd.choice(["yes", "no", "no"]) if i % 23 else None
        visits = rnd.gauss(10, 3)
        sales = 100 + 8 * visits + (40 if segment == "Online" else 0) + rnd.gauss(0, 15)
        if i % 61 == 0:
            sales = 5000.0  # an outlier
        discount = None if i % 11 == 0 else rnd.random()
        tier = rnd.choice(["gold", "silver", "bronze", " ", "silver"])
        rows.append(
            (state, segment, member, sales, visits, discount, start + timedelta(days=i % 700), tier)
        )
    return rows


def _dictionary() -> list[object]:
    return [
        column("state", Meaning.NOMINAL).model_copy(
            update={"meaning": Meaning.GEOGRAPHY, "role": Role.GEOGRAPHY}
        ),
        column("segment", Meaning.NOMINAL),
        column("member", Meaning.NOMINAL),
        column("sales", Meaning.CURRENCY, unit="$"),
        column("visits", Meaning.CONTINUOUS),
        column("discount", Meaning.CONTINUOUS),
        column("ordered", Meaning.DATETIME),
        column("tier", Meaning.NOMINAL),
    ]


ANALYSES = [
    (MinerKind.DISTRIBUTION, ("sales",)),
    (MinerKind.DISTRIBUTION, ("ordered",)),
    (MinerKind.DISTRIBUTION, ("segment",)),
    (MinerKind.DISTRIBUTION, ("tier",)),
    (MinerKind.RELATIONSHIP, ("visits", "sales")),
    (MinerKind.RELATIONSHIP, ("segment", "tier")),
    (MinerKind.TREND, ("ordered", "sales")),
    (MinerKind.SEGMENT, ("segment", "sales")),
    (MinerKind.SEGMENT, ("state", "sales")),
    (MinerKind.SEGMENT, ("segment", "member")),
    (MinerKind.BENCHMARK, ("segment", "sales")),
    (MinerKind.ANOMALY, ("sales", "visits")),
    (MinerKind.ANOMALY, ("discount",)),
    (MinerKind.MISSING_AS_SIGNAL, ("discount", "sales")),
    (MinerKind.MISSING_AS_SIGNAL, ("member", "sales")),
    (MinerKind.CONDITIONAL, ("visits", "sales", "segment")),
    (MinerKind.DRIVER, ("sales",)),
]


@contextmanager
def mined(
    target: str | None = "sales",
) -> Iterator[tuple[duckdb.DuckDBPyConnection, list[Finding]]]:
    plan = plan_with(_dictionary(), *ANALYSES)  # type: ignore[arg-type]
    if target:
        plan = plan.model_copy(
            update={
                "target": ResolvedTarget(
                    column=target, kind=TargetKind.NUMERIC, rationale="", source=Source.RULES
                )
            }
        )
    with table(COLUMNS, _rows()) as con:
        con.execute("CREATE VIEW cleaned AS SELECT * FROM t")
        places = resolve_plan(con, "cleaned", plan)
        derived = analysis_table.build(
            con, source="cleaned", resolutions=places, features=[], labels={"state": "State"}
        )
        extra, notes = place_findings(plan, places, derived)
        result = mine(con, plan, seed="charts", derived=derived, extra=extra, notes=notes)
        yield con, result.findings


Scene = tuple[duckdb.DuckDBPyConnection, list[Finding]]


@pytest.fixture(scope="module")
def scene() -> Iterator[Scene]:
    """Mined once for the module: the checks only read the table."""
    with mined() as found:
        yield found


def _charts(findings: list[Finding]) -> list[tuple[str, ChartData]]:
    return [(f.analysis.id, f.chart) for f in findings if f.chart is not None]


def test_every_chart_the_miners_draw_is_verified_point_by_point(scene: Scene) -> None:
    con, findings = scene
    charts = _charts(findings)
    kinds = {chart.kind for _, chart in charts}
    assert kinds == {"bar", "line", "scatter", "histogram"}
    assert len(charts) >= 15
    for analysis_id, chart in charts:
        verdict = check_chart(con, chart)
        assert verdict.verified, (analysis_id, verdict)
        assert verdict.claims == len(chart.points)
        claim_types = {type(p.claim).__name__ for p in chart.points}
        assert "NoneType" not in claim_types


def test_claims_cover_pooled_groups_regions_and_every_miner(scene: Scene) -> None:
    _, findings = scene
    claims = [p.claim for _, c in _charts(findings) for p in c.points]
    types = {c.type for c in claims if c is not None}
    assert types == {
        "group", "correlation", "share", "bin", "period", "point", "outliers", "missing",
    }  # fmt: skip
    pooled = [c for c in claims if isinstance(c, GroupClaim) and c.key is None]
    assert pooled, "the tiny pop-up segments are pooled into one bar"
    regions = [c for c in claims if isinstance(c, GroupClaim) and c.group.startswith("region__")]
    assert {c.key for c in regions} >= {"New South Wales", "Victoria"}


def test_explorable_bars_name_their_group_and_column(scene: Scene) -> None:
    _, findings = scene
    by_id = dict(_charts(findings))
    segment = by_id["segment:segment,sales"]
    assert segment.drill_column == "segment"
    keys = [p.key for p in segment.points]
    assert "Online" in keys
    assert None in keys  # the pooled bar cannot be explored on its own
    states = by_id["segment:state,sales"]
    assert states.drill_column == "state"  # the plan column, read as standard regions
    assert {p.key for p in states.points} >= {"New South Wales", "Victoria"}
    # Bars that are not groups of one column are not explorable.
    assert by_id["anomaly:sales,visits"].drill_column is None
    assert by_id["missing_as_signal:discount,sales"].drill_column is None


@pytest.mark.parametrize(
    ("target", "kind"), [("churned", TargetKind.BINARY), ("spend", TargetKind.NUMERIC)]
)
def test_driver_strengths_are_recomputed_for_measures_and_groups(
    target: str, kind: TargetKind
) -> None:
    """Drivers: rank correlations for measures, eta for groups (with a yes/no outcome as an
    indicator), each recomputed on its own."""
    rnd = random.Random(3)
    rows = []
    for _ in range(800):
        spend, tenure = rnd.gauss(50, 10), rnd.gauss(24, 6)
        region = rnd.choice(["N", "S", "E"])
        spend += 15 if region == "N" else 0
        score = -(tenure - 24) / 6 + (spend - 55) / 10 + rnd.gauss(0, 0.5)
        rows.append((spend, tenure, rnd.gauss(0, 1), region, "yes" if score > 0.8 else "no"))
    dictionary = [
        column("spend", Meaning.CONTINUOUS),
        column("tenure", Meaning.CONTINUOUS),
        column("noise", Meaning.CONTINUOUS),
        column("region", Meaning.NOMINAL),
        column("churned", Meaning.NOMINAL),
    ]
    plan = plan_with(dictionary, (MinerKind.DRIVER, (target,))).model_copy(
        update={
            "target": ResolvedTarget(column=target, kind=kind, rationale="", source=Source.RULES)
        }
    )
    cols = [("spend", "DOUBLE"), ("tenure", "DOUBLE"), ("noise", "DOUBLE"),
            ("region", "VARCHAR"), ("churned", "VARCHAR")]  # fmt: skip
    with table(cols, rows) as con:
        con.execute("CREATE VIEW a AS SELECT * FROM t")
        (finding,) = mine(con, plan, seed="drivers").findings
        assert finding.chart is not None
        verdict = check_chart(con, finding.chart)
    assert verdict.verified, verdict
    types = {p.claim.type for p in finding.chart.points if p.claim is not None}
    assert "eta" in types
    if kind is TargetKind.NUMERIC:
        assert "correlation" in types


@pytest.mark.parametrize("change", ["value", "n", "label_swap", "claim_column", "point"])
def test_a_chart_with_one_wrong_value_is_rejected(change: str, scene: Scene) -> None:
    con, findings = scene
    by_id = dict(_charts(findings))
    if change == "point":
        chart = by_id["relationship:visits,sales"]
        first = chart.points[0]
        assert isinstance(first.claim, PointClaim)
        bad = first.model_copy(update={"value": first.value + 0.5})
    else:
        chart = by_id["segment:segment,sales"]
        first = chart.points[0]
        if change == "value":
            bad = first.model_copy(update={"value": first.value * 1.001})
        elif change == "n":
            bad = first.model_copy(update={"n": (first.n or 0) + 1})
        elif change == "label_swap":
            other = chart.points[1]
            bad = first.model_copy(update={"value": other.value})
        else:
            assert isinstance(first.claim, GroupClaim)
            bad = first.model_copy(
                update={"claim": first.claim.model_copy(update={"group": "tier"})}
            )
    tampered = chart.model_copy(update={"points": (bad, *chart.points[1:])})
    verdict = check_chart(con, tampered)
    assert not verdict.verified
    assert verdict.failed == 1


def test_a_chart_without_claims_is_not_verified(scene: Scene) -> None:
    con, findings = scene
    _, chart = _charts(findings)[0]
    bare = chart.model_copy(
        update={"points": tuple(p.model_copy(update={"claim": None}) for p in chart.points)}
    )
    assert not check_chart(con, bare).verified
    assert not check_chart(con, chart.model_copy(update={"points": ()})).verified


def test_a_claim_on_a_missing_column_fails_instead_of_raising(scene: Scene) -> None:
    con, findings = scene
    chart = dict(_charts(findings))["distribution:segment"]
    first = chart.points[0]
    assert isinstance(first.claim, ShareClaim)
    broken = first.model_copy(
        update={"claim": first.claim.model_copy(update={"column": "no_such_column"})}
    )
    verdict = check_chart(con, chart.model_copy(update={"points": (broken,)}))
    assert not verdict.verified
    assert verdict.first_failure == "share"
