import asyncio
import logging
from collections.abc import AsyncIterator, Awaitable, Callable
from datetime import datetime, timezone

import httpx
from opentelemetry.propagate import inject
from pydantic import BaseModel, ConfigDict, Field, TypeAdapter, field_validator

from invoice_demo.reconciliation import Evidence, validate_invoice_id
from invoice_demo.storage import Store
from invoice_demo.telemetry import tracer

logger = logging.getLogger("invoice_demo")
evidence_adapter = TypeAdapter(Evidence)


class BatchRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    job_id: str = Field(pattern=r"^[a-zA-Z0-9][a-zA-Z0-9_-]{0,63}$")
    invoice_ids: list[str] = Field(min_length=1, max_length=100)

    @field_validator("invoice_ids")
    @classmethod
    def validate_manifest(cls, ids: list[str]) -> list[str]:
        for invoice_id in ids:
            validate_invoice_id(invoice_id)
        if len(set(ids)) != len(ids):
            raise ValueError("Duplicate invoice IDs are not allowed")
        return ids


class BatchInterrupted(Exception):
    pass


async def remote_check_invoice(endpoint: str, invoice_id: str) -> Evidence:
    headers: dict[str, str] = {}
    inject(headers)
    async with httpx.AsyncClient(timeout=30) as client:
        try:
            response = await client.get(
                f"{endpoint.rstrip('/')}/invoices/{invoice_id}/check",
                headers=headers,
            )
        except (httpx.TimeoutException, httpx.ConnectError) as exc:
            raise TimeoutError("Reconciliation service is temporarily unavailable") from exc
    if response.status_code in (429, 502, 503, 504):
        raise TimeoutError(f"Reconciliation service returned HTTP {response.status_code}")
    response.raise_for_status()
    result = evidence_adapter.validate_python(response.json())
    if result.invoice_id != invoice_id:
        raise ValueError("Reconciliation service returned evidence for the wrong invoice")
    if result.status == "VERIFICATION_UNAVAILABLE":
        raise TimeoutError(result.detail)
    return result


async def run_batch(
    request: BatchRequest,
    store: Store,
    check: Callable[[str], Awaitable[Evidence]],
    summarize: Callable[[list[dict]], Awaitable[str]],
    *,
    interrupted: Callable[[], bool] = lambda: False,
    retry_delay: float = 1,
) -> AsyncIterator[dict]:
    prefix = f"jobs/{request.job_id}"
    manifest = {"job_id": request.job_id, "invoice_ids": request.invoice_ids}
    await asyncio.to_thread(store.create, f"{prefix}/manifest.json", manifest)
    if await asyncio.to_thread(store.read, f"{prefix}/manifest.json") != manifest:
        raise ValueError("job_id already belongs to a different immutable invoice manifest")
    existing_report = await asyncio.to_thread(store.read, f"{prefix}/report.json")
    if existing_report is not None:
        yield {"phase": "report_reused", "report": existing_report}
        return
    results = []
    for invoice_id in request.invoice_ids:
        if interrupted():
            raise BatchInterrupted()
        key = f"{prefix}/checkpoints/{invoice_id}.json"
        result = await asyncio.to_thread(store.read, key)
        resumed = result is not None
        with tracer.start_as_current_span("batch.invoice") as span:
            span.set_attribute("job.id", request.job_id)
            span.set_attribute("invoice.id", invoice_id)
            span.set_attribute("checkpoint.reused", resumed)
            if result is None:
                for attempt in range(3):
                    if interrupted():
                        raise BatchInterrupted()
                    try:
                        evidence = await check(invoice_id)
                        if evidence.status == "VERIFICATION_UNAVAILABLE":
                            raise TimeoutError(evidence.detail)
                        if evidence.invoice_id != invoice_id:
                            raise ValueError("Evidence invoice ID does not match the manifest")
                        result = evidence.to_dict()
                        break
                    except TimeoutError:
                        logger.warning(
                            "Verification unavailable job=%s invoice=%s attempt=%s",
                            request.job_id,
                            invoice_id,
                            attempt + 1,
                            exc_info=True,
                        )
                        if attempt == 2:
                            raise
                        await asyncio.sleep(retry_delay * (2**attempt))
                if interrupted():
                    raise BatchInterrupted()
                await asyncio.to_thread(store.create, key, result)
                result = await asyncio.to_thread(store.read, key)
            if result is None:
                raise RuntimeError("Checkpoint disappeared after a successful create")
            span.set_attribute("invoice.status", result["status"])
        results.append(result)
        logger.info(
            "Batch progress job=%s invoice=%s resumed=%s", request.job_id, invoice_id, resumed
        )
        yield {"phase": "checkpoint_reused" if resumed else "checkpoint_saved", "result": result}
    if interrupted():
        raise BatchInterrupted()
    with tracer.start_as_current_span("batch.report") as span:
        span.set_attribute("job.id", request.job_id)
        summary = await summarize(results)
        if not summary.strip():
            raise ValueError("The model returned an empty reviewer summary")
        if interrupted():
            raise BatchInterrupted()
        report = {
            **manifest,
            "report_id": f"{prefix}/report.json",
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "invoice_results": results,
            "reviewer_summary": summary,
            "approval_performed": False,
        }
        created = await asyncio.to_thread(store.create, f"{prefix}/report.json", report)
        report = await asyncio.to_thread(store.read, f"{prefix}/report.json")
        if report is None:
            raise RuntimeError("Report disappeared after a successful create")
        span.set_attribute("report.created", created)
        span.add_event("report.persisted", {"report.id": report["report_id"]})
    yield {"phase": "report_created" if created else "report_reused", "report": report}
