import argparse
import asyncio
import base64
import json
import logging
import os
from pathlib import Path

from azure.ai.projects import AIProjectClient
from azure.identity import DefaultAzureCredential

from invoice_demo.batch import BatchRequest, run_batch
from invoice_demo.reconciliation import check_invoice
from invoice_demo.storage import BlobStore, SQLiteStore


def generate_pdfs(directory: Path) -> None:
    from reportlab.pdfgen.canvas import Canvas

    directory.mkdir(parents=True, exist_ok=True)
    for invoice_id, po, amount, currency in [
        ("INV-104", "PO-501", "1250.00", "USD"),
        ("INV-105", "PO-999", "450.00", "USD"),
        ("INV-106", "PO-502", "900.00", "USD"),
        ("INV-107", "PO-503", "300.00", "USD"),
        ("INV-108", "PO-504", "750.00", "USD"),
    ]:
        path = directory / f"{invoice_id}.pdf"
        canvas = Canvas(str(path))
        for offset, line in enumerate(
            [
                "SYNTHETIC DEMO INVOICE - NOT PAYABLE",
                f"Invoice ID: {invoice_id}",
                f"Purchase order: {po}",
                f"Total: {amount} {currency}",
            ]
        ):
            canvas.drawString(72, 740 - offset * 30, line)
        canvas.save()
        print(path)
    canvas = Canvas(str(directory / "unreadable-id.pdf"))
    canvas.drawString(
        72, 740, "SYNTHETIC INVOICE: identifier is unreadable; human review required."
    )
    canvas.save()


def invoke(args: argparse.Namespace) -> None:
    with DefaultAzureCredential() as credential:
        with AIProjectClient(
            endpoint=os.environ["AZURE_AI_PROJECT_ENDPOINT"],
            credential=credential,
            allow_preview=True,
        ) as project:
            with project.get_openai_client(agent_name=args.agent) as client:
                conversation = client.conversations.create()
                if args.agent == "invoice-hosted":
                    input_value = BatchRequest(
                        job_id=args.job_id,
                        invoice_ids=args.invoices,
                    ).model_dump_json()
                elif args.agent == "invoice-copilot":
                    files = sorted(args.pdf_dir.glob("*.pdf"))
                    if not files:
                        raise ValueError("No PDFs found; run the pdfs command first")
                    content = [
                        {
                            "type": "input_text",
                            "text": (
                                "Review every attached invoice PDF "
                                "and create the exception reports. "
                                "Account for all filenames, and return downloadable Foundry Files."
                            ),
                        }
                    ]
                    for path in files:
                        content.append(
                            {
                                "type": "input_file",
                                "filename": path.name,
                                "file_data": "data:application/pdf;base64,"
                                + (base64.b64encode(path.read_bytes()).decode("ascii")),
                            }
                        )
                    input_value = [{"role": "user", "content": content}]
                else:
                    input_value = f"Check {args.invoices[0]} and explain any discrepancy."
                kwargs = {"conversation": conversation.id, "input": input_value}
                if args.agent == "invoice-hosted":
                    kwargs.update(background=True, store=True)
                response = client.responses.create(**kwargs)
                args.output_dir.mkdir(parents=True, exist_ok=True)
                target = args.output_dir / f"{args.agent}-{response.id}.json"
                target.write_text(response.model_dump_json(indent=2), encoding="utf-8")
                print(
                    json.dumps(
                        {
                            "conversation_id": conversation.id,
                            "response_id": response.id,
                            "status": response.status,
                            "saved_response": str(target),
                        },
                        indent=2,
                    )
                )
                print(response.output_text)
                if response.status in ("failed", "cancelled", "incomplete"):
                    raise RuntimeError(f"Agent returned {response.status}: {response.error}")


async def local_batch(args: argparse.Namespace) -> None:
    async def check(invoice_id: str):
        return check_invoice(invoice_id)

    async def summary(results: list[dict]) -> str:
        return (
            "Local deterministic summary (no model call): "
            + "; ".join(f"{row['invoice_id']}: {row['status']}" for row in results)
            + ". Exceptions require human review. No approval or payment performed."
        )

    count = 0
    async for event in run_batch(
        BatchRequest(job_id=args.job_id, invoice_ids=args.invoices),
        SQLiteStore(args.database),
        check,
        summary,
    ):
        print(json.dumps(event, indent=2))
        if event["phase"] == "checkpoint_saved":
            count += 1
            if args.stop_after and count == args.stop_after:
                raise InterruptedError(
                    "Demo interruption after a durable checkpoint. Rerun without --stop-after."
                )


def main() -> None:
    logging.basicConfig(level=logging.INFO)
    parser = argparse.ArgumentParser(description="Synthetic Foundry invoice runtime demo")
    subparsers = parser.add_subparsers(dest="command", required=True)
    pdfs = subparsers.add_parser("pdfs")
    pdfs.add_argument("--directory", type=Path, default=Path("demo-output/pdfs"))
    local = subparsers.add_parser("local-batch")
    local.add_argument("--database", type=Path, default=Path("demo-output/checkpoints.sqlite"))
    local.add_argument("--job-id", default="blog-demo")
    local.add_argument(
        "--invoices", nargs="+", default=["INV-104", "INV-105", "INV-106", "INV-108"]
    )
    local.add_argument("--stop-after", type=int)
    call = subparsers.add_parser("invoke")
    call.add_argument("agent", choices=["invoice-prompt", "invoice-copilot", "invoice-hosted"])
    call.add_argument("--job-id", default="blog-demo")
    call.add_argument("--invoices", nargs="+", default=["INV-104", "INV-105", "INV-106", "INV-108"])
    call.add_argument("--pdf-dir", type=Path, default=Path("demo-output/pdfs"))
    call.add_argument("--output-dir", type=Path, default=Path("demo-output/responses"))
    poll = subparsers.add_parser("response")
    poll.add_argument("response_id")
    poll.add_argument("--agent", default="invoice-hosted")
    report = subparsers.add_parser("report")
    report.add_argument("job_id")
    args = parser.parse_args()
    if args.command == "pdfs":
        generate_pdfs(args.directory)
    elif args.command == "invoke":
        invoke(args)
    elif args.command == "local-batch":
        asyncio.run(local_batch(args))
    elif args.command == "response":
        with DefaultAzureCredential() as credential:
            with AIProjectClient(
                endpoint=os.environ["AZURE_AI_PROJECT_ENDPOINT"],
                credential=credential,
                allow_preview=True,
            ) as project:
                with project.get_openai_client(agent_name=args.agent) as client:
                    response = client.responses.retrieve(args.response_id)
                    print(response.model_dump_json(indent=2))
                    if response.status in ("failed", "cancelled", "incomplete"):
                        raise RuntimeError(f"Agent returned {response.status}: {response.error}")
    elif args.command == "report":
        BatchRequest(job_id=args.job_id, invoice_ids=["INV-104"])
        store = BlobStore(os.environ["CHECKPOINT_STORAGE_ENDPOINT"])
        value = store.read(f"jobs/{args.job_id}/report.json")
        if value is None:
            raise FileNotFoundError("Report has not been committed")
        print(json.dumps(value, indent=2))


if __name__ == "__main__":
    main()
