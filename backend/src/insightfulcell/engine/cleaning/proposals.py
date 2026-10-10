"""Cleaning proposals: rules over the profile, plus the planner's validated proposals, turned
into operations with their measured effect (rows affected, values lost, before/after preview).

Effects are measured in DuckDB on every row, each operation against the column as the earlier
operations in its chain leave it. Category maps are built from the data at that point, so
``"QLD "`` is trimmed before it is matched to ``"QLD"``.
"""

from __future__ import annotations

import re
from collections import defaultdict
from collections.abc import Mapping, Sequence
from typing import Any, Final

import duckdb

from insightfulcell.engine import expressions as ex
from insightfulcell.engine.cleaning.operations import (
    CATALOGUE,
    LOSSLESS_PARSE_SHARE,
    PARSE_KINDS,
    Default,
    Operation,
    Policy,
    PreviewPair,
    expression,
    initial_decision,
    operation_id,
)
from insightfulcell.engine.planning.language import (
    CleaningKind,
    CleaningProposal,
    ResolvedPlan,
    Role,
    Source,
)
from insightfulcell.engine.profiling import ColumnProfile, Profile
from insightfulcell.engine.semantics import ColumnSemantics, ParseKind, name_tokens
from insightfulcell.engine.sql import ident
from insightfulcell.engine.types import MISSING_TOKENS, Meaning, is_text_type

PREVIEW_ROWS: Final = 5
PREVIEW_CHARS: Final = 60
MAX_MAP_PAIRS: Final = 200
MAX_DISTINCT_FOR_MERGE: Final = 300
SIMILARITY: Final = 0.92
MASK: Final = "•••"

_NON_NEGATIVE: Final = {
    "age",
    "hours",
    "hrs",
    "days",
    "distance",
    "km",
    "count",
    "qty",
    "quantity",
    "price",
    "cost",
    "duration",
    "tenure",
    "logins",
    "tickets",
    "staff",
    "visits",
    "size",
    "weight",
    "height",
    "length",
    "area",
    "sqm",
    "income",
    "salary",
}
_PAST_EVENT: Final = {
    "created",
    "signup",
    "birth",
    "dob",
    "start",
    "started",
    "opened",
    "joined",
    "ordered",
    "purchased",
    "registered",
    "completed",
    "login",
    "submitted",
}
_FUTURE_OK: Final = {
    "end",
    "expiry",
    "expires",
    "expiration",
    "due",
    "scheduled",
    "next",
    "renewal",
    "contract",
    "warranty",
    "planned",
    "deadline",
}


def propose(  # noqa: PLR0913  (keyword-only inputs)
    con: duckdb.DuckDBPyConnection,
    *,
    semantics: Sequence[ColumnSemantics],
    profile: Profile,
    plan: ResolvedPlan,
    policy: Policy,
    today: str,
) -> list[Operation]:
    """Every operation for table ``t``, measured, with its starting decision under ``policy``."""
    by_name = {s.name: s for s in semantics}
    profiles = {p.name: p for p in profile.columns}
    drafts: list[Operation] = []
    for sem in semantics:
        drafts += _value_rules(sem, profiles[sem.name], plan, today)
    drafts += _from_planner(plan.cleaning, by_name, profiles)
    drafts += _exclusions(semantics, profiles, plan)

    measured = _measure(con, _dedupe(drafts), by_name)
    if profile.table.duplicate_rows:
        measured.append(_duplicates(con, measured, semantics))
    final: list[Operation] = []
    for op in measured:
        if op.rows_affected == 0 and op.kind is not CleaningKind.EXCLUDE_COLUMN:
            continue  # changes nothing in this data
        decision, why = initial_decision(op, policy)
        final.append(op.model_copy(update={"decision": decision, "notice": why or op.notice}))
    return sorted(final, key=lambda o: (o.column is None, o.column or "", o.order))


# --------------------------------------------------------------------------- rule proposals


def _draft(  # noqa: PLR0913
    kind: CleaningKind,
    column: str | None,
    reason: str,
    *,
    params: Mapping[str, Any] | None = None,
    lossy: bool = False,
    default: Default = Default.AUTO,
    source: Source = Source.RULES,
    notice: str | None = None,
) -> Operation:
    return Operation(
        id=operation_id(kind, column),
        kind=kind,
        column=column,
        params=dict(params or {}),
        lossy=lossy,
        default=default,
        reason=reason,
        source=source,
        order=CATALOGUE[kind].order,
        notice=notice,
    )


def _value_rules(  # noqa: PLR0912  (one rule per issue)
    sem: ColumnSemantics, p: ColumnProfile, plan: ResolvedPlan, today: str
) -> list[Operation]:
    if sem.personal_data is not None:
        return []  # excluded from analysis; never cleaned value by value
    resolved = plan.column(sem.name)
    if resolved is not None and resolved.role is Role.EXCLUDED:
        return []
    ops: list[Operation] = []
    name = sem.name
    text = is_text_type(sem.physical_type)
    cat = p.categorical
    if text and cat is not None:
        if cat.non_nfc_rows:
            ops.append(
                _draft(
                    CleaningKind.NORMALIZE_UNICODE,
                    name,
                    "Some values use a different Unicode form for the same characters.",
                )
            )
        if cat.whitespace_rows:
            ops.append(
                _draft(
                    CleaningKind.TRIM_WHITESPACE,
                    name,
                    "Some values have spaces before or after them.",
                )
            )
    if text and p.missing_tokens:
        ops.append(
            _draft(
                CleaningKind.STANDARDIZE_MISSING,
                name,
                "Some values are words meaning 'no value' (such as N/A).",
                params={"tokens": list(MISSING_TOKENS)},
            )
        )
    if (
        text
        and cat is not None
        and cat.case_variant_values
        and sem.meaning
        in {
            Meaning.NOMINAL,
            Meaning.GEOGRAPHY,
        }
        and sem.parse is None
    ):
        ops.append(
            _draft(
                CleaningKind.FIX_CATEGORY_CASE,
                name,
                "The same category is written with different capitals or spaces.",
                params={"value_map": []},
            )
        )
    if (
        text
        and sem.meaning in {Meaning.NOMINAL, Meaning.GEOGRAPHY}
        and sem.parse is None
        and p.distinct <= MAX_DISTINCT_FOR_MERGE
    ):
        ops.append(
            _draft(
                CleaningKind.MERGE_CATEGORIES,
                name,
                "Some categories look like spellings of the same thing.",
                params={"value_map": [], "detect": True},
                lossy=True,
                default=Default.REVIEW,
            )
        )
    if sem.parse is not None and text:
        ops.append(_parse_op(sem))

    tokens = set(name_tokens(name))
    numeric = p.numeric
    if numeric is not None and numeric.count:
        if numeric.sentinels:
            ops.append(
                _draft(
                    CleaningKind.SET_INVALID_TO_NULL,
                    name,
                    "Some values are placeholder codes far outside the real range.",
                    params={"values": list(numeric.sentinels)},
                    lossy=True,
                    default=Default.REVIEW,
                )
            )
        elif tokens & _NON_NEGATIVE and 0 < numeric.negative_share < 0.25:
            ops.append(
                _draft(
                    CleaningKind.SET_INVALID_TO_NULL,
                    name,
                    "A few values are negative, which this measure cannot be.",
                    params={"lower_bound": 0},
                    lossy=True,
                    default=Default.REVIEW,
                )
            )
        if sem.meaning is Meaning.PERCENTAGE:
            ops.append(
                _draft(
                    CleaningKind.SET_INVALID_TO_NULL,
                    name,
                    "A share must be between 0% and 100%.",
                    params={"lower_bound": 0, "upper_bound": 1},
                    lossy=True,
                    default=Default.REVIEW,
                )
            )
        if sem.meaning is Meaning.GEOGRAPHY and tokens & {"latitude", "lat"}:
            ops.append(_bounds(name, -90, 90, "A latitude must be between -90 and 90."))
        if sem.meaning is Meaning.GEOGRAPHY and tokens & {"longitude", "lon", "lng"}:
            ops.append(_bounds(name, -180, 180, "A longitude must be between -180 and 180."))
    dates = p.datetime
    if (
        dates is not None
        and dates.future_count
        and tokens & _PAST_EVENT
        and not tokens & _FUTURE_OK
    ):
        ops.append(
            _draft(
                CleaningKind.SET_INVALID_TO_NULL,
                name,
                "Some dates are in the future, which this event cannot be.",
                params={"not_after": today},
                lossy=True,
                default=Default.REVIEW,
            )
        )
    return ops


def _bounds(name: str, low: float, high: float, reason: str) -> Operation:
    return _draft(
        CleaningKind.SET_INVALID_TO_NULL,
        name,
        reason,
        params={"lower_bound": low, "upper_bound": high},
        lossy=True,
        default=Default.REVIEW,
    )


def _parse_op(sem: ColumnSemantics) -> Operation:
    assert sem.parse is not None  # noqa: S101  (called only for columns with a parse hint)
    kind = {
        ParseKind.NUMBERS: CleaningKind.PARSE_NUMBERS,
        ParseKind.PERCENTAGES: CleaningKind.PARSE_PERCENTAGES,
        ParseKind.DATES: CleaningKind.PARSE_DATES,
        ParseKind.BOOLEANS: CleaningKind.PARSE_BOOLEANS,
    }[sem.parse.kind]
    params: dict[str, Any] = {}
    reason = {
        CleaningKind.PARSE_NUMBERS: "Numbers are stored as text (with symbols or separators).",
        CleaningKind.PARSE_PERCENTAGES: "Percentages are stored as text; they become fractions.",
        CleaningKind.PARSE_DATES: "Dates are stored as text, in more than one format.",
        CleaningKind.PARSE_BOOLEANS: "Yes/no answers are written in several ways.",
    }[kind]
    default = Default.AUTO
    if kind is CleaningKind.PARSE_DATES:
        params["day_first"] = sem.parse.day_first is not False
        if sem.parse.ambiguous_day_month or sem.parse.mixed_day_month:
            default = Default.REVIEW
            order = "day before month" if params["day_first"] else "month before day"
            reason += (
                f" Some dates read differently day-first and month-first; they will be read "
                f"{order}."
            )
    if kind is CleaningKind.PARSE_PERCENTAGES and sem.parse.mixed_representation:
        default = Default.REVIEW
        reason += " Some values have no % sign: up to 1 is read as a fraction, above 1 as points."
    return _draft(kind, sem.name, reason, params=params, default=default)


def _exclusions(
    semantics: Sequence[ColumnSemantics],
    profiles: Mapping[str, ColumnProfile],
    plan: ResolvedPlan,
) -> list[Operation]:
    ops = []
    for sem in semantics:
        resolved = plan.column(sem.name)
        reason = None
        if sem.personal_data is not None:
            reason = "Holds personal data; it is masked and left out of the analysis."
        elif "constant" in profiles[sem.name].flags:
            reason = "Every row has the same value, so it cannot explain anything."
        elif sem.meaning is Meaning.IDENTIFIER:
            reason = "An identifier: it names rows rather than describing them."
        elif resolved is not None and resolved.role is Role.EXCLUDED:
            reason = "The plan leaves it out of the analysis."
        if reason:
            ops.append(
                _draft(
                    CleaningKind.EXCLUDE_COLUMN,
                    sem.name,
                    reason,
                    notice="Analysis only: the column stays in the cleaned data.",
                )
            )
    return ops


def _from_planner(
    proposals: Sequence[CleaningProposal],
    semantics: Mapping[str, ColumnSemantics],
    profiles: Mapping[str, ColumnProfile],
) -> list[Operation]:
    """The planner's proposals (already validated against the catalogue and the columns)."""
    ops: list[Operation] = []
    for item in proposals:
        sem = semantics.get(item.column) if item.column else None
        if item.kind is CleaningKind.DROP_DUPLICATES or (sem and sem.personal_data):
            continue  # duplicates are measured on the whole table; personal data is excluded
        if item.kind is CleaningKind.MERGE_CATEGORIES and item.value_map:
            ops.append(
                _draft(
                    item.kind,
                    item.column,
                    item.reason,
                    params={"value_map": [[p.from_value, p.to_value] for p in item.value_map]},
                    lossy=True,
                    default=Default.REVIEW,
                    source=Source.MODEL,
                )
            )
        elif item.kind is CleaningKind.SET_INVALID_TO_NULL and (
            item.lower_bound is not None or item.upper_bound is not None
        ):
            ops.append(
                _draft(
                    item.kind,
                    item.column,
                    item.reason,
                    params={"lower_bound": item.lower_bound, "upper_bound": item.upper_bound},
                    lossy=True,
                    default=Default.REVIEW,
                    source=Source.MODEL,
                )
            )
        elif item.kind in {CleaningKind.WINSORIZE, CleaningKind.IMPUTE} and item.column:
            profile = profiles.get(item.column)
            op = _opt_in(item, profile)
            if op is not None:
                ops.append(op)
        elif item.kind is CleaningKind.EXCLUDE_COLUMN and item.column:
            ops.append(
                _draft(
                    item.kind,
                    item.column,
                    item.reason,
                    source=Source.MODEL,
                    notice="Analysis only: the column stays in the cleaned data.",
                )
            )
    return ops


def _opt_in(item: CleaningProposal, profile: ColumnProfile | None) -> Operation | None:
    """Impute and winsorize are off unless a person turns them on; the fill value or caps are
    computed now, from the data, so the change is reproducible."""
    if profile is None:
        return None
    numeric = profile.numeric
    if item.kind is CleaningKind.WINSORIZE:
        if numeric is None or numeric.p01 is None or numeric.p99 is None:
            return None
        params: dict[str, Any] = {"lower": numeric.p01, "upper": numeric.p99}
    elif numeric is not None and numeric.count and item.strategy != "mode":
        params = {"value": numeric.p50, "strategy": "median"}
    elif profile.categorical is not None and profile.categorical.top:
        params = {"value": profile.categorical.top[0].value, "strategy": "mode"}
    else:
        return None
    return _draft(
        item.kind,
        item.column,
        item.reason,
        params=params,
        lossy=True,
        default=Default.OPT_IN,
        source=Source.MODEL,
    )


def _dedupe(ops: Sequence[Operation]) -> list[Operation]:
    """One operation per (kind, column). Category maps from rules and the planner are joined:
    the planner's pairs first (it knows ``N.S.W.`` and ``New South Wales`` mean ``NSW``), then
    the pairs detected from the data, never creating a cycle."""
    merged: dict[str, Operation] = {}
    for op in ops:
        current = merged.get(op.id)
        if current is None:
            merged[op.id] = op
            continue
        if op.kind is CleaningKind.MERGE_CATEGORIES:
            # The planner's pairs (labels it knows mean the same) come first; detection fills in.
            model_op, rule_op = (op, current) if op.source is Source.MODEL else (current, op)
            pairs = [*model_op.params.get("value_map", []), *rule_op.params.get("value_map", [])]
            params = {**rule_op.params, **model_op.params, "value_map": pairs}
            if rule_op.params.get("detect"):
                params["detect"] = True
            merged[op.id] = model_op.model_copy(update={"params": params})
    return list(merged.values())


# --------------------------------------------------------------------------- measuring


def _measure(
    con: duckdb.DuckDBPyConnection,
    ops: Sequence[Operation],
    semantics: Mapping[str, ColumnSemantics],
) -> list[Operation]:
    by_column: dict[str | None, list[Operation]] = defaultdict(list)
    for op in ops:
        by_column[op.column].append(op)
    out: list[Operation] = []
    for column, chain in by_column.items():
        if column is None:
            out += chain
            continue
        sem = semantics[column]
        value = ident(column)
        for op in sorted(chain, key=lambda o: o.order):
            if op.kind is CleaningKind.EXCLUDE_COLUMN:
                out.append(op)
                continue
            current = op
            if op.kind in {CleaningKind.FIX_CATEGORY_CASE, CleaningKind.MERGE_CATEGORIES}:
                current = _with_map(con, op, value)
                if not current.params.get("value_map"):
                    continue
            new_value = expression(current.kind, value, current.params)
            current = _effect(con, current, value, new_value, sem)
            out.append(current)
            value = new_value
    return out


def _with_map(con: duckdb.DuckDBPyConnection, op: Operation, value: str) -> Operation:
    """Fill in (or check) the category map against the column's values at this point."""
    rows = con.execute(
        f"SELECT v, count(*) AS n FROM (SELECT {value} AS v FROM t) WHERE v IS NOT NULL "
        "GROUP BY v ORDER BY n DESC, v"
    ).fetchall()
    counts = {str(v): int(n) for v, n in rows}
    present = set(counts)
    if op.kind is CleaningKind.FIX_CATEGORY_CASE:
        pairs = _case_map(counts)
    else:
        proposed = [(a, b) for a, b in op.params.get("value_map", []) if a in present and a != b]
        detected = _similar_map(con, counts) if op.params.get("detect") else []
        pairs = _without_cycles(proposed, detected)
    pairs = pairs[:MAX_MAP_PAIRS]
    params = {k: v for k, v in op.params.items() if k != "detect"}
    params["value_map"] = [[a, b] for a, b in pairs]
    return op.model_copy(update={"params": params})


def _style(value: str) -> int:
    """0: capitalised (``Plumbing``, ``Darwin City``) or a short code (``NSW``); 1: shouting
    (``ELECTRICAL``); 2: all lower case."""
    letters = [ch for ch in value if ch.isalpha()]
    if not letters or (not value.islower() and not value.isupper()):
        return 0
    if value.isupper():
        return 0 if len(letters) <= 4 else 1
    return 2


def _without_cycles(
    proposed: Sequence[tuple[str, str]], detected: Sequence[tuple[str, str]]
) -> list[tuple[str, str]]:
    """The planner's pairs first; a detected pair is added only if it neither rewrites a label
    the planner chose as a target nor feeds one of its sources. No label is ever both a source
    and a target, so applying the map once is final (no swaps, no chains)."""
    pairs = list(dict(proposed).items())
    sources = {a for a, _ in pairs}
    targets = {b for _, b in pairs}
    for a, b in detected:
        if a in sources or a in targets or b in sources:
            continue
        pairs.append((a, b))
        sources.add(a)
        targets.add(b)
    return [(a, b) for a, b in pairs if b not in sources]


def _canonical(spellings: Mapping[str, int]) -> str:
    """The best-written spelling (capitalised beats shouting beats lower case), then the most
    common, then the one with less punctuation (``NSW`` over ``N.S.W.``), then alphabetical."""
    return sorted(
        spellings,
        key=lambda s: (
            _style(s),
            -spellings[s],
            sum(not ch.isalnum() and not ch.isspace() for ch in s),
            s,
        ),
    )[0]


def _case_map(counts: Mapping[str, int]) -> list[tuple[str, str]]:
    groups: dict[str, dict[str, int]] = defaultdict(dict)
    for value, n in counts.items():
        groups[" ".join(value.lower().split())][value] = n
    pairs: list[tuple[str, str]] = []
    for spellings in groups.values():
        if len(spellings) > 1:
            target = _canonical(spellings)
            pairs += [(s, target) for s in sorted(spellings) if s != target]
    return pairs


def _letters(value: str) -> str:
    return "".join(ch for ch in value.lower() if ch.isalnum())


_DIGITS: Final = re.compile(r"[0-9]+")
_WORDS: Final = re.compile(r"[^\W_]+")


def _enumerated(a: str, b: str) -> bool:
    """True when two similar-looking labels are different members of a series, not two
    spellings: their numbers differ (``Store 1``/``Store 2``, ``item0``/``item1``) or they
    differ in exactly one short word (``Group A``/``Group B``, ``Zone N``/``Zone S``)."""
    if _DIGITS.findall(a) != _DIGITS.findall(b):
        return True
    words_a, words_b = _WORDS.findall(a.lower()), _WORDS.findall(b.lower())
    if len(words_a) != len(words_b):
        return False
    differing = [(x, y) for x, y in zip(words_a, words_b, strict=True) if x != y]
    return len(differing) == 1 and max(len(differing[0][0]), len(differing[0][1])) <= 2


def _similar_map(
    con: duckdb.DuckDBPyConnection, counts: Mapping[str, int]
) -> list[tuple[str, str]]:
    """Labels that differ only by punctuation (``N.T.``/``NT``) or are very similar spellings
    (``Electrician``/``Electrical``). Always reviewed: similar is not the same."""
    if len(counts) < 2 or len(counts) > MAX_DISTINCT_FOR_MERGE:
        return []
    parent = {v: v for v in counts}

    def find(v: str) -> str:
        while parent[v] != v:
            parent[v] = parent[parent[v]]
            v = parent[v]
        return v

    def union(a: str, b: str) -> None:
        ra, rb = find(a), find(b)
        if ra != rb:
            parent[rb] = ra

    keys = {v: _letters(v) for v in counts}
    by_key: dict[str, list[str]] = defaultdict(list)
    for value, key in keys.items():
        if key:
            by_key[key].append(value)
    for members in by_key.values():
        for other in members[1:]:
            union(members[0], other)
    values = sorted(k for k in by_key if len(k) >= 6)
    if len(values) >= 2:
        con.execute("CREATE OR REPLACE TEMP TABLE _labels (k VARCHAR)")
        con.executemany("INSERT INTO _labels VALUES (?)", [[v] for v in values])
        similar = con.execute(
            "SELECT a.k, b.k FROM _labels a JOIN _labels b ON a.k < b.k "
            "WHERE jaro_winkler_similarity(a.k, b.k) >= ?",
            [SIMILARITY],
        ).fetchall()
        for a, b in similar:
            if not _enumerated(by_key[a][0], by_key[b][0]):
                union(by_key[a][0], by_key[b][0])
    groups: dict[str, dict[str, int]] = defaultdict(dict)
    for value, n in counts.items():
        groups[find(value)][value] = n
    pairs: list[tuple[str, str]] = []
    for spellings in groups.values():
        if len(spellings) > 1:
            target = _canonical(spellings)
            pairs += [(s, target) for s in sorted(spellings) if s != target]
    return pairs


def _effect(
    con: duckdb.DuckDBPyConnection,
    op: Operation,
    before: str,
    after: str,
    sem: ColumnSemantics,
) -> Operation:
    b, a = f"CAST({before} AS VARCHAR)", f"CAST({after} AS VARCHAR)"
    row = con.execute(
        f"SELECT count(*) FILTER (WHERE {b} IS DISTINCT FROM {a}), "
        f"count(*) FILTER (WHERE {before} IS NOT NULL AND {after} IS NULL "
        f"AND NOT {ex.is_missing_token(b)}), "
        f"count(*) FILTER (WHERE {before} IS NOT NULL AND NOT {ex.is_missing_token(b)}) FROM t"
    ).fetchone()
    assert row is not None  # noqa: S101  (an aggregate query always returns a row)
    affected, lost, present = int(row[0] or 0), int(row[1] or 0), int(row[2] or 0)
    preview = con.execute(
        f"SELECT {b} AS before, {a} AS after, count(*) AS n FROM t "
        f"WHERE {b} IS DISTINCT FROM {a} GROUP BY ALL ORDER BY n DESC, before LIMIT ?",
        [PREVIEW_ROWS],
    ).fetchall()
    pairs = tuple(
        PreviewPair(before=_show(x, sem), after=_show(y, sem), rows=int(n)) for x, y, n in preview
    )
    update: dict[str, Any] = {"rows_affected": affected, "values_lost": lost, "preview": pairs}
    if op.kind in PARSE_KINDS:
        parsed_share = 1 - (lost / present) if present else 1.0
        if parsed_share < LOSSLESS_PARSE_SHARE:
            update["lossy"] = True
            update["default"] = Default.REVIEW
            update["reason"] = (
                f"{op.reason} {lost} value{'s' if lost != 1 else ''} could not be read and "
                "would become empty."
            )
    return op.model_copy(update=update)


def _show(value: object, sem: ColumnSemantics) -> str | None:
    if value is None:
        return None
    if sem.personal_data is not None:
        return MASK
    text = str(value)
    return text if len(text) <= PREVIEW_CHARS else text[: PREVIEW_CHARS - 1] + "…"


def _duplicates(
    con: duckdb.DuckDBPyConnection,
    ops: Sequence[Operation],
    semantics: Sequence[ColumnSemantics],
) -> Operation:
    """Exact duplicates after the proposed value changes (so ``"QLD "`` equals ``"QLD"``)."""
    chains: dict[str, str] = {}
    for sem in semantics:
        value = ident(sem.name)
        for op in sorted((o for o in ops if o.column == sem.name), key=lambda o: o.order):
            value = expression(op.kind, value, op.params)
        chains[sem.name] = value
    select = ", ".join(f"{v} AS {ident(k)}" for k, v in chains.items())
    row = con.execute(
        "SELECT (SELECT count(*) FROM t) - "
        f"(SELECT count(*) FROM (SELECT DISTINCT {select} FROM t))"
    ).fetchone()
    duplicates = int((row or (0,))[0] or 0)
    return _draft(
        CleaningKind.DROP_DUPLICATES,
        None,
        f"{duplicates} row{'s are' if duplicates != 1 else ' is'} an exact copy of another row.",
        lossy=True,
        default=Default.REVIEW,
    ).model_copy(update={"rows_affected": duplicates, "values_lost": 0})
