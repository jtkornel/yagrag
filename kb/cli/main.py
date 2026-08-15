"""Typer CLI entry point for the `kb` executable.

Only `kb init` is wired up in Step 1. Later steps add schema, doc, graph, index,
and search command groups. Every command supports `--json` for reliable agent
consumption.
"""

from __future__ import annotations

import json as _json
from pathlib import Path

import typer
from rich.console import Console

from .. import __version__
from ..init import init_kb
from .code_cmd import code_app
from .doc_cmd import doc_app
from .graph_cmd import graph_app
from .index_cmd import index_app, search_command
from .schema_cmd import schema_app

app = typer.Typer(
    name="kb",
    help="Local-first GraphRAG knowledge base CLI (deterministic; LLM lives in the agent).",
    no_args_is_help=True,
    add_completion=False,
)
app.add_typer(code_app)
app.add_typer(schema_app)
app.add_typer(doc_app)
app.add_typer(graph_app)
app.add_typer(index_app)
app.command("search")(search_command)

_console = Console()
_err_console = Console(stderr=True)


def _version_callback(value: bool) -> None:
    if value:
        typer.echo(f"kb {__version__}")
        raise typer.Exit()


@app.callback()
def _root(
    version: bool | None = typer.Option(
        None,
        "--version",
        callback=_version_callback,
        is_eager=True,
        help="Show version and exit.",
    ),
) -> None:
    """Root callback; only used to attach global options like --version."""


@app.command("init")
def cmd_init(
    path: Path = typer.Argument(
        ...,
        help="Directory to scaffold as a knowledge base (created if missing).",
    ),
    name: str | None = typer.Option(
        None,
        "--name",
        help="Knowledge base display name (defaults to the target directory name).",
    ),
    json_output: bool = typer.Option(
        False,
        "--json",
        help="Emit a JSON summary instead of human-readable output.",
    ),
) -> None:
    """Scaffold a knowledge base directory (idempotent)."""
    try:
        result = init_kb(path, name=name)
    except (NotADirectoryError, IsADirectoryError) as exc:
        # Structural conflicts (e.g. a file where a directory is expected) are
        # user-actionable errors; surface them cleanly with a non-zero exit.
        if json_output:
            _err_console.print_json(_json.dumps({"error": str(exc)}))
        else:
            _err_console.print(f"[red]error:[/red] {exc}")
        raise typer.Exit(code=2) from exc

    if json_output:
        payload = {
            "kb_root": str(result.kb_root),
            "entries": result.entries,
            "created": result.created,
            "existed": result.existed,
        }
        typer.echo(_json.dumps(payload, indent=2))
        return

    _console.print(f"[bold]Knowledge base:[/bold] {result.kb_root}")
    if result.created:
        _console.print("[green]created:[/green]")
        for entry in result.created:
            _console.print(f"  + {entry}")
    if result.existed:
        _console.print("[dim]exists:[/dim]")
        for entry in result.existed:
            _console.print(f"  · {entry}")
    if not result.created and not result.existed:
        _console.print("[yellow]nothing to do[/yellow]")


if __name__ == "__main__":  # pragma: no cover - manual invocation
    app()
