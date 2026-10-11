"""Phase B, stage ``visualize``: charts and maps checked against the data before anyone sees them.

1. Every chart a miner drew is checked point by point (``engine.analysis.charts``): each claim
   is recomputed from the analysis table with its own query. A chart with any value that does
   not match is withheld; its answer keeps its words and facts.
2. Every resolved place gets its maps (``engine.analysis.regions``): rows per region and, with
   a numeric outcome, the outcome's average per region, small regions hatched. Maps are checked
   the same way.

Nothing here is written by a model, and nothing is stored but the checked charts and maps.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from insightfulcell.engine.analysis.charts import (
    RELATIVE_TOLERANCE,
    check_chart,
    check_map,
    map_with_check,
    with_check,
)
from insightfulcell.engine.analysis.facts import ChartData, MapData
from insightfulcell.engine.analysis.miners import Derived
from insightfulcell.engine.analysis.regions import place_maps
from insightfulcell.engine.geo.maps import TOLERANCE
from insightfulcell.engine.geo.reference import REFERENCE_SET
from insightfulcell.engine.planning.language import ResolvedPlan
from insightfulcell.engine.types import Meaning
from insightfulcell.pipeline.artifacts import fetch, fetch_json, put_json, table
from insightfulcell.pipeline.mine import findings_from_json, load_places
from insightfulcell.pipeline.stage import SourceFile, StageContext, StageResult

logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class Visuals:
    """The ``visualize`` artifact, read back by the report stage."""

    charts: dict[str, ChartData]  # analysis id -> verified chart
    withheld: frozenset[str]  # analysis ids whose chart failed its check
    maps: dict[str, list[MapData]]  # analysis id -> verified maps
    claims: int
    maps_withheld: int


class VisualizeStage:
    name = "visualize"
    version = "1"

    def fingerprint(self, source: SourceFile) -> Mapping[str, Any]:
        return {
            "tolerance": RELATIVE_TOLERANCE,
            "reference_set": REFERENCE_SET,
            "outline_tolerance": TOLERANCE,
        }

    async def run(self, ctx: StageContext) -> StageResult:
        found = await fetch_json(ctx, "mine", "findings")
        findings = findings_from_json(found)
        analysis = await fetch(ctx, "mine", "table", "analysis.parquet")
        plan = ResolvedPlan.model_validate(await fetch_json(ctx, "clean_apply", "plan"))
        places = await load_places(ctx)
        derived = derived_from_json(found.get("derived", []))

        def compute() -> dict[str, Any]:
            charts: dict[str, Any] = {}
            withheld: list[str] = []
            claims = 0
            with table(analysis, name="a", threads=1) as con:
                for finding in findings:
                    if finding.chart is None:
                        continue
                    verdict = check_chart(con, finding.chart)
                    claims += verdict.claims
                    if verdict.verified:
                        chart = with_check(finding.chart, verdict)
                        charts[finding.analysis.id] = chart.model_dump(mode="json")
                    else:
                        withheld.append(finding.analysis.id)
                        logger.warning(
                            "chart withheld: a value did not match the data",
                            extra={"kind": finding.analysis.kind.value,
                                   "claim": verdict.first_failure},
                        )  # fmt: skip
                maps: list[dict[str, Any]] = []
                maps_withheld = 0
                for placed in place_maps(con, plan, places, derived):
                    verdict = check_map(con, placed.map)
                    claims += verdict.claims
                    if verdict.verified:
                        maps.append(
                            {
                                "analysis_id": placed.analysis_id,
                                "map": map_with_check(placed.map, verdict).model_dump(mode="json"),
                            }
                        )
                    else:
                        maps_withheld += 1
                        logger.warning(
                            "map withheld: a value did not match the data",
                            extra={"claim": verdict.first_failure},
                        )
            return {
                "charts": charts,
                "withheld": withheld,
                "maps": maps,
                "claims": claims,
                "maps_withheld": maps_withheld,
            }

        result = await asyncio.to_thread(compute)
        key = await put_json(ctx, self.name, "visuals.json", result)
        return StageResult(
            output={
                "charts_verified": len(result["charts"]),
                "charts_withheld": len(result["withheld"]),
                "maps": len(result["maps"]),
                "maps_withheld": result["maps_withheld"],
                "claims_checked": result["claims"],
            },
            artifacts={"visuals": key},
        )


def derived_from_json(rows: list[Mapping[str, Any]]) -> list[Derived]:
    return [
        Derived(
            name=str(d["name"]),
            label=str(d["label"]),
            meaning=Meaning(d["meaning"]),
            replaces=d.get("replaces"),
            origin=tuple(d.get("origin") or ()),
        )
        for d in rows
    ]


def visuals_from_json(data: Mapping[str, Any]) -> Visuals:
    maps: dict[str, list[MapData]] = {}
    for item in data.get("maps", []):
        maps.setdefault(str(item["analysis_id"]), []).append(MapData.model_validate(item["map"]))
    return Visuals(
        charts={k: ChartData.model_validate(v) for k, v in data.get("charts", {}).items()},
        withheld=frozenset(data.get("withheld", [])),
        maps=maps,
        claims=int(data.get("claims", 0)),
        maps_withheld=int(data.get("maps_withheld", 0)),
    )
