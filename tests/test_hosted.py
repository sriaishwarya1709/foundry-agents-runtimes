import json
from unittest.mock import AsyncMock

from starlette.testclient import TestClient

from invoice_demo.batch import BatchRequest
from invoice_demo.reconciliation import check_invoice
from invoice_demo.storage import SQLiteStore


def test_responses_handler_completes_and_reuses_durable_report(tmp_path, monkeypatch):
    monkeypatch.setenv("AGENTSERVER_STATE_ROOT", str(tmp_path / "server"))
    monkeypatch.setenv("CHECKPOINT_STORAGE_ENDPOINT", "https://storage.invalid")
    monkeypatch.setenv("INVOICE_API_ENDPOINT", "https://api.invalid")
    from azure.ai.agentserver import core

    monkeypatch.setattr(core, "configure_observability", lambda **kwargs: None)
    from invoice_demo import hosted

    store = SQLiteStore(tmp_path / "jobs.sqlite")
    monkeypatch.setattr(hosted, "BlobStore", lambda endpoint: store)
    monkeypatch.setattr(hosted, "configure_telemetry", lambda **kwargs: None)

    async def check(endpoint, invoice_id):
        return check_invoice(invoice_id)

    monkeypatch.setattr(hosted, "remote_check_invoice", check)
    summary = AsyncMock(return_value="INV-104 is $250.00 over PO-501. Human review required.")
    monkeypatch.setattr(hosted, "summarize", summary)
    request = BatchRequest(job_id="responses-test", invoice_ids=["INV-104"])
    test_app = hosted.ResponsesAgentServerHost(
        options=hosted.ResponsesServerOptions(resilient_background=True),
        configure_observability=None,
    )
    test_app.response_handler(hosted.handler)
    with TestClient(test_app) as client:
        response = client.post(
            "/responses",
            json={
                "input": request.model_dump_json(),
                "store": False,
            },
        )
        assert response.status_code == 200
        body = response.json()
        assert body["status"] == "completed"
        event = json.loads(body["output"][-1]["content"][0]["text"])
        assert event["phase"] == "report_created"
        assert event["report"]["invoice_results"][0]["variance_minor_units"] == 25000
        repeated = client.post(
            "/responses",
            json={
                "input": request.model_dump_json(),
                "store": False,
            },
        ).json()
    assert repeated["status"] == "completed"
    event = json.loads(repeated["output"][-1]["content"][0]["text"])
    assert event["phase"] == "report_reused"
    summary.assert_awaited_once()
