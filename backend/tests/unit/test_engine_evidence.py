"""Phase 4's evidence: the statistical tests, false-discovery control, the planted-effect
evaluation, driver skill and leakage, the signal gate, places, the sensitivity check and
ranking."""

from __future__ import annotations

import gzip
import hashlib
import random
from importlib import resources
from pathlib import Path

import numpy as np
import pytest
from scipy import stats as sps

from insightfulcell.engine.analysis import rank as ranking
from insightfulcell.engine.analysis import sensitivity, signal, stats
from insightfulcell.engine.analysis import table as analysis_table
from insightfulcell.engine.analysis.facts import Fact, FactTest, Tier
from insightfulcell.engine.analysis.miners import Finding, LoggedTest, Mined, mine
from insightfulcell.engine.analysis.places import place_findings
from insightfulcell.engine.geo import reference
from insightfulcell.engine.geo.resolve import (
    au_postcode_state,
    coordinate_pairs,
    from_json,
    resolve_names,
    resolve_plan,
    resolve_points,
    to_json,
)
from insightfulcell.engine.planning.language import (
    DatePart,
    FeatureKind,
    MinerKind,
    ResolvedAnalysis,
    ResolvedFeature,
    ResolvedTarget,
    Role,
    Source,
    TargetKind,
)
from insightfulcell.engine.types import Meaning
from tests.unit.engine_support import table
from tests.unit.test_engine_analysis import column, plan_with

MIGRATION = Path(__file__).resolve().parents[2] / "migrations/versions/0007_evidence_and_places.py"


# --------------------------------------------------------------------------- tests and FDR


def test_spearman_matches_scipy_and_its_interval_holds_the_estimate() -> None:
    rng = np.random.default_rng(1)
    x = rng.normal(size=200)
    y = 0.5 * x + rng.normal(size=200)
    test = stats.spearman("h", ("x", "y"), x, y)
    assert test is not None
    expected = sps.spearmanr(x, y)
    assert test.effect == pytest.approx(float(expected.statistic))
    assert test.p_value == pytest.approx(float(expected.pvalue))
    assert test.ci_low is not None
    assert test.ci_high is not None
    assert test.ci_low < test.effect < test.ci_high
    assert test.size == pytest.approx(abs(test.effect))
    # A constant column cannot be correlated with anything.
    assert stats.spearman("h", ("x", "y"), np.ones(20), y[:20]) is None


def test_welch_matches_scipy() -> None:
    rng = np.random.default_rng(2)
    a, b = rng.normal(1.0, 1.0, 40), rng.normal(0.0, 3.0, 25)
    test = stats.mean_difference("h", ("a", "b"), a, b)
    assert test is not None
    expected = sps.ttest_ind(a, b, equal_var=False)
    assert test.p_value == pytest.approx(float(expected.pvalue))
    assert test.effect == pytest.approx(a.mean() - b.mean())
    assert test.ci_low is not None
    assert test.ci_high is not None
    assert test.ci_low < test.effect < test.ci_high


def test_share_difference_and_its_interval() -> None:
    test = stats.proportion_difference("h", ("a", "b"), (60, 30), (100, 100))
    assert test is not None
    assert test.effect == pytest.approx(0.3)
    assert test.p_value < 0.001
    assert test.ci_low is not None
    assert test.ci_high is not None
    assert 0.15 < test.ci_low < 0.3 < test.ci_high < 0.45


def test_benjamini_hochberg_matches_the_textbook_procedure() -> None:
    ps = [0.01, 0.04, 0.03, 0.005, 0.5]
    tests = [
        stats.TestResult(f"h{i}", "m", ("a",), 10, "e", 0.0, None, None, p)
        for i, p in enumerate(ps)
    ]
    controlled, threshold = stats.benjamini_hochberg(tests)
    # Sorted p: 0.005, 0.01, 0.03, 0.04, 0.5; p*m/rank: 0.025, 0.025, 0.05, 0.05, 0.5.
    assert [t.q_value for t in controlled] == pytest.approx([0.025, 0.05, 0.05, 0.025, 0.5])
    assert [t.passed for t in controlled] == [True, True, True, True, False]
    assert threshold == pytest.approx(0.04)
    assert stats.benjamini_hochberg([]) == ([], 0.0)


def test_mutual_information_gate_keeps_signal_and_drops_noise() -> None:
    rng = np.random.default_rng(3)
    y = rng.integers(0, 2, 2000)
    informative = np.where(rng.random(2000) < 0.8, y, 1 - y)
    noise = rng.integers(0, 5, 2000)
    assert stats.signal_gate(informative, y, np.random.default_rng(4))[2] is True
    assert stats.signal_gate(noise, y, np.random.default_rng(4))[2] is False
    # Bias correction: independent variables measure close to zero, not above it.
    assert abs(stats.mutual_information(noise, y)) < 0.005


def test_seeds_differ_by_part_and_repeat_by_value() -> None:
    assert stats.seed_for("run", "a") == stats.seed_for("run", "a")
    assert stats.seed_for("run", "a") != stats.seed_for("run", "b")
    assert stats.seed_for("ab", "c") != stats.seed_for("a", "bc")


# --------------------------------------------------------------------------- planted effects


def _relationships(names: list[str]) -> list[tuple[MinerKind, tuple[str, ...]]]:
    return [(MinerKind.RELATIONSHIP, (a, b)) for i, a in enumerate(names) for b in names[i + 1 :]]


def test_planted_effects_are_found_and_noise_is_not() -> None:
    """The evaluation the baseline asks for: plant effects among many null hypotheses; every
    planted one must be found, and the share of false discoveries stays small."""
    rnd = random.Random(7)
    rows = []
    for _ in range(600):
        base = rnd.gauss(0, 1)
        planted = 0.5 * base + rnd.gauss(0, 1)
        group = rnd.choice(["A", "B", "C"])
        lifted = rnd.gauss(0, 1) + (0.8 if group == "B" else 0.0)
        rows.append((base, planted, group, lifted, *(rnd.gauss(0, 1) for _ in range(6))))
    noise = [f"n{i}" for i in range(6)]
    columns = [("base", "DOUBLE"), ("planted", "DOUBLE"), ("group", "VARCHAR"),
               ("lifted", "DOUBLE"), *((n, "DOUBLE") for n in noise)]  # fmt: skip
    analyses = [
        (MinerKind.RELATIONSHIP, ("base", "planted")),
        (MinerKind.SEGMENT, ("group", "lifted")),
        *_relationships(["base", *noise]),
        *((MinerKind.SEGMENT, ("group", n)) for n in noise),
    ]
    dictionary = [
        column("base", Meaning.CONTINUOUS),
        column("planted", Meaning.CONTINUOUS),
        column("group", Meaning.NOMINAL),
        column("lifted", Meaning.CONTINUOUS),
        *(column(n, Meaning.CONTINUOUS) for n in noise),
    ]
    with table(columns, rows) as con:
        con.execute("CREATE VIEW a AS SELECT * FROM t")
        result = mine(con, plan_with(dictionary, *analyses), seed="evaluation")
    by_analysis = {f.analysis.id: f for f in result.findings}
    assert by_analysis["relationship:base,planted"].facts[0].tier == "strong"
    assert by_analysis["segment:group,lifted"].facts[0].tier == "strong"
    planted_tests = {"correlation:base|planted", "groups:group|lifted"}
    false = [t for t in result.tests if t.test.passed and t.test.hypothesis not in planted_tests]
    assert len(result.tests) == len(analyses)
    assert len(false) <= 1  # 5% of 35 null hypotheses would be under 2 on average


def test_with_no_effect_at_all_nothing_is_reported_as_a_finding() -> None:
    rnd = random.Random(11)
    names = [f"c{i}" for i in range(8)]
    rows = [tuple(rnd.gauss(0, 1) for _ in names) for _ in range(300)]
    with table([(n, "DOUBLE") for n in names], rows) as con:
        con.execute("CREATE VIEW a AS SELECT * FROM t")
        plan = plan_with([column(n, Meaning.CONTINUOUS) for n in names], *_relationships(names))
        result = mine(con, plan, seed="null")
    assert len(result.tests) == 28
    assert not [t for t in result.tests if t.test.passed]
    assert result.threshold == 0.0
    for finding in result.findings:
        (fact,) = finding.facts
        assert fact.tier == "inconclusive"
        assert "no reliable link" in fact.statement


def test_the_same_comparison_asked_twice_is_one_hypothesis() -> None:
    rows = [(g, float(i % 7) + (5 if g == "B" else 0)) for i, g in enumerate("AB" * 30)]
    with table([("g", "VARCHAR"), ("v", "DOUBLE")], rows) as con:
        con.execute("CREATE VIEW a AS SELECT * FROM t")
        plan = plan_with(
            [column("g", Meaning.NOMINAL), column("v", Meaning.CONTINUOUS)],
            (MinerKind.SEGMENT, ("g", "v")),
            (MinerKind.BENCHMARK, ("g", "v")),
        )
        result = mine(con, plan, seed="s")
    (logged,) = result.tests
    assert logged.test.hypothesis == "groups:g|v"
    assert logged.fact_id == "F1"
    segment, benchmark = (f.facts[0] for f in result.findings)
    assert segment.test == benchmark.test


def test_mining_is_deterministic_for_a_run() -> None:
    rnd = random.Random(5)
    rows = [(rnd.choice("XYZ"), rnd.gauss(0, 1), rnd.gauss(0, 1)) for _ in range(400)]
    cols = [("g", "VARCHAR"), ("a", "DOUBLE"), ("b", "DOUBLE")]
    plan = plan_with(
        [column("g", Meaning.NOMINAL), column("a", Meaning.CONTINUOUS),
         column("b", Meaning.CONTINUOUS)],
        (MinerKind.SEGMENT, ("g", "a")), (MinerKind.RELATIONSHIP, ("g", "b")),
        (MinerKind.CONDITIONAL, ("a", "b", "g")), (MinerKind.RELATIONSHIP, ("a", "b")),
    )  # fmt: skip
    hashes = []
    for _ in range(2):
        with table(cols, rows) as con:
            con.execute("CREATE VIEW a AS SELECT * FROM t")
            result = mine(con, plan, seed="same-run")
        hashes.append([f.content_hash() for x in result.findings for f in x.facts])
    assert hashes[0] == hashes[1]


# --------------------------------------------------------------------------- drivers


def _driver_plan(target: str, kind: TargetKind) -> object:
    dictionary = [
        column("spend", Meaning.CONTINUOUS),
        column("tenure", Meaning.CONTINUOUS),
        column("noise", Meaning.CONTINUOUS),
        column("region", Meaning.NOMINAL),
        column("churned", Meaning.NOMINAL),
    ]
    plan = plan_with(dictionary, (MinerKind.DRIVER, (target,)))
    return plan.model_copy(
        update={
            "target": ResolvedTarget(column=target, kind=kind, rationale="", source=Source.RULES)
        }
    )


def _customers(signal: bool) -> list[tuple[object, ...]]:
    rnd = random.Random(3)
    rows: list[tuple[object, ...]] = []
    for _ in range(800):
        spend, tenure = rnd.gauss(50, 10), rnd.gauss(24, 6)
        score = (-(tenure - 24) / 6 + rnd.gauss(0, 0.7)) if signal else rnd.gauss(0, 1)
        rows.append(
            (spend, tenure, rnd.gauss(0, 1), rnd.choice(["N", "S"]), "yes" if score > 0.8 else "no")
        )
    return rows


DRIVER_COLUMNS = [("spend", "DOUBLE"), ("tenure", "DOUBLE"), ("noise", "DOUBLE"),
                  ("region", "VARCHAR"), ("churned", "VARCHAR")]  # fmt: skip


def test_drivers_are_reported_only_after_the_model_shows_skill_on_held_out_rows() -> None:
    with table(DRIVER_COLUMNS, _customers(signal=True)) as con:
        con.execute("CREATE VIEW a AS SELECT * FROM t")
        (finding,) = mine(con, _driver_plan("churned", TargetKind.BINARY), seed="d").findings  # type: ignore[arg-type]
    model = next(f for f in finding.facts if f.kind == "driver_model")
    assert model.values["skilful"] is True
    assert "area under the ROC curve" in model.statement
    drivers = [f for f in finding.facts if f.kind == "driver"]
    assert drivers[0].columns[0] == "tenure"
    assert all(f.tier in {"strong", "supported"} for f in drivers)
    assert "noise" not in {f.columns[0] for f in drivers}


def test_without_skill_no_driver_is_reported() -> None:
    with table(DRIVER_COLUMNS, _customers(signal=False)) as con:
        con.execute("CREATE VIEW a AS SELECT * FROM t")
        (finding,) = mine(con, _driver_plan("churned", TargetKind.BINARY), seed="d").findings  # type: ignore[arg-type]
    (model,) = finding.facts
    assert model.values["skilful"] is False
    assert "do not predict" in model.statement


# --------------------------------------------------------------------------- places


def test_the_packaged_reference_set_matches_its_pinned_digest_and_catalogue() -> None:
    raw = resources.files("insightfulcell.engine.geo").joinpath("data", reference.REFERENCE_FILE)
    digest = hashlib.sha256(raw.read_bytes()).hexdigest()
    assert digest == reference.REFERENCE_SHA256
    assert reference.REFERENCE_SHA256 in MIGRATION.read_text()
    assert gzip.decompress(raw.read_bytes())  # a valid archive
    gazetteer = reference.load()
    assert gazetteer.region("AU-NSW") is not None
    assert {s.key for s in gazetteer.scopes()} >= {"country", "state:AU", "state:US"}


@pytest.mark.parametrize(
    ("value", "expected"),
    [("N.S.W.", "nsw"), ("Côte d'Ivoire", "cote divoire"), ("  The  Gambia ", "gambia"),
     ("State of Victoria", "victoria"), ("Trinidad & Tobago", "trinidad and tobago")],
)  # fmt: skip
def test_place_names_are_compared_without_case_accents_or_punctuation(
    value: str, expected: str
) -> None:
    assert reference.normalise(value) == expected


@pytest.mark.parametrize(
    ("values", "scope", "codes"),
    [
        (["NSW", "Vic", "WA", "SA", "Queensand"], "state:AU",
         {"WA": "AU-WA", "SA": "AU-SA", "Queensand": "AU-QLD"}),
        (["CA", "WA", "GA", "Georgia", "Washington"], "state:US",
         {"CA": "US-CA", "WA": "US-WA", "Georgia": "US-GA", "Washington": "US-WA"}),
        (["Georgia", "France", "UK", "USA", "Autralia"], "country",
         {"Georgia": "GE", "UK": "GB", "USA": "US", "Autralia": "AU"}),
    ],
)  # fmt: skip
def test_a_column_is_resolved_in_the_scope_most_of_its_values_match(
    values: list[str], scope: str, codes: dict[str, str]
) -> None:
    with table([("place", "VARCHAR")], [(v,) for v in values * 3]) as con:
        found = resolve_names(con, "t", "place")
    assert found is not None
    assert (found.scope, found.resolved) == (scope, True)
    assert codes.items() <= dict(found.mapping).items()


def test_suburbs_are_not_guessed_and_the_column_is_not_resolved() -> None:
    values = ["Richmond", "Carlton", "Brunswick", "Fitzroy", "NSW"]
    with table([("suburb", "VARCHAR")], [(v,) for v in values * 4]) as con:
        found = resolve_names(con, "t", "suburb")
    assert found is not None
    assert not found.resolved
    assert found.mapping == ()
    assert {v for v, _ in found.unmatched} == {"Richmond", "Carlton", "Brunswick", "Fitzroy"}


def test_australian_postcodes_follow_australia_post_ranges() -> None:
    assert [au_postcode_state(v) for v in ("2000", "0800", "800", "2600", "3000", "7001",
                                           "2000.0", "12", "abcd")] == [
        "AU-NSW", "AU-NT", "AU-NT", "AU-ACT", "AU-VIC", "AU-TAS", "AU-NSW", None, None,
    ]  # fmt: skip


def test_coordinates_land_in_states_with_a_margin_for_simplified_coasts() -> None:
    cities = [(-33.87, 151.21), (-37.81, 144.96), (-27.47, 153.03), (-31.95, 115.86),
              (-42.88, 147.33), (-12.46, 130.84), (-35.28, 149.13)]  # fmt: skip
    with table([("latitude", "DOUBLE"), ("longitude", "DOUBLE")], cities * 3) as con:
        found = resolve_points(con, "t", "latitude", "longitude")
    assert found is not None
    assert (found.scope, found.resolved, found.matched_rows) == ("state:AU", True, 21)
    assert {code for _, _, code in found.points} == {
        "AU-NSW", "AU-VIC", "AU-QLD", "AU-WA", "AU-TAS", "AU-NT", "AU-ACT",
    }  # fmt: skip
    assert from_json(to_json(found)) == found
    with table([("latitude", "DOUBLE"), ("longitude", "DOUBLE")], [(0.0, -30.0)] * 10) as con:
        at_sea = resolve_points(con, "t", "latitude", "longitude")
    assert at_sea is not None
    assert not at_sea.resolved


def test_coordinate_columns_are_paired_by_name() -> None:
    assert coordinate_pairs(["pickup_lat", "pickup_lon", "drop_lat", "drop_lng", "x"]) == [
        ("pickup_lat", "pickup_lon"),
        ("drop_lat", "drop_lng"),
    ]
    assert coordinate_pairs(["Latitude", "Longitude"]) == [("Latitude", "Longitude")]


def test_regions_become_one_group_and_get_their_own_findings() -> None:
    rows = [(s, 10.0 + (5 if s in {"VIC", "Victoria"} else 0) + i % 3)
            for i, s in enumerate(["NSW", "New South Wales", "VIC", "Victoria"] * 15)]  # fmt: skip
    dictionary = [
        column("state", Meaning.NOMINAL).model_copy(
            update={"meaning": Meaning.GEOGRAPHY, "role": Role.GEOGRAPHY}
        ),
        column("sales", Meaning.CONTINUOUS),
    ]
    plan = plan_with(dictionary, (MinerKind.SEGMENT, ("state", "sales")))
    with table([("state", "VARCHAR"), ("sales", "DOUBLE")], rows) as con:
        con.execute("CREATE VIEW cleaned AS SELECT * FROM t")
        places = resolve_plan(con, "cleaned", plan)
        derived = analysis_table.build(
            con, source="cleaned", resolutions=places, features=[], labels={"state": "State"}
        )
        extra, notes = place_findings(plan, places, derived)
        result = mine(con, plan, seed="p", derived=derived, extra=extra, notes=notes)
        codes = con.execute(
            "SELECT DISTINCT region__state__code FROM a ORDER BY 1"
        ).fetchall()  # fmt: skip
    assert codes == [("AU-NSW",), ("AU-VIC",)]
    segment = result.findings[0].facts[0]
    assert segment.labels == ("Victoria", "New South Wales")  # four spellings, two groups
    place = next(f for f in result.findings if f.analysis.id == "places:state")
    assert place.facts[0].kind == "place_match"
    assert "4 of the 4 different State values (100%) match a state" in place.facts[0].statement


# --------------------------------------------------------------------------- signal gate


def _feature(kind: FeatureKind, columns: tuple[str, ...], part: DatePart | None = None) -> (
    ResolvedFeature
):  # fmt: skip
    from insightfulcell.engine.features import output_name  # noqa: PLC0415

    return ResolvedFeature(
        id=f"{kind.value}:{','.join(columns)}",
        kind=kind,
        columns=columns,
        part=part,
        name=output_name(kind, columns, part),
        reason="",
        source=Source.RULES,
    )


def test_the_gate_keeps_informative_features_and_drops_noise_copies_and_text() -> None:
    rnd = random.Random(9)
    rows = []
    for i in range(1500):
        amount, count = rnd.uniform(10, 100), rnd.randint(1, 10)
        per_item = amount / count
        rows.append(
            (amount, count, "a" * rnd.randint(1, 5), f"{i}", per_item * 2 + rnd.gauss(0, 3))
        )
    dictionary = [
        column("amount", Meaning.CONTINUOUS),
        column("items", Meaning.CONTINUOUS),
        column("note", Meaning.TEXT),
        column("code", Meaning.CONTINUOUS),
        column("value", Meaning.CONTINUOUS),
    ]
    plan = plan_with(dictionary).model_copy(
        update={
            "target": ResolvedTarget(
                column="value", kind=TargetKind.NUMERIC, rationale="", source=Source.RULES
            ),
            "features": (
                _feature(FeatureKind.RATIO, ("amount", "items")),
                _feature(FeatureKind.TEXT_LENGTH, ("note",)),
                _feature(FeatureKind.LOG, ("amount",)),
                _feature(FeatureKind.DATE_PART, ("code",), DatePart.MONTH),
            ),
        }
    )
    cols = [("amount", "DOUBLE"), ("items", "INTEGER"), ("note", "VARCHAR"), ("code", "VARCHAR"),
            ("value", "DOUBLE")]  # fmt: skip
    with table(cols, rows) as con:
        results = {r.kind: r for r in signal.gate(con, "t", plan, seed="g")}
    assert results["ratio"].outcome == "kept"
    assert results["ratio"].label == "Amount per Items"
    assert results["text_length"].outcome == "no_signal"
    assert results["log"].outcome == "redundant"  # a monotone copy of Amount
    assert results["log"].redundant_with == "Amount"
    assert results["date_part"].outcome == "not_computed"  # Code is text, not a date
    assert signal.from_json(signal.to_json(results["ratio"])) == results["ratio"]
    without = plan.model_copy(update={"target": None})
    with table(cols, rows[:50]) as con:
        assert {r.outcome for r in signal.gate(con, "t", without, seed="g")} == {"no_target"}


# --------------------------------------------------------------------------- sensitivity, rank


def _fact(fid: str, *, tier: Tier = "strong", q: float = 0.001, effect: float = 0.5,
          kind: str = "correlation", columns: tuple[str, ...] = ("a", "b"),
          hypothesis: str | None = None, labels: tuple[str, ...] = ()) -> Fact:  # fmt: skip
    test = None
    if tier != "descriptive":
        test = FactTest(
            hypothesis=hypothesis or f"h:{fid}",
            method="m",
            n=100,
            effect_name="spearman" if kind == "correlation" else "eta",
            effect=effect,
            ci_low=None,
            ci_high=None,
            p_value=q / 2,
            q_value=q,
            size=abs(effect),
        )
    return Fact(
        id=fid,
        analysis_id=f"a:{fid}",
        kind=kind,
        columns=columns,
        rows=100,
        method="m",
        statement=f"statement {fid}",
        numbers=(),
        labels=labels,
        tier=tier,
        test=test,
    )


def _retest(hypothesis: str, effect: float, p: float, fact: Fact | None = None) -> LoggedTest:
    result = stats.TestResult(hypothesis, "m", ("a", "b"), 90, "spearman", effect, None, None, p)
    return LoggedTest(result, fact.id if fact else None)


def test_sensitivity_says_whether_a_finding_holds_weakens_flips_or_was_not_retested() -> None:
    holds, weakens, flips, gone = (_fact(f"F{i}") for i in range(1, 5))
    groups = _fact("F5", kind="group_comparison", labels=("North", "South"))
    moved = groups.model_copy(update={"id": "F9", "labels": ("South", "North")})
    retest = Mined(
        findings=[Finding(ResolvedAnalysis(id="x", kind=MinerKind.SEGMENT, columns=("a", "b"),
                                           question="?", priority=1, source=Source.RULES),
                          facts=[moved])],
        tests=[_retest("h:F1", 0.4, 0.0001), _retest("h:F2", 0.3, 0.2),
               _retest("h:F3", -0.2, 0.0001), _retest("h:F5", 0.5, 0.0001, moved)],
        threshold=0.0,
    )  # fmt: skip
    chosen = [holds, weakens, flips, gone, groups]
    outcomes = {r.fact_id: r.outcome for r in sensitivity.compare(chosen, retest, threshold=0.01)}
    assert outcomes == {"F1": "holds", "F2": "weakens", "F3": "flips", "F4": "not_retested",
                        "F5": "flips"}  # fmt: skip
    assert {r.outcome for r in sensitivity.unchanged(chosen)} == {"same_data"}
    # Only reliable findings are re-tested, smallest q first, at most ten.
    many = [_fact(f"F{i}", q=0.001 * i) for i in range(1, 15)] + [_fact("F99", tier="inconclusive")]
    assert [f.id for f in sensitivity.top_findings(many)] == [f"F{i}" for i in range(1, 11)]


def test_ranking_puts_evidence_first_marks_duplicates_and_picks_highlights() -> None:
    facts = [
        _fact("F1", tier="supported", q=0.04, effect=0.2),
        _fact("F2", tier="strong", q=1e-9, effect=0.6, columns=("x", "y")),
        _fact("F3", tier="inconclusive", q=0.6, effect=0.05, columns=("p", "q")),
        _fact("F4", tier="strong", q=1e-9, effect=0.6, columns=("x", "y"), hypothesis="h:F2"),
        _fact("F5", tier="descriptive", kind="place_match", columns=("s",)),
        _fact("F6", tier="descriptive", kind="distribution", columns=("d",)),
    ]
    plan = plan_with([column("x", Meaning.CONTINUOUS)])
    flipped = {"F2": sensitivity.Recheck("F2", "h:F2", "flips", 0.6, 1e-9)}
    ranked = ranking.rank(facts, plan, {})
    assert ranked[0].fact_id == "F2"
    assert ranked[-1].duplicate_of == "F2"  # F4 repeats F2's test
    assert next(r for r in ranked if r.fact_id == "F3").evidence == 0.0
    by_id = {f.id: f for f in facts}
    assert ranking.highlights(ranked, by_id) == ["F2", "F1", "F6"]
    demoted = {r.fact_id: r for r in ranking.rank(facts, plan, flipped)}
    assert demoted["F2"].stability == 0.4
    assert demoted["F2"].score < next(r for r in ranked if r.fact_id == "F2").score
