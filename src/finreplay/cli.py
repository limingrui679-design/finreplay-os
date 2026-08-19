"""Command-line entry point for catalogs, offline scenarios, and ReplayPacks."""

from __future__ import annotations

import json
import tempfile
import webbrowser
from pathlib import Path
from typing import Annotated

import typer

from finreplay import __version__
from finreplay.adapters import FDICFinancialsAdapter
from finreplay.adapters.base import SafeHttpClient
from finreplay.catalog import (
    AdapterCatalogEntry,
    find_capability,
    find_scenario,
    load_adapter_catalog,
    load_capability_catalog,
    load_scenario_catalog,
    load_scenario_explorer_catalog,
    run_scenario,
)
from finreplay.engines import (
    ReplayBuildResult,
    ReplayPackSpec,
    ReplayStudio,
    ReplayStudioError,
    TimeVault,
)
from finreplay.storage import ContentAddressedStore, write_live_verification

app = typer.Typer(
    add_completion=False,
    no_args_is_help=True,
    help="Point-in-time financial-system replay and adversarial research CLI.",
)
adapter_app = typer.Typer(no_args_is_help=True, help="Inspect and operate source adapters.")
scenario_app = typer.Typer(no_args_is_help=True, help="Run bundled evidence scenarios offline.")
capability_app = typer.Typer(
    no_args_is_help=True,
    help="Explore evidence-bounded analytical capabilities and curated scenarios.",
)
replaypack_app = typer.Typer(no_args_is_help=True, help="Build, verify, and open ReplayPacks.")
evidence_app = typer.Typer(no_args_is_help=True, help="Verify packaged evidence surfaces.")
app.add_typer(adapter_app, name="adapter")
app.add_typer(scenario_app, name="scenario")
app.add_typer(capability_app, name="capability")
app.add_typer(replaypack_app, name="replaypack")
app.add_typer(evidence_app, name="evidence")


@app.callback()
def main() -> None:
    """Run FinReplay OS research and verification commands."""


@app.command()
def version() -> None:
    """Print the installed package version."""

    typer.echo(__version__)


@adapter_app.command("list")
def adapter_list(
    historical_only: Annotated[
        bool,
        typer.Option("--historical-only", help="Show only historical-replay-eligible sources."),
    ] = False,
    output_json: Annotated[
        bool,
        typer.Option("--json", help="Emit machine-readable JSON."),
    ] = False,
) -> None:
    """List all 30 formal live-validation adapters and temporal boundaries."""

    catalog = load_adapter_catalog()
    entries = tuple(
        entry
        for entry in catalog.adapters
        if not historical_only or entry.historical_replay_eligible
    )
    if output_json:
        typer.echo(
            json.dumps(
                [entry.model_dump(mode="json") for entry in entries],
                ensure_ascii=False,
                indent=2,
            )
        )
        return
    typer.echo("ADAPTER\tTEMPORAL_COVERAGE\tHISTORICAL_REPLAY\tRECORDS")
    for entry in entries:
        typer.echo(
            f"{entry.adapter_id}\t{entry.temporal_coverage}\t"
            f"{str(entry.historical_replay_eligible).lower()}\t{entry.record_count}"
        )
    typer.echo(
        f"count={len(entries)} formal_live_total={catalog.adapter_count} "
        f"historical_replay_eligible={catalog.historical_replay_eligible_count}"
    )


@adapter_app.command("show")
def adapter_show(
    adapter_id: Annotated[str, typer.Argument(help="Exact adapter ID from `adapter list`.")],
) -> None:
    """Show one adapter's publisher, receipt, and replay eligibility."""

    entry = _find_adapter(adapter_id)
    typer.echo(json.dumps(entry.model_dump(mode="json"), ensure_ascii=False, indent=2))


@adapter_app.command("validate")
def adapter_validate() -> None:
    """Validate the packaged live-adapter catalog and report its claim boundary."""

    catalog = load_adapter_catalog()
    typer.echo(
        f"validated=true adapters={catalog.adapter_count} "
        f"historical_replay_eligible={catalog.historical_replay_eligible_count} "
        f"catalog_sha256={catalog.catalog_sha256}"
    )
    typer.echo(f"boundary={catalog.claim_boundary}")


@adapter_app.command("fetch")
def adapter_fetch(
    adapter_id: Annotated[
        str,
        typer.Argument(
            help="Adapter ID; the packaged generic fetch runner currently supports FDIC."
        ),
    ],
    cert: Annotated[
        int | None,
        typer.Option(min=1, help="FDIC certificate number for fdic.bankfind.financials."),
    ] = None,
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
        typer.Option(help="Directory for commit-safe receipts without raw response content."),
    ] = Path("verification/live/fdic"),
    page_size: Annotated[int, typer.Option(min=1, max=10_000)] = 1_000,
) -> None:
    """Fetch through a supported live adapter without weakening temporal labels."""

    entry = _find_adapter(adapter_id)
    if entry.adapter_id != "fdic.bankfind.financials":
        typer.echo(
            "No generic parameter contract is packaged for this adapter. "
            "Use `adapter show` for its verified receipt and the adapter API for source-specific "
            "parameters.",
            err=True,
        )
        raise typer.Exit(code=2)
    if cert is None:
        raise typer.BadParameter(
            "is required for fdic.bankfind.financials",
            param_hint="--cert",
        )
    _fetch_fdic_financials(
        cert=cert,
        fields=fields,
        database=database,
        raw_store=raw_store,
        receipt_directory=receipt_directory,
        page_size=page_size,
    )


@scenario_app.command("list")
def scenario_list(
    output_json: Annotated[
        bool,
        typer.Option("--json", help="Emit machine-readable JSON."),
    ] = False,
) -> None:
    """List the 30 bundled, byte-locked offline scenario runners."""

    catalog = load_scenario_catalog()
    if output_json:
        typer.echo(
            json.dumps(
                [entry.model_dump(mode="json") for entry in catalog.scenarios],
                ensure_ascii=False,
                indent=2,
            )
        )
        return
    typer.echo("SLUG\tMODE\tDECISION_TIME\tINPUT_RECORDS")
    for entry in catalog.scenarios:
        typer.echo(
            f"{entry.slug}\t{entry.mode}\t{entry.decision_time}\t{entry.distinct_input_records}"
        )
    typer.echo(f"count={catalog.scenario_count}")


@scenario_app.command("show")
def scenario_show(
    value: Annotated[str, typer.Argument(help="Scenario slug, scenario ID, or replay ID.")],
) -> None:
    """Show one bundled scenario and its recorded evidence identity."""

    try:
        entry = find_scenario(value)
    except KeyError as error:
        raise typer.BadParameter(str(error), param_hint="value") from error
    typer.echo(json.dumps(entry.model_dump(mode="json"), ensure_ascii=False, indent=2))


@scenario_app.command("explain")
def scenario_explain(
    value: Annotated[str, typer.Argument(help="Scenario slug, scenario ID, or replay ID.")],
) -> None:
    """Show the case method, decision question, dimensions, paths, and claim boundary."""

    try:
        canonical = find_scenario(value)
    except KeyError as error:
        raise typer.BadParameter(str(error), param_hint="value") from error
    explorer = load_scenario_explorer_catalog()
    entry = next(item for item in explorer.scenarios if item.slug == canonical.slug)
    pathways = [
        pathway.pathway_id
        for pathway in explorer.pathways
        if canonical.slug in pathway.scenario_slugs
    ]
    capabilities = [
        capability.capability_id
        for capability in load_capability_catalog().capabilities
        if canonical.slug in capability.scenario_slugs
    ]
    payload = {
        **entry.model_dump(mode="json"),
        "pathway_ids": pathways,
        "capability_ids": capabilities,
        "claim_boundary": explorer.claim_boundary,
    }
    typer.echo(json.dumps(payload, ensure_ascii=False, indent=2))


@scenario_app.command("pathways")
def scenario_pathways() -> None:
    """List five cross-scenario reading paths with their analytical dimensions."""

    explorer = load_scenario_explorer_catalog()
    typer.echo("PATHWAY\tSCENARIOS\tDIMENSIONS")
    for pathway in explorer.pathways:
        typer.echo(
            f"{pathway.pathway_id}\t{len(pathway.scenario_slugs)}\t"
            f"{', '.join(pathway.lens_ids)}"
        )
    typer.echo(
        f"count={len(explorer.pathways)} dimensions={len(explorer.lenses)} "
        f"scenarios={explorer.scenario_count}"
    )
    typer.echo(f"boundary={explorer.claim_boundary}")


@scenario_app.command("run")
def scenario_run(
    value: Annotated[str, typer.Argument(help="Scenario slug, scenario ID, or replay ID.")],
    destination: Annotated[Path, typer.Argument(help="New or byte-identical output directory.")],
    archive: Annotated[
        Path | None,
        typer.Option(help="Optional deterministic .zip output path."),
    ] = None,
) -> None:
    """Build one scenario from packaged inputs without network access."""

    _run_catalog_scenario(value, destination, archive=archive)


@scenario_app.command("verify")
def scenario_verify(
    value: Annotated[str, typer.Argument(help="Scenario slug, scenario ID, or replay ID.")],
) -> None:
    """Rerun one scenario offline and compare its pack hash to recorded evidence."""

    try:
        entry = find_scenario(value)
        with tempfile.TemporaryDirectory(prefix="finreplay-verify-") as directory:
            result = run_scenario(value, Path(directory) / entry.slug)
    except (KeyError, OSError, ValueError, ReplayStudioError) as error:
        typer.echo(f"Scenario verification failed: {error}", err=True)
        raise typer.Exit(code=1) from error
    if result.receipt.pack_sha256 != entry.pack_sha256:
        typer.echo(
            f"Scenario verification failed: expected {entry.pack_sha256}, "
            f"got {result.receipt.pack_sha256}",
            err=True,
        )
        raise typer.Exit(code=1)
    typer.echo(
        f"verified=true offline=true scenario={entry.slug} pack_sha256={result.receipt.pack_sha256}"
    )


@capability_app.command("list")
def capability_list(
    scope: Annotated[
        str | None,
        typer.Option(help="Optional scope: direct, transferable, or boundary_only."),
    ] = None,
    output_json: Annotated[
        bool,
        typer.Option("--json", help="Emit machine-readable JSON."),
    ] = False,
) -> None:
    """List capability paths without turning adjacent-domain limits into experience claims."""

    catalog = load_capability_catalog()
    normalized_scope = scope.strip().lower() if scope else None
    allowed_scopes = {"direct", "transferable", "boundary_only"}
    if normalized_scope is not None and normalized_scope not in allowed_scopes:
        raise typer.BadParameter(
            "must be direct, transferable, or boundary_only",
            param_hint="--scope",
        )
    entries = tuple(
        entry
        for entry in catalog.capabilities
        if normalized_scope is None or entry.scope == normalized_scope
    )
    if output_json:
        typer.echo(
            json.dumps(
                [entry.model_dump(mode="json") for entry in entries],
                ensure_ascii=False,
                indent=2,
            )
        )
        return
    typer.echo("CAPABILITY\tSCOPE\tSCENARIOS\tDISCIPLINES")
    for entry in entries:
        typer.echo(
            f"{entry.capability_id}\t{entry.scope}\t{len(entry.scenario_slugs)}\t"
            f"{', '.join(entry.disciplines)}"
        )
    typer.echo(f"count={len(entries)} capability_total={catalog.capability_count}")
    typer.echo(f"boundary={catalog.claim_boundary}")


@capability_app.command("show")
def capability_show(
    value: Annotated[str, typer.Argument(help="Capability ID or exact short title.")],
) -> None:
    """Show one capability, its curated scenarios, evidence locators, and claim limits."""

    try:
        entry = find_capability(value)
    except KeyError as error:
        raise typer.BadParameter(str(error), param_hint="value") from error
    typer.echo(json.dumps(entry.model_dump(mode="json"), ensure_ascii=False, indent=2))


@replaypack_app.command("build")
def replaypack_build(
    spec_path: Annotated[Path, typer.Argument(help="ReplayPackSpec JSON input.")],
    destination: Annotated[
        Path,
        typer.Argument(help="New or byte-identical ReplayPack directory."),
    ],
    archive: Annotated[
        Path | None,
        typer.Option(help="Optional deterministic .zip output path."),
    ] = None,
) -> None:
    """Build and verify a deterministic human- and machine-readable ReplayPack."""

    _build_replaypack(spec_path, destination, archive)


@replaypack_app.command("verify")
def replaypack_verify(
    root: Annotated[
        Path,
        typer.Argument(help="ReplayPack directory containing manifest.json."),
    ],
) -> None:
    """Verify hashes, structure, semantic invariants, and deterministic rendering."""

    _verify_replaypack(root)


@replaypack_app.command("open")
def replaypack_open(
    root: Annotated[
        Path,
        typer.Argument(help="Verified ReplayPack directory containing index.html."),
    ],
) -> None:
    """Verify a ReplayPack, then open its local human-readable report."""

    try:
        receipt = ReplayStudio().verify(root)
    except ReplayStudioError as error:
        typer.echo(f"ReplayPack verification failed: {error}", err=True)
        raise typer.Exit(code=1) from error
    report = root.expanduser().resolve() / "index.html"
    opened = webbrowser.open(report.as_uri())
    typer.echo(
        f"verified=true opened={str(opened).lower()} replay_id={receipt.replay_id} report={report}"
    )


@evidence_app.command("verify")
def evidence_verify(
    all_scenarios: Annotated[
        bool,
        typer.Option("--all-scenarios", help="Rerun and hash-check all 30 offline scenarios."),
    ] = False,
) -> None:
    """Validate packaged catalogs and optionally rerun every offline scenario."""

    adapters = load_adapter_catalog()
    scenarios = load_scenario_catalog()
    capabilities = load_capability_catalog()
    explorer = load_scenario_explorer_catalog()
    verified = 0
    if all_scenarios:
        with tempfile.TemporaryDirectory(prefix="finreplay-evidence-") as directory:
            root = Path(directory)
            for entry in scenarios.scenarios:
                result = run_scenario(entry.slug, root / entry.slug)
                if result.receipt.pack_sha256 != entry.pack_sha256:
                    typer.echo(
                        f"Evidence verification failed for {entry.slug}: hash mismatch",
                        err=True,
                    )
                    raise typer.Exit(code=1)
                verified += 1
    typer.echo(
        f"verified=true adapters={adapters.adapter_count} scenarios={scenarios.scenario_count} "
        f"capabilities={capabilities.capability_count} dimensions={len(explorer.lenses)} "
        f"pathways={len(explorer.pathways)} scenarios_rerun={verified}"
    )
    typer.echo(f"boundary={scenarios.claim_boundary}")


@app.command("demo")
def demo(
    value: Annotated[
        str,
        typer.Argument(help="Bundled scenario slug, scenario ID, or replay ID."),
    ] = "svb-2023",
    destination: Annotated[
        Path | None,
        typer.Option(help="Output directory; defaults to finreplay-demo/<scenario>."),
    ] = None,
    offline: Annotated[
        bool,
        typer.Option("--offline/--no-offline", help="Use only packaged byte-locked inputs."),
    ] = True,
    archive: Annotated[
        bool,
        typer.Option("--archive/--no-archive", help="Also create a deterministic ZIP."),
    ] = True,
    open_report: Annotated[
        bool,
        typer.Option("--open/--no-open", help="Open the verified local HTML report."),
    ] = False,
) -> None:
    """Run the three-minute offline demonstration and produce a verified ReplayPack."""

    if not offline:
        typer.echo(
            "The demo intentionally supports offline packaged inputs only; use adapter fetch for "
            "live source access.",
            err=True,
        )
        raise typer.Exit(code=2)
    try:
        entry = find_scenario(value)
    except KeyError as error:
        raise typer.BadParameter(str(error), param_hint="value") from error
    destination = destination or Path("finreplay-demo") / entry.slug
    archive_path = destination.parent / f"{destination.name}.zip" if archive else None
    result = _run_catalog_scenario(value, destination, archive=archive_path)
    if open_report:
        report = result.root / "index.html"
        opened = webbrowser.open(report.as_uri())
        typer.echo(f"opened={str(opened).lower()} report={report}")
    typer.echo(
        f"demo_complete=true offline=true scenario={entry.slug} engines=7 root={result.root}"
    )


# Backward-compatible command names from the first public release candidate.
@app.command("build-replaypack", hidden=True)
def build_replaypack_legacy(
    spec_path: Annotated[Path, typer.Argument()],
    destination: Annotated[Path, typer.Argument()],
    archive: Annotated[Path | None, typer.Option()] = None,
) -> None:
    """Build a ReplayPack (legacy alias for `replaypack build`)."""

    _build_replaypack(spec_path, destination, archive)


@app.command("verify-replaypack", hidden=True)
def verify_replaypack_legacy(root: Annotated[Path, typer.Argument()]) -> None:
    """Verify a ReplayPack (legacy alias for `replaypack verify`)."""

    _verify_replaypack(root)


@app.command("fetch-fdic-financials", hidden=True)
def fetch_fdic_financials_legacy(
    cert: Annotated[int, typer.Option(min=1)],
    fields: Annotated[str, typer.Option()] = "CERT,REPDTE,ASSET,DEP,DEPUNINS",
    database: Annotated[Path, typer.Option()] = Path("data/silver/timevault.duckdb"),
    raw_store: Annotated[Path, typer.Option()] = Path("data/raw/artifacts"),
    receipt_directory: Annotated[Path, typer.Option()] = Path("verification/live/fdic"),
    page_size: Annotated[int, typer.Option(min=1, max=10_000)] = 1_000,
) -> None:
    """Fetch FDIC financials (legacy alias for `adapter fetch`)."""

    _fetch_fdic_financials(
        cert=cert,
        fields=fields,
        database=database,
        raw_store=raw_store,
        receipt_directory=receipt_directory,
        page_size=page_size,
    )


def _find_adapter(adapter_id: str) -> AdapterCatalogEntry:
    normalized = adapter_id.strip().lower()
    matches = [
        entry for entry in load_adapter_catalog().adapters if entry.adapter_id.lower() == normalized
    ]
    if len(matches) != 1:
        raise typer.BadParameter(f"unknown adapter: {adapter_id}", param_hint="adapter_id")
    return matches[0]


def _run_catalog_scenario(
    value: str,
    destination: Path,
    *,
    archive: Path | None,
) -> ReplayBuildResult:
    try:
        result = run_scenario(value, destination, archive=archive)
    except (KeyError, OSError, ValueError, ReplayStudioError) as error:
        typer.echo(f"Scenario build failed: {error}", err=True)
        raise typer.Exit(code=1) from error
    typer.echo(
        f"verified=true offline=true replay_id={result.receipt.replay_id} "
        f"trace_id={result.receipt.trace_id} pack_sha256={result.receipt.pack_sha256} "
        f"idempotent={str(result.idempotent).lower()} root={result.root}"
    )
    if archive is not None:
        typer.echo(f"archive={archive.expanduser().resolve()}")
    return result


def _build_replaypack(spec_path: Path, destination: Path, archive: Path | None) -> None:
    try:
        spec = ReplayPackSpec.model_validate_json(spec_path.expanduser().read_text())
    except (OSError, ValueError) as error:
        raise typer.BadParameter(
            "must be a readable, valid ReplayPackSpec JSON file",
            param_hint="spec_path",
        ) from error
    studio = ReplayStudio()
    try:
        result = studio.build(spec, destination)
        archive_path = studio.archive(result.root, archive) if archive else None
    except ReplayStudioError as error:
        typer.echo(f"ReplayPack build failed: {error}", err=True)
        raise typer.Exit(code=1) from error
    typer.echo(
        f"replay_id={result.receipt.replay_id} trace_id={result.receipt.trace_id} "
        f"idempotent={str(result.idempotent).lower()} root={result.root}"
    )
    if archive_path:
        typer.echo(f"archive={archive_path}")


def _verify_replaypack(root: Path) -> None:
    try:
        receipt = ReplayStudio().verify(root)
    except ReplayStudioError as error:
        typer.echo(f"ReplayPack verification failed: {error}", err=True)
        raise typer.Exit(code=1) from error
    typer.echo(
        f"verified=true replay_id={receipt.replay_id} trace_id={receipt.trace_id} "
        f"pack_sha256={receipt.pack_sha256}"
    )


def _fetch_fdic_financials(
    *,
    cert: int,
    fields: str,
    database: Path,
    raw_store: Path,
    receipt_directory: Path,
    page_size: int,
) -> None:
    normalized_fields = tuple(part.strip() for part in fields.split(",") if part.strip())
    database = database.expanduser().resolve()
    database.parent.mkdir(parents=True, exist_ok=True)
    user_agent = "FinReplayOS/0.1 research connector (https://github.com/limingrui679-design)"
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
