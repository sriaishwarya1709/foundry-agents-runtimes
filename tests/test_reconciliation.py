import pytest
from fastapi.testclient import TestClient

from invoice_demo import reconciliation
from invoice_demo.api import app
from invoice_demo.reconciliation import Evidence, Invoice, PurchaseOrder, check_invoice


@pytest.mark.parametrize(
    ("invoice_id", "status", "variance"),
    [
        ("INV-104", "AMOUNT_MISMATCH", 25000),
        ("INV-105", "MISSING_PO", None),
        ("INV-106", "CURRENCY_MISMATCH", None),
        ("INV-108", "MATCH", 0),
        ("INV-999", "MISSING_INVOICE", None),
    ],
)
def test_financial_evidence(invoice_id, status, variance):
    result = check_invoice(invoice_id)
    assert result.status == status
    assert result.variance_minor_units == variance
    if invoice_id == "INV-104":
        assert (
            result.to_dict()
            == Evidence(
                "INV-104",
                "PO-501",
                "AMOUNT_MISMATCH",
                currency="USD",
                invoice_total_minor_units=125000,
                po_total_minor_units=100000,
                po_currency="USD",
                variance_minor_units=25000,
                detail="Invoice exceeds PO.",
            ).to_dict()
        )


def test_negative_variance_is_not_overpayment(monkeypatch):
    monkeypatch.setitem(reconciliation.INVOICES, "INV-109", Invoice("PO-505", 9999))
    monkeypatch.setitem(reconciliation.PURCHASE_ORDERS, "PO-505", PurchaseOrder(10000))
    evidence = check_invoice("INV-109")
    assert evidence.variance_minor_units == -1
    assert evidence.detail == "Invoice is below PO."


def test_timeout_is_not_missing_po():
    with pytest.raises(TimeoutError):
        check_invoice("INV-107")
    with TestClient(app) as client:
        response = client.get("/invoices/INV-107/check")
    assert response.status_code == 200
    assert response.json()["status"] == "VERIFICATION_UNAVAILABLE"
    assert response.json()["po_total_minor_units"] is None


def test_api_rejects_invalid_identifiers():
    with TestClient(app) as client:
        response = client.get("/invoices/not-an-invoice/check")
    assert response.status_code == 422
