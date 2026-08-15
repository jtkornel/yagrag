"""`kb schema` command group: show | validate | apply | migrate.

All commands operate on a knowledge base directory (default: cwd) and support
`--json` output for agent consumption.
"""

from __future__ import annotations

import json as _json
from pathlib import Path
from typing import Optional

import typer
from rich.console import Console

from ..config import KBConfig
from ..graph.connection import GrafeoNotInstalled, GraphDB
from ..schema.migrations import (
    MigrationError,
    applied_migration_ids,
    apply_migrations,
    build_target_schema,
    create_migration_file,
    load_migrations,
    validate as validate_schema,
)

schema_app = typer.Typer(
    name="schema",
    help="Manage the versioned graph schema (migrations in schema/migrations/).",
    no_args_is_help=True,
)

_console = Console()
_err_console = Console(stderr=True)


def _fail(message: str, json_output: bool, code: int = 2) -> None:
    if json_output:
        typer.echo(_json.dumps({"error": message}))
    else:
        _err_console.print(f"[red]error:[/red] {message}")
    raise typer.Exit(code=code)


def _load_config(kb_root: Path, json_output: bool) -> KBConfig:
    try:
        return KBConfig.load(kb_root)
    except FileNotFoundError as exc:
        _fail(str(exc), json_output)
        raise AssertionError  # unreachable


def _migrations_dir(kb_root: Path, config: KBConfig) -> Path:
    return kb_root / config.paths.schema_dir / "migrations"


def _open_db(kb_root: Path, config: KBConfig, json_output: bool) -> GraphDB:
    try:
        return GraphDB(kb_root / config.paths.graph_db)
    except GrafeoNotInstalled as exc:
        _fail(str(exc), json_output)
        raise AssertionError  # unreachable


_KB_OPT = typer.Option(
    Path("."),
    "--kb",
    help="Knowledge base directory (containing kb.toml).",
)
_JSON_OPT = typer.Option(False, "--json", help="Emit JSON output.")


@schema_app.command("show")
def cmd_show(
    type_name: Optional[str] = typer.Option(  # noqa: UP007
        None, "--type", "-t", help="Filter by node or relation type name (case-insensitive)."
    ),
    kb: Path = _KB_OPT,
    json_output: bool = _JSON_OPT,
    gql_output: bool = typer.Option(
        False, "--gql", help="Emit standard ISO GQL (ISO/IEC 39075) schema definition."
    ),
) -> None:
    """Show the target schema declared by all migrations on disk."""
    config = _load_config(kb, json_output)
    try:
        schema = build_target_schema(_migrations_dir(kb, config))
    except MigrationError as exc:
        _fail(str(exc), json_output)
        return

    node_types = schema.node_types
    relation_types = schema.relation_types

    if gql_output:
        from ..schema.model import render_gql_graph_type

        typer.echo(render_gql_graph_type(schema))
        return

    if type_name:
        target = type_name.strip().lower()
        node_types = [nt for nt in node_types if nt.name.lower() == target]
        relation_types = [rt for rt in relation_types if rt.name.lower() == target]
        if not node_types and not relation_types:
            _fail(f"unknown node or relation type {type_name!r}", json_output)
            return

    if json_output:
        if type_name:
            out = {
                "node_types": [nt.model_dump(by_alias=True) for nt in node_types],
                "relation_types": [rt.model_dump(by_alias=True) for rt in relation_types],
            }
            typer.echo(_json.dumps(out, indent=2))
        else:
            typer.echo(schema.model_dump_json(indent=2, by_alias=True))
        return

    if node_types:
        _console.print(f"[bold]Node types[/bold] ({len(node_types)}):")
        for nt in node_types:
            props = ", ".join(p.name for p in nt.effective_properties())
            _console.print(f"  {nt.name}: {props}")

    if relation_types:
        _console.print(f"[bold]Relation types[/bold] ({len(relation_types)}):")
        for rt in relation_types:
            pairs = ", ".join(f"{p.from_}->{p.to}" for p in rt.pairs)
            _console.print(f"  {rt.name}: {pairs}")


@schema_app.command("validate")
def cmd_validate(kb: Path = _KB_OPT, json_output: bool = _JSON_OPT) -> None:
    """Compare the DB against the schema declared by migrations."""
    config = _load_config(kb, json_output)
    g = _open_db(kb, config, json_output)
    try:
        report = validate_schema(g, _migrations_dir(kb, config))
    except MigrationError as exc:
        _fail(str(exc), json_output)
        return
    finally:
        g.close()
    if json_output:
        typer.echo(_json.dumps(report.to_dict(), indent=2))
    else:
        status = "[green]ok[/green]" if report.ok else "[red]invalid[/red]"
        _console.print(f"schema validation: {status}")
        for label, items in (
            ("missing node tables", report.missing_node_tables),
            ("missing rel tables", report.missing_rel_tables),
            ("pending migrations", report.pending_migrations),
            ("extra node tables", report.extra_node_tables),
            ("extra rel tables", report.extra_rel_tables),
        ):
            if items:
                _console.print(f"  {label}: {', '.join(items)}")
    if not report.ok:
        raise typer.Exit(code=1)


@schema_app.command("apply")
def cmd_apply(kb: Path = _KB_OPT, json_output: bool = _JSON_OPT) -> None:
    """Apply all pending migrations to the graph database (idempotent)."""
    config = _load_config(kb, json_output)
    g = _open_db(kb, config, json_output)
    try:
        newly = apply_migrations(g, _migrations_dir(kb, config))
        applied = applied_migration_ids(g)
    except MigrationError as exc:
        _fail(str(exc), json_output)
        return
    finally:
        g.close()
    if json_output:
        typer.echo(_json.dumps({"newly_applied": newly, "applied": applied}, indent=2))
    else:
        if newly:
            _console.print("[green]applied:[/green]")
            for mid in newly:
                _console.print(f"  + {mid}")
        else:
            _console.print("[dim]nothing to apply — schema is up to date[/dim]")


@schema_app.command("migrate")
def cmd_migrate(
    name: str = typer.Argument(..., help="Migration name (e.g. add_noise_model)."),
    kb: Path = _KB_OPT,
    json_output: bool = _JSON_OPT,
) -> None:
    """Scaffold a new empty JSON migration file in schema/migrations/."""
    config = _load_config(kb, json_output)
    try:
        path = create_migration_file(_migrations_dir(kb, config), name)
    except MigrationError as exc:
        _fail(str(exc), json_output)
        return
    if json_output:
        typer.echo(_json.dumps({"created": str(path), "id": path.stem}, indent=2))
    else:
        _console.print(f"[green]created:[/green] {path}")


@schema_app.command("status")
def cmd_status(kb: Path = _KB_OPT, json_output: bool = _JSON_OPT) -> None:
    """List migrations on disk and whether each has been applied."""
    config = _load_config(kb, json_output)
    g = _open_db(kb, config, json_output)
    try:
        applied = set(applied_migration_ids(g))
        on_disk = load_migrations(_migrations_dir(kb, config))
    except MigrationError as exc:
        _fail(str(exc), json_output)
        return
    finally:
        g.close()
    rows = [{"id": mf.id, "applied": mf.id in applied} for mf in on_disk]
    if json_output:
        typer.echo(_json.dumps({"migrations": rows}, indent=2))
    else:
        if not rows:
            _console.print("[dim]no migrations on disk[/dim]")
        for row in rows:
            mark = "[green]✓[/green]" if row["applied"] else "[yellow]·[/yellow]"
            _console.print(f"  {mark} {row['id']}")
