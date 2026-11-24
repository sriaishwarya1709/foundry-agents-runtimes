import re
from dataclasses import asdict, dataclass
from typing import Literal

Status = Literal[
    "MATCH",
    "AMOUNT_MISMATCH",
    "MISSING_PO",
    "MISSING_INVOICE",
    "CURRENCY_MISMATCH",
    "VERIFICATION_UNAVAILABLE",
]


@dataclass(frozen=True)
class Invoice:
    po_id: str
    total_minor_units: int
    currency: str = "USD"


@dataclass(frozen=True)
class PurchaseOrder:
    total_minor_units: int
    currency: str = "USD"


@dataclass(frozen=True)
class Evidence:
    invoice_id: str
    po_id: str | None
    status: Status
    currency: str | None = None
    invoice_total_minor_units: int | None = None
    po_total_minor_units: int | None = None
    po_currency: str | None = None
    variance_minor_units: int | None = None
    human_review_required: bool = True
    detail: str = ""

    def to_dict(self) -> dict:
        return asdict(self)


INVOICES = {
    "INV-104": Invoice("PO-501", 125000),
    "INV-105": Invoice("PO-999", 45000),
    "INV-106": Invoice("PO-502", 90000),
    "INV-107": Invoice("PO-503", 30000),
    "INV-108": Invoice("PO-504", 75000),
}
PURCHASE_ORDERS = {
    "PO-501": PurchaseOrder(100000),
    "PO-502": PurchaseOrder(90000, "EUR"),
    "PO-503": PurchaseOrder(30000),
    "PO-504": PurchaseOrder(75000),
}


def validate_invoice_id(invoice_id: str) -> None:
    if not isinstance(invoice_id, str) or not re.fullmatch(r"INV-[0-9]{1,12}", invoice_id):
        raise ValueError("invoice_id must have the form INV-104 (at most 12 digits)")


def lookup_purchase_order(po_id: str) -> PurchaseOrder | None:
    if po_id == "PO-503":
        raise TimeoutError("Synthetic purchase-order service timeout")
    return PURCHASE_ORDERS.get(po_id)


def check_invoice(invoice_id: str) -> Evidence:
    validate_invoice_id(invoice_id)
    invoice = INVOICES.get(invoice_id)
    if invoice is None:
        return Evidence(invoice_id, None, "MISSING_INVOICE", detail="Invoice not found.")
    common = {
        "invoice_id": invoice_id,
        "po_id": invoice.po_id,
        "currency": invoice.currency,
        "invoice_total_minor_units": invoice.total_minor_units,
    }
    # A transient lookup error must never become missing-record evidence.
    order = lookup_purchase_order(invoice.po_id)
    if order is None:
        return Evidence(**common, status="MISSING_PO", detail="Linked purchase order not found.")
    common.update(po_total_minor_units=order.total_minor_units, po_currency=order.currency)
    if invoice.currency != order.currency:
        return Evidence(
            **common, status="CURRENCY_MISMATCH", detail="Different currencies; no subtraction."
        )
    variance = invoice.total_minor_units - order.total_minor_units
    return Evidence(
        **common,
        status="AMOUNT_MISMATCH" if variance else "MATCH",
        variance_minor_units=variance,
        human_review_required=bool(variance),
        detail="Invoice exceeds PO."
        if variance > 0
        else ("Invoice is below PO." if variance < 0 else "Totals match; no approval performed."),
    )
