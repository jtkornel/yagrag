"""`kb doc` command group: add | list | show | remove | text.

Documents are ingested into the KB's document store with strict raw vs
synthesized separation and mandatory provenance for synthesized documents.
"""

from __future__ import annotations

import json as _json
from pathlib import Path
from typing import List, Optional

import typer
from rich.console import Console
from rich.table import Table

from ..config import KBConfig
from ..store.documents import DocumentStore, StoreError

doc_app = typer.Typer(
    name="doc",
    help="Manage documents (raw ingested sources vs synthesized outputs).",
    no_args_is_help=True,
)

_console = Console()
_err_console = Console(stderr=True)

_KB_OPT = typer.Option(
    Path("."),
    "--kb",
    help="Knowledge base directory (containing kb.toml).",
)
_JSON_OPT = typer.Option(False, "--json", help="Emit JSON output.")


def _fail(message: str, json_output: bool, code: int = 2) -> None:
    if json_output:
        typer.echo(_json.dumps({"error": message}))
    else:
        _err_console.print(f"[red]error:[/red] {message}")
    raise typer.Exit(code=code)


def _open_store(kb: Path, json_output: bool) -> DocumentStore:
    try:
        return DocumentStore(kb, KBConfig.load(kb))
    except (FileNotFoundError, StoreError) as exc:
        _fail(str(exc), json_output)
        raise AssertionError  # unreachable


@doc_app.command("add")
def cmd_add(
    file: Path = typer.Argument(..., help="File to ingest (md/txt/pdf)."),
    kind: str = typer.Option(..., "--kind", help="raw | synthesized"),
    title: Optional[str] = typer.Option(None, "--title"),  # noqa: UP007
    source: List[str] = typer.Option(  # noqa: UP006
        [], "--source", help="Source document id (repeatable; synthesized only)."
    ),
    tag: List[str] = typer.Option([], "--tag", help="Tag (repeatable)."),  # noqa: UP006
    notes: str = typer.Option("", "--notes"),
    kb: Path = _KB_OPT,
    json_output: bool = _JSON_OPT,
) -> None:
    """Ingest a document into the store (immutable copy for raw)."""
    if kind not in ("raw", "synthesized"):
        _fail(f"invalid --kind {kind!r}; expected raw or synthesized", json_output)
    store = _open_store(kb, json_output)
    try:
        rec = store.add(
            file,
            kind,  # type: ignore[arg-type]
            title=title,
            sources=list(source),
            tags=list(tag),
            notes=notes,
        )
    except StoreError as exc:
        _fail(str(exc), json_output)
        return
    if json_output:
        typer.echo(_json.dumps(rec.to_dict(), indent=2))
    else:
        _console.print(f"[green]added:[/green] {rec.id} → {rec.path}")


@doc_app.command("list")
def cmd_list(
    kind: Optional[str] = typer.Option(None, "--kind", help="Filter: raw | synthesized."),  # noqa: UP007
    kb: Path = _KB_OPT,
    json_output: bool = _JSON_OPT,
) -> None:
    """List documents in the manifest."""
    store = _open_store(kb, json_output)
    try:
        records = store.records()
    except StoreError as exc:
        _fail(str(exc), json_output)
        return
    if kind is not None:
        records = [r for r in records if r.kind == kind]
    if json_output:
        typer.echo(_json.dumps([r.to_dict() for r in records], indent=2))
        return
    table = Table("id", "kind", "format", "title", "path")
    for r in records:
        table.add_row(r.id, r.kind, r.format, r.title, r.path)
    _console.print(table)


@doc_app.command("show")
def cmd_show(
    doc_id: str = typer.Argument(..., help="Document id."),
    kb: Path = _KB_OPT,
    json_output: bool = _JSON_OPT,
) -> None:
    """Show a document's metadata."""
    store = _open_store(kb, json_output)
    try:
        rec = store.get(doc_id)
    except StoreError as exc:
        _fail(str(exc), json_output)
        return
    if json_output:
        typer.echo(_json.dumps(rec.to_dict(), indent=2))
    else:
        for key, value in rec.to_dict().items():
            _console.print(f"[bold]{key}[/bold]: {value}")


@doc_app.command("text")
def cmd_text(
    doc_id: str = typer.Argument(..., help="Document id."),
    kb: Path = _KB_OPT,
    json_output: bool = _JSON_OPT,
) -> None:
    """Print the extracted plain-text content of a document."""
    store = _open_store(kb, json_output)
    try:
        text = store.extract_text(doc_id)
    except StoreError as exc:
        _fail(str(exc), json_output)
        return
    if json_output:
        typer.echo(_json.dumps({"id": doc_id, "text": text}))
    else:
        typer.echo(text)


@doc_app.command("remove")
def cmd_remove(
    doc_id: str = typer.Argument(..., help="Document id."),
    kb: Path = _KB_OPT,
    json_output: bool = _JSON_OPT,
) -> None:
    """Remove a document (blocked if other documents derive from it)."""
    store = _open_store(kb, json_output)
    try:
        rec = store.remove(doc_id)
    except StoreError as exc:
        _fail(str(exc), json_output)
        return
    if json_output:
        typer.echo(_json.dumps({"removed": rec.id}))
    else:
        _console.print(f"[green]removed:[/green] {rec.id}")
