"""The masked schema card: everything the planning model is allowed to know about a table.

Rules (system map, Understand stage): names, types, missing share, distinct counts, trimmed
ranges, common categories, value shapes. No rows, no personal values, no minimum or maximum,
nothing that relates two columns.

Within those rules the card tries to give the model as much *kind* of evidence as possible,
because a plan is only as good as the model's picture of the table:

- **What each column is**: physical type, detected meaning and confidence, unit and where the
  unit came from, name tokens, the reasons the engine gave.
- **How values are distributed**: trimmed range (5th to 95th percentile, rounded to three
  significant figures), median, IQR, skew and tail shape, zero, negative and integer shares.
- **What is wrong with it**: quality flags (missing tokens, case variants, unparsed values,
  ambiguous day/month, sentinel codes, outliers, future dates, constant...).
- **What it looks like**: value *shapes* (``A-9``, ``$9.9``), never values; common category
  labels only when each occurs at least ``k`` times (k-anonymity) and does not look like a
  person's data.
- **Time**: granularity, coverage span, start and end to the month, busiest weekday.
- **The table**: rows, duplicates, complete-row share, candidate keys, constant columns,
  how often values are missing together (as a count of patterns, not which columns).

Wide tables would drown the model, so detail is spent where it matters: each column starts
with full detail, then the least informative columns are demoted to compact, then minimal,
then near-identical columns are grouped into families (``revenue_2025_01..12``). The card
records what was compressed, so the model knows what it is not seeing.
"""

from __future__ import annotations

import json
import math
import re
from collections import defaultdict
from collections.abc import Iterable, Mapping, Sequence
from typing import Any, Final

from insightfulcell.engine.profiling import ColumnProfile, Profile
from insightfulcell.engine.semantics import ColumnSemantics, name_tokens
from insightfulcell.engine.types import Meaning

CARD_VERSION: Final = "card-v1"
#: Characters per token for estimates. Conservative for JSON with short keys.
CHARS_PER_TOKEN: Final = 3.4
MAX_NAME_CHARS: Final = 64
MAX_LABEL_CHARS: Final = 40
MAX_CATEGORIES: Final = 8
LOOKS_PERSONAL: Final = re.compile(
    r"@|\d{6,}|\+\d|\b\d{3}[- ]\d{3,4}[- ]\d{3,4}\b|^[A-Z][a-z]+ [A-Z][a-z]+$"
)
TARGET_WORDS: Final = {
    "target",
    "label",
    "outcome",
    "churn",
    "churned",
    "converted",
    "conversion",
    "default",
    "fraud",
    "response",
    "survived",
    "complaint",
    "cancelled",
    "won",
    "lost",
}
WEEKDAYS: Final = ("Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun")


def estimate_tokens(value: object) -> int:
    text = value if isinstance(value, str) else json.dumps(value, ensure_ascii=False)
    return math.ceil(len(text) / CHARS_PER_TOKEN)


def sig(value: float | None, digits: int = 3) -> float | None:
    """Round to significant figures: enough to plan with, too coarse to identify a row."""
    if value is None or not math.isfinite(value):
        return None
    if value == 0:
        return 0.0
    magnitude = math.floor(math.log10(abs(value)))
    rounded = round(value, -magnitude + digits - 1)
    return float(int(rounded)) if rounded == int(rounded) else rounded


def share(numerator: int | float, denominator: int | float) -> float:
    return round(numerator / denominator, 3) if denominator else 0.0


def k_min(rows: int) -> int:
    """A category label is shown only if at least this many rows carry it."""
    return max(2, min(10, math.ceil(rows * 0.01)))


def quote(text: str, limit: int) -> str:
    """Data-derived text: control characters removed, length limited. The prompt marks the
    whole card as data, and the gateway checks the payload before it leaves."""
    cleaned = "".join(ch if ch.isprintable() else " " for ch in text).strip()
    return cleaned if len(cleaned) <= limit else cleaned[: limit - 1] + "…"


# --------------------------------------------------------------------------- column entries


def _numeric(p: ColumnProfile) -> dict[str, Any] | None:
    n = p.numeric
    if n is None or not n.count:
        return None
    iqr = (n.p75 - n.p25) if n.p75 is not None and n.p25 is not None else None
    skew = n.skewness or 0.0
    shape = "symmetric"
    if skew > 1:
        shape = "right-skewed"
    elif skew < -1:
        shape = "left-skewed"
    tails = "heavy" if (n.kurtosis or 0) > 3 else "normal"
    out: dict[str, Any] = {
        "trimmed_range": [sig(n.p05), sig(n.p95)],
        "median": sig(n.p50),
        "iqr": sig(iqr),
        "shape": shape,
        "tails": tails,
    }
    if n.zero_share:
        out["zero_share"] = round(n.zero_share, 3)
    if n.negative_share:
        out["negative_share"] = round(n.negative_share, 3)
    out["whole_numbers"] = n.integer_share == 1
    if n.outliers:
        out["outlier_share"] = share(n.outliers, n.count)
    return out


def _datetime(p: ColumnProfile) -> dict[str, Any] | None:
    d = p.datetime
    if d is None or not d.count:
        return None
    out: dict[str, Any] = {
        "granularity": d.granularity,
        "coverage_days": sig(d.span_days),
        "from_month": (d.p05 or "")[:7] or None,  # trimmed, to the month: never the minimum
        "to_month": (d.p95 or "")[:7] or None,
        "distinct_days": d.distinct_days,
    }
    if d.weekday_counts and sum(d.weekday_counts):
        busiest = max(range(7), key=lambda i: d.weekday_counts[i])
        out["busiest_weekday"] = WEEKDAYS[busiest]
        weekend = sum(d.weekday_counts[5:]) / sum(d.weekday_counts)
        out["weekend_share"] = round(weekend, 2)
    if d.future_count:
        out["future_share"] = share(d.future_count, d.count)
    return out


def _categories(p: ColumnProfile, rows: int) -> tuple[list[list[Any]], float] | None:
    c = p.categorical
    if c is None or not c.top or p.meaning not in {Meaning.NOMINAL, Meaning.GEOGRAPHY}:
        return None
    present = max(1, p.present)
    threshold = k_min(rows)
    shown: list[list[Any]] = [
        [quote(item.value, MAX_LABEL_CHARS), share(item.count, present)]
        for item in c.top
        if item.count >= threshold and not LOOKS_PERSONAL.search(item.value)
    ][:MAX_CATEGORIES]
    other = 1 - sum(float(s) for _, s in shown)
    return shown, round(max(0.0, other), 3)


def _shapes(p: ColumnProfile) -> list[list[Any]] | None:
    c = p.categorical
    if c is None or not c.shapes:
        return None
    present = max(1, p.present)
    return [[s.shape, share(s.count, present)] for s in c.shapes[:3]]


def column_entry(  # noqa: PLR0912  (one branch per kind of evidence)
    sem: ColumnSemantics, p: ColumnProfile, rows: int, *, detail: str
) -> dict[str, Any]:
    """One column at ``detail`` = full, compact or minimal."""
    entry: dict[str, Any] = {
        "name": quote(sem.name, MAX_NAME_CHARS),
        "type": sem.physical_type,
        "meaning": sem.meaning.value,
    }
    entry["missing_share"] = share(p.missing, p.rows)
    if sem.personal_data is not None:
        # Masked: the kind of personal data and nothing about its values.
        entry["personal_data"] = sem.personal_data.value
        return entry
    entry["distinct"] = p.distinct
    if p.flags:
        entry["flags"] = list(p.flags) if detail != "minimal" else list(p.flags[:3])
    if detail == "minimal":
        return entry

    entry["confidence"] = sem.confidence.value
    if sem.unit:
        entry["unit"] = sem.unit
        entry["unit_from"] = sem.unit_source
    if sem.parse is not None:
        entry["stored_as_text"] = sem.parse.kind.value
        entry["parse_share"] = round(sem.parse.share, 3)
    entry["unique_share"] = share(p.distinct, max(1, p.present))
    numeric = _numeric(p)
    if numeric:
        entry["numbers"] = (
            numeric
            if detail == "full"
            else {k: numeric[k] for k in ("trimmed_range", "median", "shape") if k in numeric}
        )
    dates = _datetime(p)
    if dates:
        entry["dates"] = (
            dates
            if detail == "full"
            else {k: dates[k] for k in ("granularity", "from_month", "to_month") if k in dates}
        )
    categories = _categories(p, rows)
    if categories:
        shown, other = categories
        entry["common_values"] = shown if detail == "full" else shown[:3]
        if detail == "full":
            entry["other_share"] = other
    if p.text is not None and p.text.length_p50 is not None:
        entry["text"] = {
            "median_length": sig(p.text.length_p50),
            "median_words": sig(p.text.words_p50),
        }
    if detail == "full":
        shapes = _shapes(p)
        if shapes and sem.meaning in {
            Meaning.IDENTIFIER,
            Meaning.NOMINAL,
            Meaning.CURRENCY,
            Meaning.CONTINUOUS,
            Meaning.PERCENTAGE,
            Meaning.DATETIME,
        }:
            entry["value_shapes"] = shapes
        tokens = name_tokens(sem.name)
        if len(tokens) > 1:
            entry["name_words"] = list(tokens)
        if sem.reasons:
            entry["why"] = [quote(r, 120) for r in sem.reasons[:2]]
    return entry


# --------------------------------------------------------------------------- budgeting


def _priority(sem: ColumnSemantics, p: ColumnProfile) -> float:
    """Higher keeps more detail. Ties keep file order."""
    score = 1.0
    tokens = set(name_tokens(sem.name))
    if tokens & TARGET_WORDS:
        score += 3
    if p.flags:
        score += 1 + 0.25 * len(p.flags)
    if sem.meaning in {Meaning.CONTINUOUS, Meaning.CURRENCY, Meaning.PERCENTAGE, Meaning.DATETIME}:
        score += 1
    if sem.meaning is Meaning.IDENTIFIER or "constant" in p.flags:
        score -= 2
    if sem.personal_data is not None:
        score -= 3  # always minimal anyway
    return score


_FAMILY: Final = re.compile(r"^(.*?)[_\s-]*\d+([_\s-]*\d+)*$")


def family_key(name: str) -> str | None:
    """``revenue_2025_01`` -> ``revenue``; ``q12`` -> ``q``; names without trailing digits have
    no family."""
    match = _FAMILY.match(name)
    if not match or not match.group(1):
        return None
    return match.group(1).lower()


def build_card(
    semantics: Sequence[ColumnSemantics],
    profile: Profile,
    *,
    dataset_name: str,
    token_budget: int,
    confirmed: Mapping[str, Mapping[str, str]] | None = None,
) -> dict[str, Any]:
    """The card as a JSON-ready dict. ``confirmed`` holds names and units a person already
    confirmed for this dataset (``{column: {"display_name": .., "unit": ..}}``)."""
    by_name = {p.name: p for p in profile.columns}
    rows = profile.table.rows
    order = list(semantics)
    detail = {s.name: "full" if s.personal_data is None else "minimal" for s in order}

    def render() -> dict[str, Any]:
        return _assemble(order, by_name, rows, detail, profile, dataset_name, confirmed)

    card = render()
    ranked = sorted(order, key=lambda s: (_priority(s, by_name[s.name]), -s.position))
    for level in ("compact", "minimal"):
        for sem in ranked:
            if estimate_tokens(card) <= token_budget:
                break
            if detail[sem.name] in ("full", "compact") and (
                level == "minimal" or detail[sem.name] == "full"
            ):
                detail[sem.name] = level
                card = render()
    if estimate_tokens(card) > token_budget:
        card = _group_families(card)
    if estimate_tokens(card) > token_budget:
        card = _omit_tail(card, token_budget)
    card["detail"]["estimated_tokens"] = estimate_tokens(card)
    return card


def _assemble(  # noqa: PLR0913, PLR0917
    order: Sequence[ColumnSemantics],
    by_name: Mapping[str, ColumnProfile],
    rows: int,
    detail: Mapping[str, str],
    profile: Profile,
    dataset_name: str,
    confirmed: Mapping[str, Mapping[str, str]] | None,
) -> dict[str, Any]:
    columns = []
    for sem in order:
        entry = column_entry(sem, by_name[sem.name], rows, detail=detail[sem.name])
        known = (confirmed or {}).get(sem.name)
        if known:
            entry["confirmed_by_person"] = {
                k: quote(v, MAX_LABEL_CHARS) for k, v in known.items() if v
            }
        columns.append(entry)
    table = profile.table
    counts: dict[str, int] = defaultdict(int)
    for level in detail.values():
        counts[level] += 1
    return {
        "card_version": CARD_VERSION,
        "dataset": {
            "name": quote(dataset_name, 120),
            "rows": rows,
            "columns": table.columns,
            "duplicate_rows": table.duplicate_rows,
            "complete_row_share": share(table.complete_rows, rows),
            "candidate_keys": [quote(k, MAX_NAME_CHARS) for k in table.candidate_keys[:5]],
            "constant_columns": [quote(k, MAX_NAME_CHARS) for k in table.constant_columns[:10]],
            "personal_data_columns": sum(1 for s in order if s.personal_data is not None),
            "missing_value_patterns": len(table.missing_patterns),
        },
        "columns": columns,
        "detail": {
            "full": counts["full"],
            "compact": counts["compact"],
            "minimal": counts["minimal"],
            "grouped_columns": 0,
            "omitted_columns": 0,
            "personal_data_masked": True,
            "rules": "no rows, no personal values, no minimum or maximum, no cross-column "
            "statistics; category labels shown only when at least "
            f"{k_min(rows)} rows share them",
        },
    }


def _group_families(card: dict[str, Any]) -> dict[str, Any]:
    """Collapse runs of minimal columns that differ only by trailing numbers."""
    groups: dict[tuple[str, str, str], list[dict[str, Any]]] = defaultdict(list)
    for entry in card["columns"]:
        key = family_key(entry["name"])
        if (
            key
            and "personal_data" not in entry
            and set(entry)
            <= {
                "name",
                "type",
                "meaning",
                "missing_share",
                "distinct",
                "flags",
            }
        ):
            groups[(key, entry["type"], entry["meaning"])].append(entry)
    grouped = 0
    replaced: dict[int, dict[str, Any]] = {}
    drop: set[int] = set()
    for (key, physical, meaning), members in groups.items():
        if len(members) < 3:
            continue
        ids = [id(m) for m in members]
        replaced[ids[0]] = {
            "family": key,
            "count": len(members),
            "first": members[0]["name"],
            "last": members[-1]["name"],
            "type": physical,
            "meaning": meaning,
            "missing_share_max": max(m["missing_share"] for m in members),
            "flags": sorted({f for m in members for f in m.get("flags", [])})[:4],
        }
        drop.update(ids[1:])
        grouped += len(members)
    card["columns"] = [replaced.get(id(e), e) for e in card["columns"] if id(e) not in drop]
    card["detail"]["grouped_columns"] = grouped
    return card


def _omit_tail(card: dict[str, Any], budget: int) -> dict[str, Any]:
    """Last resort: drop the lowest columns (already minimal) and say how many."""
    columns: list[dict[str, Any]] = card["columns"]
    omitted = 0
    while columns and estimate_tokens(card) > budget:
        columns.pop()
        omitted += 1
    card["detail"]["omitted_columns"] = omitted
    return card


def column_names(card: Mapping[str, Any]) -> list[str]:
    """Every real column name the card refers to (families included by name only)."""
    return [entry["name"] for entry in card.get("columns", []) if "name" in entry]


def card_json(card: Mapping[str, Any]) -> str:
    return json.dumps(card, ensure_ascii=False, separators=(",", ":"), sort_keys=False)


def fingerprint_parts(semantics: Iterable[ColumnSemantics]) -> list[list[str]]:
    """What identifies a schema for the plan cache: names, physical types and meanings."""
    return [[s.name, s.physical_type, s.meaning.value] for s in semantics]
