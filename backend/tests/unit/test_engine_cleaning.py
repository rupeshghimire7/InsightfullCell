"""Cleaning proposals, policies, the cleaning SQL and the Python recipe."""

from __future__ import annotations

import ast
import subprocess
import sys
from pathlib import Path

import duckdb
import pytest

from insightfulcell.engine import recipe, sql
from insightfulcell.engine.cleaning.operations import (
    Decision,
    Operation,
    Policy,
    cleaned_select,
    expression,
    needs_review,
)
from insightfulcell.engine.cleaning.proposals import _without_cycles, propose
from insightfulcell.engine.planning import merge, rules, validator
from insightfulcell.engine.planning.language import CleaningKind, Provenance, ResolvedPlan
from insightfulcell.engine.profiling import Profile
from insightfulcell.engine.semantics import ColumnSemantics
from insightfulcell.ingestion.engine import ingest_file
from insightfulcell.ingestion.formats import FileFormat
from tests.unit.engine_support import SAMPLES, TODAY, analyse

COLUMNS = [
    ("state", "VARCHAR"),
    ("created", "VARCHAR"),
    ("hours", "DOUBLE"),
    ("fee", "VARCHAR"),
    ("email", "VARCHAR"),
]
ROWS = [
    ("NSW", "15/11/2025", 2.0, "$10", "a1@x.io"),
    ("nsw", "16/11/2025", 3.0, "$12", "a2@x.io"),
    ("NSW ", "03/04/2025", 4.0, "$14", "a3@x.io"),
    ("N.T.", "17/11/2025", -999.0, "$16", "a4@x.io"),
    ("NT", "18/11/2025", 5.0, "N/A", "a5@x.io"),
    ("NT", "18/11/2025", 5.0, "N/A", "a5@x.io"),
    ("VIC", "19/11/2025", 6.0, "$18", "a6@x.io"),
    ("VIC", "20/11/2025", 7.0, "$20", "a7@x.io"),
]


def plan_for(found: list[ColumnSemantics], profile: Profile) -> ResolvedPlan:
    rule_plan, _ = validator.validate(
        rules.plan(found, profile), found, profile, dataset_name="jobs"
    )
    return merge.resolve(
        rules=rule_plan,
        model=None,
        semantics=found,
        profile=profile,
        annotations=merge.Annotations(),
        provenance=Provenance(source="rules", schema_fingerprint="0" * 64),
    )


def proposals(policy: Policy) -> tuple[list[Operation], duckdb.DuckDBPyConnection]:
    con = sql.connect()
    con.execute("CREATE TABLE t (" + ", ".join(f"{sql.ident(n)} {t}" for n, t in COLUMNS) + ")")
    con.executemany(
        f"INSERT INTO t VALUES ({', '.join('?' * len(COLUMNS))})", [list(r) for r in ROWS]
    )
    found, profile = analyse(con, COLUMNS)
    ops = propose(
        con,
        semantics=found,
        profile=profile,
        plan=plan_for(found, profile),
        policy=policy,
        today=TODAY,
    )
    return ops, con


def by_id(ops: list[Operation]) -> dict[str, Operation]:
    return {o.id: o for o in ops}


class TestProposals:
    def test_lossless_changes_apply_and_lossy_ones_wait_for_a_person(self) -> None:
        ops, _ = proposals(Policy.REVIEW_LOSSY)
        found = by_id(ops)
        assert found["trim_whitespace:state"].decision is Decision.ACCEPTED
        case = found["fix_category_case:state"]
        assert case.decision is Decision.ACCEPTED
        assert ["nsw", "NSW"] in case.params["value_map"]
        merge_op = found["merge_categories:state"]
        assert (merge_op.lossy, merge_op.decision) == (True, Decision.PENDING)
        assert merge_op.params["value_map"] == [["N.T.", "NT"]]
        sentinel = found["set_invalid_to_null:hours"]
        assert sentinel.params["values"] == [-999.0]
        assert (sentinel.rows_affected, sentinel.values_lost) == (1, 1)
        dates = found["parse_dates:created"]
        assert dates.decision is Decision.PENDING  # 03/04 reads differently day-first
        fee = found["parse_numbers:fee"]
        assert fee.decision is Decision.ACCEPTED  # N/A is a missing token, not a lost value
        assert found["standardize_missing:fee"].decision is Decision.ACCEPTED
        duplicates = found["drop_duplicates:*"]
        assert (duplicates.rows_affected, duplicates.decision) == (1, Decision.PENDING)
        email = found["exclude_column:email"]
        assert email.decision is Decision.ACCEPTED
        assert email.notice
        assert needs_review(ops)

    def test_previews_show_before_and_after_and_mask_personal_data(self) -> None:
        ops, _ = proposals(Policy.REVIEW_LOSSY)
        trim = by_id(ops)["trim_whitespace:state"]
        assert [(p.before, p.after, p.rows) for p in trim.preview] == [("NSW ", "NSW", 1)]
        assert all("@" not in (p.before or "") for o in ops for p in o.preview)

    def test_always_review_holds_everything_and_auto_safe_never_pauses(self) -> None:
        ops, _ = proposals(Policy.ALWAYS_REVIEW)
        assert {o.decision for o in ops} == {Decision.PENDING}
        ops, _ = proposals(Policy.AUTO_SAFE)
        assert not needs_review(ops)
        lossy = [o for o in ops if o.lossy]
        assert lossy
        assert all(o.decision is Decision.REJECTED for o in lossy)
        assert all(o.notice for o in lossy)

    def test_the_cleaned_select_applies_only_accepted_changes(self) -> None:
        ops, con = proposals(Policy.REVIEW_LOSSY)
        names = [n for n, _ in COLUMNS]
        rows = con.execute(cleaned_select(names, ops)).fetchall()
        states = sorted({r[0] for r in rows})
        assert states == ["N.T.", "NSW", "NT", "VIC"]  # the lossy merge is still pending
        assert len(rows) == 8  # duplicates are kept until a person accepts dropping them
        accepted = [o.model_copy(update={"decision": Decision.ACCEPTED}) for o in ops]
        rows = con.execute(cleaned_select(names, accepted)).fetchall()
        assert sorted({r[0] for r in rows}) == ["NSW", "NT", "VIC"]
        assert len(rows) == 7
        assert -999.0 not in {r[2] for r in rows}


def test_the_recipe_rebuilds_the_cleaned_table(tmp_path: Path) -> None:
    source = SAMPLES / "3_complex" / "field_service_jobs.csv"
    result = ingest_file(source, FileFormat.CSV, tmp_path / "data.parquet")
    columns = [(c.name, c.type) for c in result.columns]
    con = sql.connect()
    sql.open_table(con, result.parquet_path)
    found, profile = analyse(con, columns)
    plan = plan_for(found, profile)
    ops = propose(
        con, semantics=found, profile=profile, plan=plan, policy=Policy.REVIEW_LOSSY, today=TODAY
    )
    ops = [
        o.model_copy(update={"decision": Decision.ACCEPTED})
        if o.decision is Decision.PENDING
        else o
        for o in ops
    ]
    expected = con.execute(
        f"SELECT count(*) FROM ({cleaned_select([n for n, _ in columns], ops)})"
    ).fetchone()

    script = recipe.render(
        dataset_name="field_service_jobs",
        run_id="r",
        source_format="csv",
        columns=columns,
        read_as_text=False,
        operations=ops,
        plan=plan,
        cleaning_revision=2,
        cleaning_status="approved",
    )
    compile(script, "recipe.py", "exec")
    assert "CLEAN_SQL" in script
    path = tmp_path / "recipe.py"
    path.write_text(script)
    out = tmp_path / "out"
    done = subprocess.run(  # noqa: S603  (our own generated file, run with this interpreter)
        [sys.executable, str(path), str(source), str(out)],
        capture_output=True,
        text=True,
        check=False,
    )
    assert done.returncode == 0, done.stderr
    rebuilt = duckdb.connect()
    rows = rebuilt.execute(
        "SELECT count(*) FROM read_parquet(?)", [str(out / "cleaned.parquet")]
    ).fetchone()
    assert rows == expected == (23,)  # two exact duplicates dropped
    analysis_columns = [
        r[0]
        for r in rebuilt.execute(
            "DESCRIBE SELECT * FROM read_parquet(?)", [str(out / "analysis.parquet")]
        ).fetchall()
    ]
    assert "Email" not in analysis_columns
    assert "Customer Name" not in analysis_columns
    assert (out / "charts.html").read_text().count("vegaEmbed(") >= 1


@pytest.mark.parametrize("kind", list(CleaningKind))
def test_every_catalogue_operation_has_sql_or_is_analysis_only(kind: CleaningKind) -> None:
    params = {
        "value_map": [["a", "b"]],
        "values": [-999],
        "lower": 0,
        "upper": 1,
        "value": 0,
        "day_first": True,
        "lower_bound": 0,
    }
    sql_text = expression(kind, '"x"', params)
    if kind in {CleaningKind.EXCLUDE_COLUMN, CleaningKind.DROP_DUPLICATES}:
        assert sql_text == '"x"'
    else:
        assert sql_text != '"x"'


def test_merged_category_maps_never_swap_or_chain() -> None:
    planner = [("N.S.W.", "NSW"), ("New South Wales", "NSW")]
    detected = [("NSW", "N.S.W."), ("Vic", "VIC"), ("VIC", "Victoria")]
    pairs = _without_cycles(planner, detected)
    assert ("NSW", "N.S.W.") not in pairs  # would undo the planner's merge
    sources = {a for a, _ in pairs}
    assert not sources & {b for _, b in pairs}  # applying the map once is final
    assert ("N.S.W.", "NSW") in pairs


def test_numbered_or_lettered_labels_are_never_proposed_as_spellings_of_one_label() -> None:
    labels = ["Store 1", "Store 2", "item0", "item1", "Group A", "Group B", "Electrician"]
    labels += ["Plumbing"]
    con = sql.connect()
    con.execute("CREATE TABLE t (team VARCHAR)")
    rows = [[v] for v in labels for _ in range(3)] + [["Electrican"], ["Plumbng"]]
    con.executemany("INSERT INTO t VALUES (?)", rows)
    found, profile = analyse(con, [("team", "VARCHAR")])
    ops = by_id(
        propose(
            con,
            semantics=found,
            profile=profile,
            plan=plan_for(found, profile),
            policy=Policy.REVIEW_LOSSY,
            today=TODAY,
        )
    )
    merged = ops["merge_categories:team"].params["value_map"]
    assert sorted(merged) == [["Electrican", "Electrician"], ["Plumbng", "Plumbing"]]


def test_hostile_file_and_column_names_never_become_recipe_code(tmp_path: Path) -> None:
    """A file name can close a docstring and a column name can hold a line break; neither may
    turn into a statement in the recipe a person runs on their own machine."""
    hostile = 'x"""\nINJECTED = 1\n#'
    columns = [(hostile, "VARCHAR"), ('q"', "VARCHAR"), ("note", "VARCHAR")]
    con = sql.connect()
    con.execute("CREATE TABLE t (" + ", ".join(f"{sql.ident(n)} {t}" for n, t in columns) + ")")
    rows = [(" a", "b", "</script><script>alert(1)</script>"), ("A", "b", "x")] * 6
    con.executemany("INSERT INTO t VALUES (?, ?, ?)", [list(r) for r in rows])
    found, profile = analyse(con, columns)
    plan = plan_for(found, profile)
    ops = propose(
        con, semantics=found, profile=profile, plan=plan, policy=Policy.AUTO_SAFE, today=TODAY
    )
    assert any(o.column == hostile for o in ops)  # the hostile name reaches the comments
    script = recipe.render(
        dataset_name='jobs"""; INJECTED = 2; """',
        run_id="r",
        source_format="csv",
        columns=columns,
        read_as_text=False,
        operations=ops,
        plan=plan,
        cleaning_revision=1,
        cleaning_status="approved",
    )
    tree = ast.parse(script)
    assigned = {
        target.id
        for node in tree.body
        if isinstance(node, ast.Assign)
        for target in node.targets
        if isinstance(target, ast.Name)
    }
    assert "INJECTED" not in assigned
    namespace: dict[str, object] = {}
    exec(compile(tree, "recipe.py", "exec"), namespace)  # noqa: S102  (definitions only)
    assert "INJECTED" not in namespace
    assert namespace["COLUMNS"] == [[n, t] for n, t in columns]  # names kept, as data
