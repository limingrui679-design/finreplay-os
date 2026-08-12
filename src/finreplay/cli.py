"""Command-line entry point. Commands are added only when their evidence gate is runnable."""

from pathlib import Path
from typing import Annotated

import typer

from finreplay import __version__
from finreplay.adapters import FDICFinancialsAdapter
from finreplay.adapters.base import SafeHttpClient
from finreplay.engines import TimeVault
from finreplay.storage import ContentAddressedStore, write_live_verification

app = typer.Typer(
    add_completion=False,
    no_args_is_help=True,
    help="Point-in-time financial-system replay and adversarial research CLI.",
)


@app.callback()
def main() -> None:
    """Run FinReplay OS research and verification commands."""


@app.command()
def version() -> None:
    """Print the installed package version."""

    typer.echo(__version__)


@app.command("fetch-fdic-financials")
def fetch_fdic_financials(
    cert: Annotated[int, typer.Option(min=1, help="FDIC certificate number.")],
    fields: Annotated[
        str,
        typer.Option(help="Comma-separated documented FDIC financial fields."),
    ] = "CERT,REPDTE,ASSET,DEP,DEPUNINS",
    database: Annotated[
        Path,
        typer.Option(help="Local TimeVault database; raw data directories are gitignored."),
    ] = Path("data/silver/timevault.duckdb"),
    raw_store: Annotated[
        Path,
        typer.Option(help="Content-addressed local raw-response store."),
    ] = Path("data/raw/artifacts"),
    receipt_directory: Annotated[
        Path,
        typer.Option(
            help="Directory for commit-safe machine receipts without raw response content."
        ),
    ] = Path("verification/live/fdic"),
    page_size: Annotated[int, typer.Option(min=1, max=10_000)] = 1_000,
) -> None:
    """Fetch current FDIC financials with a latest-only temporal warning."""

    normalized_fields = tuple(part.strip() for part in fields.split(",") if part.strip())
    database = database.expanduser().resolve()
    database.parent.mkdir(parents=True, exist_ok=True)
    user_agent = "FinReplayOS/0.1 research connector (https://github.com/)"
    with SafeHttpClient(user_agent=user_agent) as http:
        batch = FDICFinancialsAdapter(http).fetch_all(
            cert=cert,
            fields=normalized_fields,
            page_size=page_size,
        )
    store = ContentAddressedStore(raw_store)
    stored = tuple(store.put(artifact) for artifact in batch.artifacts)
    with TimeVault(database) as vault:
        append_receipt = vault.append(batch.records)
        manifest = vault.manifest()
    receipt_path = write_live_verification(
        output_directory=receipt_directory,
        batch=batch,
        stored_artifacts=stored,
        append_receipt=append_receipt,
        vault_manifest=manifest,
    )
    typer.echo(
        f"retrieved={len(batch.records)} inserted={append_receipt.inserted_records} "
        f"latest_only=true receipt={receipt_path}"
    )


if __name__ == "__main__":  # pragma: no cover
    app()
