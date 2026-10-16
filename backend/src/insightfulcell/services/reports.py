"""Report use cases for the API: a finished run's report and dashboard with the facts they cite,
each with its test (after the run's false-discovery control) and its sensitivity check."""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Final, Literal
from uuid import UUID

from sqlalchemy import select

from insightfulcell.db.models import RunFact, RunReport, TestLog
from insightfulcell.db.tenant import TenantDatabase
from insightfulcell.domain.errors import DashboardNotAvailable, ReportNotReady
from insightfulcell.domain.security import Action, Principal, require
from insightfulcell.engine.analysis.dashboard import Dashboard
from insightfulcell.engine.analysis.report import Report
from insightfulcell.engine.analysis.sensitivity import NOTES
from insightfulcell.exports.document import Document, build_document
from insightfulcell.exports.html import filename, render_report
from insightfulcell.exports.markdown import render_markdown
from insightfulcell.exports.pdf import render_pdf
from insightfulcell.exports.word import render_docx
from insightfulcell.services import run_store
from insightfulcell.services.dto import RunDetail

ExportFormat = Literal["md", "docx", "pdf"]

#: Media type and renderer per downloadable format (HTML has its own path: a template).
EXPORTS: Final[dict[str, tuple[str, Callable[[Document], bytes]]]] = {
    "md": ("text/markdown; charset=utf-8", lambda doc: render_markdown(doc).encode("utf-8")),
    "docx": (
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        render_docx,
    ),
    "pdf": ("application/pdf", render_pdf),
}


@dataclass(frozen=True, slots=True)
class ReportView:
    run: RunDetail
    revision: int
    created_at: datetime
    document: dict[str, Any]
    facts: list[dict[str, Any]]
    #: The dashboard of the same revision (None for reports written before dashboards).
    dashboard: dict[str, Any] | None = None


class ReportService:
    def __init__(self, db: TenantDatabase) -> None:
        self._db = db

    async def get_dashboard(self, principal: Principal, run_id: UUID) -> ReportView:
        """The dashboard with the report it was assembled from (the facts and names)."""
        view = await self.get_report(principal, run_id)
        if view.dashboard is None:
            raise DashboardNotAvailable(
                "This report was written before dashboards were part of the analysis.",
                hint="Open the report, or start a new analysis of the dataset for a dashboard.",
            )
        return view

    async def export_html(self, principal: Principal, run_id: UUID) -> tuple[str, str]:
        """The report, its dashboard and every fact as one self-contained HTML file:
        ``(filename, html)``."""
        view = await self.get_report(principal, run_id)
        report = Report.model_validate(view.document)
        board = Dashboard.model_validate(view.dashboard) if view.dashboard is not None else None
        html = render_report(
            report=report,
            dashboard=board,
            facts=view.facts,
            run_id=str(view.run.run.id),
            written_at=view.created_at,
        )
        return filename(report.dataset_name, str(view.run.run.id)), html

    async def export_file(
        self, principal: Principal, run_id: UUID, fmt: ExportFormat
    ) -> tuple[str, bytes, str]:
        """The report as Markdown, Word or PDF: ``(filename, content, media type)``. Built from
        the same stored documents as the HTML export (``exports.document``)."""
        view = await self.get_report(principal, run_id)
        report = Report.model_validate(view.document)
        board = Dashboard.model_validate(view.dashboard) if view.dashboard is not None else None
        doc = build_document(
            report=report,
            dashboard=board,
            facts=view.facts,
            run_id=str(view.run.run.id),
            written_at=view.created_at,
        )
        media_type, render = EXPORTS[fmt]
        # Charts become images and pages are laid out: CPU work, kept off the event loop.
        content = await asyncio.to_thread(render, doc)
        return filename(report.dataset_name, str(view.run.run.id), fmt), content, media_type

    async def get_report(self, principal: Principal, run_id: UUID) -> ReportView:
        require(principal, Action.RUN_READ)
        async with self._db.transaction(principal) as session:
            detail = await run_store.run_detail(session, principal.org_id, run_id)
            row = (
                await session.execute(
                    select(RunReport)
                    .where(RunReport.run_id == run_id, RunReport.org_id == principal.org_id)
                    .order_by(RunReport.revision.desc())
                    .limit(1)
                )
            ).scalar_one_or_none()
            if row is None:
                raise ReportNotReady(
                    "This analysis has not written its report yet.",
                    hint="The report appears when the analysis has finished.",
                )
            facts = (
                await session.execute(
                    select(RunFact).where(
                        RunFact.run_id == run_id, RunFact.org_id == principal.org_id
                    )
                )
            ).scalars()
            rechecks = {
                t.fact_id: t
                for t in (
                    await session.execute(
                        select(TestLog).where(
                            TestLog.run_id == run_id,
                            TestLog.org_id == principal.org_id,
                            TestLog.purpose == "sensitivity",
                        )
                    )
                ).scalars()
            }
            names: dict[str, str] = dict(row.document.get("column_names") or {})
            fact_rows = sorted(
                (_fact(f, names, rechecks.get(f.fact_id)) for f in facts),
                key=lambda f: int(str(f["id"])[1:]),
            )
            return ReportView(
                run=detail,
                revision=row.revision,
                created_at=row.created_at,
                document=row.document,
                facts=fact_rows,
                dashboard=row.dashboard,
            )


def _fact(fact: RunFact, names: dict[str, str], check: TestLog | None) -> dict[str, Any]:
    tested = fact.tier != "descriptive" and fact.p_value is not None
    return {
        "id": fact.fact_id,
        "analysis_id": fact.analysis_id,
        "kind": fact.kind,
        "columns": [{"name": c, "display_name": names.get(c, c)} for c in fact.columns],
        "rows": fact.rows,
        "method": fact.method,
        "statement": fact.statement,
        "values": fact.value,
        "tier": fact.tier,
        "test": {
            "method": fact.test_method or "",
            "effect_name": fact.effect_name or "",
            "effect": fact.effect if fact.effect is not None else 0.0,
            "ci_low": fact.ci_low,
            "ci_high": fact.ci_high,
            "p_value": fact.p_value,
            "q_value": fact.q_value if fact.q_value is not None else 1.0,
        }
        if tested
        else None,
        "check": {
            "outcome": check.outcome,
            "note": NOTES.get(check.outcome, ""),  # type: ignore[call-overload]
            "effect": check.effect,
            "p_value": check.p_value,
        }
        if check is not None and check.outcome
        else None,
        "content_hash": fact.content_hash,
        "exploratory": bool((fact.provenance or {}).get("exploratory")),
    }
