import logging

from fastapi import FastAPI, HTTPException
from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor

from invoice_demo.reconciliation import INVOICES, Evidence, check_invoice
from invoice_demo.telemetry import configure_telemetry, tracer

logger = logging.getLogger("invoice_demo")


configure_telemetry()
app = FastAPI(title="Synthetic Invoice Reconciliation")
FastAPIInstrumentor.instrument_app(app)


@app.get("/health")
def health() -> dict:
    return {"status": "ok", "dataset": "synthetic-invoices-v1"}


@app.get("/invoices/{invoice_id}/check", operation_id="check_invoice")
def check(invoice_id: str) -> dict:
    with tracer.start_as_current_span("check_invoice") as span:
        span.set_attribute("invoice.id", invoice_id)
        try:
            evidence = check_invoice(invoice_id)
        except ValueError as exc:
            logger.warning("Rejected invalid invoice identifier: %s", exc)
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        except TimeoutError as exc:
            logger.warning("Purchase-order verification unavailable for %s: %s", invoice_id, exc)
            invoice = INVOICES[invoice_id]
            evidence = Evidence(
                invoice_id,
                invoice.po_id,
                "VERIFICATION_UNAVAILABLE",
                currency=invoice.currency,
                invoice_total_minor_units=invoice.total_minor_units,
                detail="Purchase-order service timed out; retry verification later.",
            )
            span.record_exception(exc)
        span.set_attribute("invoice.status", evidence.status)
        span.add_event(
            "reconciliation.result",
            {key: value for key, value in evidence.to_dict().items() if value is not None},
        )
        logger.info("check_invoice invoice=%s status=%s", invoice_id, evidence.status)
        return evidence.to_dict()
