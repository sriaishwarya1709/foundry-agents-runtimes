import logging
import os
import threading

import httpx
from azure.identity import DefaultAzureCredential
from azure.monitor.opentelemetry import configure_azure_monitor
from azure.monitor.opentelemetry.exporter import AzureMonitorTraceExporter
from opentelemetry import trace
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor

logger = logging.getLogger("invoice_demo")
logger.setLevel(logging.INFO)
tracer = trace.get_tracer("invoice_demo")
_lock = threading.Lock()
_configured = False


def configure_telemetry(*, hosted: bool = False) -> None:
    global _configured
    with _lock:
        if _configured:
            return
        resource_id = os.getenv("AZURE_APPLICATION_INSIGHTS_RESOURCE_ID")
        if not resource_id:
            logger.warning("No App Insights resource ID: local telemetry stays in console.")
            _configured = True
            return
        if hosted and (
            os.getenv("APPLICATIONINSIGHTS_CONNECTION_STRING")
            or os.getenv("OTEL_EXPORTER_OTLP_ENDPOINT")
        ):
            # The AgentServer SDK already owns this exporter.
            _configured = True
            return
        credential = DefaultAzureCredential()
        token = credential.get_token("https://management.azure.com/.default")
        response = httpx.get(
            f"https://management.azure.com{resource_id}",
            params={"api-version": "2020-02-02"},
            headers={"Authorization": f"Bearer {token.token}"},
            timeout=30,
        )
        response.raise_for_status()
        connection = response.json()["properties"]["ConnectionString"]
        if hosted:
            provider = trace.get_tracer_provider()
            if not isinstance(provider, TracerProvider):
                raise RuntimeError(
                    "AgentServer must initialize its trace provider before invocation"
                )
            provider.add_span_processor(
                BatchSpanProcessor(
                    AzureMonitorTraceExporter(
                        connection_string=connection,
                        credential=credential,
                    )
                )
            )
        else:
            configure_azure_monitor(connection_string=connection, credential=credential)
        _configured = True
        logger.info("Application Insights telemetry enabled; prompt content is not recorded.")
