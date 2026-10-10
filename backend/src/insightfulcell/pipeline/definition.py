"""Which stages run in which phase.

A run executes as two jobs split at the review gate.

- Phase A: ``ingest`` -> ``profile`` -> ``understand`` -> ``clean_plan``. It ends at the review
  gate, where a person checks the plan and the cleaning, may ask for changes (Phase A then
  runs again, reusing what did not change), and approves.
- Phase B: ``clean_apply`` -> ``geo`` -> ``features`` -> ``mine`` -> ``sensitivity`` ->
  ``rank`` -> ``report``: the approved cleaning applied, places resolved to regions, features
  kept only if they pass the signal gate, every question answered with tested facts under the
  run's false-discovery control (facts and test log stored), the top findings re-tested on
  minimally cleaned data, the findings ranked, and the report written with citations. The
  baseline's narrate, verify, visualize and assemble are inside ``report`` until Phases 5
  and 6 split them out.
"""

from __future__ import annotations

import hashlib
from collections.abc import Iterator
from dataclasses import dataclass

from insightfulcell import __version__
from insightfulcell.domain.runs import Phase
from insightfulcell.ingestion.limits import IngestLimits
from insightfulcell.pipeline.clean_apply import CleanApplyStage
from insightfulcell.pipeline.clean_plan import CleanPlanStage
from insightfulcell.pipeline.features import FeaturesStage
from insightfulcell.pipeline.geo import GeoStage
from insightfulcell.pipeline.ingest import IngestStage
from insightfulcell.pipeline.mine import MineStage
from insightfulcell.pipeline.profile import ProfileStage
from insightfulcell.pipeline.rank import RankStage
from insightfulcell.pipeline.report import ReportStage
from insightfulcell.pipeline.sensitivity import SensitivityStage
from insightfulcell.pipeline.stage import Stage
from insightfulcell.pipeline.understand import UnderstandStage


@dataclass(frozen=True, slots=True)
class PlannedStage:
    phase: Phase
    position: int
    stage: Stage


@dataclass(frozen=True, slots=True)
class Pipeline:
    phase_a: tuple[Stage, ...]
    phase_b: tuple[Stage, ...] = ()

    def __post_init__(self) -> None:
        names = [stage.name for stage in (*self.phase_a, *self.phase_b)]
        if len(names) != len(set(names)):
            raise ValueError("stage names must be unique across both phases")

    def stages(self, phase: Phase) -> tuple[Stage, ...]:
        return self.phase_a if phase is Phase.A else self.phase_b

    def planned(self) -> Iterator[PlannedStage]:
        """Every stage in execution order, with its position across the whole run."""
        position = 0
        for phase in (Phase.A, Phase.B):
            for stage in self.stages(phase):
                yield PlannedStage(phase, position, stage)
                position += 1

    @property
    def code_version(self) -> str:
        """Package version plus a digest of the stage list, recorded on every run."""
        stages = ",".join(f"{p.phase}:{p.stage.name}@{p.stage.version}" for p in self.planned())
        return f"{__version__}+{hashlib.sha256(stages.encode()).hexdigest()[:12]}"


def default_pipeline(
    limits: IngestLimits | None = None,
    *,
    card_token_budget: int = 9_000,
    model_identity: tuple[str, str] | None = None,
) -> Pipeline:
    return Pipeline(
        phase_a=(
            IngestStage(limits),
            ProfileStage(),
            UnderstandStage(card_token_budget=card_token_budget, model_identity=model_identity),
            CleanPlanStage(),
        ),
        phase_b=(
            CleanApplyStage(),
            GeoStage(),
            FeaturesStage(),
            MineStage(),
            SensitivityStage(),
            RankStage(),
            ReportStage(model_identity=model_identity),
        ),
    )
