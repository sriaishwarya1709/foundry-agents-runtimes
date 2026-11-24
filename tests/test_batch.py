import asyncio
from unittest.mock import AsyncMock

import pytest
from pydantic import ValidationError

from invoice_demo.batch import BatchRequest, run_batch
from invoice_demo.reconciliation import check_invoice
from invoice_demo.storage import SQLiteStore


async def collect(request, store, check, summary, **kwargs):
    return [event async for event in run_batch(request, store, check, summary, **kwargs)]


def test_interruption_resumes_saved_results_and_reuses_report(tmp_path):
    request = BatchRequest(job_id="resume-demo", invoice_ids=["INV-104", "INV-105"])
    database = tmp_path / "jobs.sqlite"
    check = AsyncMock(side_effect=check_invoice)
    summary = AsyncMock(return_value="Human review required.")

    async def interrupt():
        generator = run_batch(request, SQLiteStore(database), check, summary)
        first = await anext(generator)
        assert first["phase"] == "checkpoint_saved"
        await generator.aclose()

    asyncio.run(interrupt())
    check.reset_mock()
    result = asyncio.run(collect(request, SQLiteStore(database), check, summary))
    assert result[0]["phase"] == "checkpoint_reused"
    check.assert_awaited_once_with("INV-105")
    report = result[-1]["report"]
    assert report["invoice_results"][0]["variance_minor_units"] == 25000
    assert report["approval_performed"] is False
    check.reset_mock()
    summary.reset_mock()
    repeated = asyncio.run(collect(request, SQLiteStore(database), check, summary))
    assert repeated == [{"phase": "report_reused", "report": report}]
    check.assert_not_awaited()
    summary.assert_not_awaited()


def test_job_identity_cannot_change_manifest(tmp_path):
    store = SQLiteStore(tmp_path / "jobs.sqlite")
    check = AsyncMock(side_effect=check_invoice)
    summary = AsyncMock(return_value="Review.")
    asyncio.run(
        collect(
            BatchRequest(job_id="fixed", invoice_ids=["INV-104"]),
            store,
            check,
            summary,
        )
    )
    with pytest.raises(ValueError, match="different immutable"):
        asyncio.run(
            collect(
                BatchRequest(job_id="fixed", invoice_ids=["INV-105"]),
                store,
                check,
                summary,
            )
        )


def test_timeout_retries_boundedly_and_leaves_invoice_unfinished(tmp_path):
    request = BatchRequest(job_id="timeout", invoice_ids=["INV-104", "INV-107"])
    store = SQLiteStore(tmp_path / "jobs.sqlite")
    check = AsyncMock(side_effect=check_invoice)
    summary = AsyncMock(return_value="Review.")
    with pytest.raises(TimeoutError):
        asyncio.run(collect(request, store, check, summary, retry_delay=0))
    assert check.await_count == 4
    assert store.read("jobs/timeout/checkpoints/INV-104.json") is not None
    assert store.read("jobs/timeout/checkpoints/INV-107.json") is None
    assert store.read("jobs/timeout/report.json") is None
    summary.assert_not_awaited()


def test_concurrent_runs_commit_only_one_report(tmp_path):
    store = SQLiteStore(tmp_path / "jobs.sqlite")
    request = BatchRequest(job_id="concurrent", invoice_ids=["INV-104"])
    check = AsyncMock(side_effect=check_invoice)
    arrived = 0

    async def run_both():
        ready = asyncio.Event()

        async def summary(results):
            nonlocal arrived
            arrived += 1
            if arrived == 2:
                ready.set()
            await ready.wait()
            return f"Reviewer draft {arrived}"

        return await asyncio.gather(
            collect(request, store, check, summary),
            collect(request, store, check, summary),
        )

    events = asyncio.run(run_both())
    reports = [stream[-1] for stream in events]
    assert {event["phase"] for event in reports} == {"report_created", "report_reused"}
    assert reports[0]["report"] == reports[1]["report"]


@pytest.mark.parametrize("ids", [[], ["INV-104", "INV-104"], ["../../secret"], ["INV-1"] * 101])
def test_invalid_manifests_are_rejected(ids):
    with pytest.raises(ValidationError):
        BatchRequest(job_id="valid", invoice_ids=ids)
