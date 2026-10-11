"""The report as one self-contained HTML file.

Built from the stored documents (the report, its dashboard and the facts they cite) through a
Jinja template with autoescaping on and undefined names refused. No script, no external
resource: styles are inline, charts and maps are SVG (``exports.svg``). A print stylesheet
makes "Print, then Save as PDF" produce a clean PDF.
"""

from __future__ import annotations

import re
import unicodedata
from collections.abc import Mapping, Sequence
from datetime import datetime
from functools import cache
from typing import Any, Final

from jinja2 import Environment, PackageLoader, StrictUndefined, select_autoescape

from insightfulcell import __version__
from insightfulcell.engine.analysis.dashboard import Dashboard
from insightfulcell.engine.analysis.report import Report
from insightfulcell.exports.svg import chart_svg, format_value, map_legend, map_svg

#: The response's Content-Security-Policy: nothing may load or run.
CONTENT_SECURITY_POLICY: Final = (
    "default-src 'none'; style-src 'unsafe-inline'; img-src data:; base-uri 'none'; "
    "form-action 'none'; frame-ancestors 'none'"
)
_SLUG: Final = re.compile(r"[^a-z0-9]+")

EFFECT_LABELS: Final = {
    "spearman": "rank correlation",
    "pooled_spearman": "rank correlation within groups",
    "kendall_tau": "Kendall's tau",
    "eta": "eta",
    "cramers_v": "Cramér's V",
    "mean_difference": "difference of averages",
    "share_difference": "difference of shares",
}
TIER_LABELS: Final = {
    "strong": "Strong evidence",
    "supported": "Supported",
    "inconclusive": "Not reliable",
    "descriptive": "Descriptive",
}


_PROVIDERS: Final = {
    "gemini": "Gemini",
    "openai": "OpenAI",
    "anthropic": "Claude",
    "deepseek": "DeepSeek",
}
_FALLBACKS: Final = {
    "disabled": "no language model is configured",
    "budget_run": "this run's model budget was spent",
    "budget_org": "the organisation's monthly model budget was spent",
    "breaker_open": "the model provider has been failing",
    "payload_blocked": "the request would have held personal data",
    "timeout": "the model did not answer in time",
    "rate_limited": "the model provider was busy",
    "provider_error": "the model provider returned an error",
    "invalid_output": "the model's answer did not fit what was asked",
    "refused": "the model declined the request",
    "no_facts": "there were no facts to write about",
}


def writing_note(report: Report) -> str:
    """Who wrote the words and what the checks did, in one or two sentences."""
    n = report.narration
    if n.source == "engine":
        reason = _FALLBACKS.get(n.fallback_reason or "", "")
        return "Written by our engine directly from the facts" + (
            f", because {reason}." if reason else "."
        )
    who = _PROVIDERS.get(n.provider or "", n.provider or "a language model")
    model = f" ({n.model})" if n.model else ""
    text = (
        f"Written by {who}{model} from the facts, with every number filled in by our engine. "
        f"Each sentence was checked against its sources: {n.sentences_kept:,} kept"
    )
    if n.sentences_repaired:
        text += f", {n.sentences_repaired:,} of them after one rewrite"
    if n.sentences_dropped:
        text += f"; {n.sentences_dropped:,} replaced by the fact's own wording"
    text += "."
    if n.source == "mixed":
        text += " Some parts were written by the engine instead."
    return text


@cache
def _environment() -> Environment:
    env = Environment(
        loader=PackageLoader("insightfulcell.exports", "templates"),
        autoescape=select_autoescape(default=True, default_for_string=True),
        undefined=StrictUndefined,
        trim_blocks=True,
        lstrip_blocks=True,
    )
    env.globals.update(
        chart_svg=chart_svg,
        map_svg=map_svg,
        map_legend=map_legend,
        format_value=format_value,
        tier_label=lambda tier: TIER_LABELS.get(str(tier), str(tier)),
        effect_label=lambda name: EFFECT_LABELS.get(str(name), str(name).replace("_", " ")),
    )
    env.filters["count"] = lambda n: f"{int(n):,}"
    return env


def render_report(
    *,
    report: Report,
    dashboard: Dashboard | None,
    facts: Sequence[Mapping[str, Any]],
    run_id: str,
    written_at: datetime,
) -> str:
    return (
        _environment()
        .get_template("report.html.j2")
        .render(
            report=report,
            dashboard=dashboard,
            facts=list(facts),
            facts_by_id={str(f["id"]): f for f in facts},
            run_id=run_id,
            written_at=written_at.strftime("%-d %B %Y, %H:%M UTC"),
            version=__version__,
            writing=writing_note(report),
        )
    )


def filename(dataset_name: str, run_id: str) -> str:
    """``report-{dataset}-{run}.html``: ASCII letters, digits and dashes only, so the
    ``Content-Disposition`` header needs no quoting rules."""
    ascii_name = (
        unicodedata.normalize("NFKD", dataset_name).encode("ascii", "ignore").decode().lower()
    )
    slug = _SLUG.sub("-", ascii_name).strip("-")[:60].strip("-") or "dataset"
    return f"report-{slug}-{run_id[:8]}.html"
