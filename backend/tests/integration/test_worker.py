"""The real worker: Procrastinate claims the API's job, runs Phase A (real ingestion) and then
the Phase B job that Phase A deferred, and the run ends ``succeeded``."""

from __future__ import annotations

import asyncio

import httpx
import pytest
from sqlalchemy.ext.asyncio import AsyncEngine

from insightfulcell.config import Settings
from insightfulcell.storage.s3 import S3ObjectStore
from insightfulcell.worker.__main__ import parse_args
from insightfulcell.worker.runtime import build_worker
from tests.conftest import Identity
from tests.integration.conftest import AuthHeaders, OwnerSql
from tests.integration.helpers import csv_bytes
from tests.integration.runs_support import create_dataset, start_run

pytestmark = pytest.mark.integration


async def test_worker_processes_both_phases(
    client: httpx.AsyncClient,
    auth: AuthHeaders,
    alice: Identity,
    settings: Settings,
    engine: AsyncEngine,
    owner_sql: OwnerSql,
) -> None:
    dataset_id = (await create_dataset(client, auth(alice), data=csv_bytes(25)))["dataset"]["id"]
    run_id = (await start_run(client, auth(alice), dataset_id)).json()["id"]

    async with build_worker(settings, engine=engine, store=S3ObjectStore(settings)) as worker:
        await asyncio.wait_for(
            worker.run(wait=False, install_signal_handlers=False, concurrency=1), 60
        )

    run = (await client.get(f"/v1/runs/{run_id}", headers=auth(alice))).json()
    assert run["status"] == "succeeded", run
    assert run["stages"][0]["status"] == "succeeded"
    assert run["stages"][0]["output"]["rows"] == 25
    # Both phase jobs finished, and finished jobs are deleted.
    assert owner_sql("SELECT count(*) FROM procrastinate.procrastinate_jobs") == [(0,)]
    dataset = (await client.get(f"/v1/datasets/{dataset_id}", headers=auth(alice))).json()
    assert dataset["dataset"]["latest_run"]["status"] == "succeeded"


async def test_worker_startup_ensures_event_partitions(
    settings: Settings, engine: AsyncEngine, owner_sql: OwnerSql
) -> None:
    month = owner_sql(
        "SELECT format('run_events_y%sm%s', to_char(d, 'YYYY'), to_char(d, 'MM')) "
        "FROM (SELECT date_trunc('month', now()) AS d) s"
    )[0][0]
    owner_sql(f"DROP TABLE app.{month}")
    async with build_worker(settings, engine=engine, store=S3ObjectStore(settings)):
        pass
    assert owner_sql("SELECT to_regclass(:name) IS NOT NULL", name=f"app.{month}") == [(True,)]


class TestCli:
    def test_defaults_to_every_queue(self) -> None:
        args = parse_args([])
        assert args.queues == ["standard", "large", "maintenance"]
        assert args.concurrency is None

    def test_accepts_a_subset(self) -> None:
        assert parse_args(["--queues", "large", "--concurrency", "2"]).queues == ["large"]

    @pytest.mark.parametrize(
        "argv", [["--queues", "nope"], ["--queues", ","], ["--concurrency", "0"]]
    )
    def test_rejects_bad_arguments(self, argv: list[str]) -> None:
        with pytest.raises(SystemExit):
            parse_args(argv)
