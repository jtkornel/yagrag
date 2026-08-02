"""`kb index` and `kb search` commands.

`kb index build` (re)generates the chunk table, embeddings, and vector/FTS
indexes; `kb search` runs hybrid retrieval and prints the context bundle.
"""

from __future__ import annotations

import json as _json
from pathlib import Path

import typer
from rich.console import Console

from ..config import KBConfig
from ..graph.connection import KuzuNotInstalled
from ..index.embedder import EmbedderError
from ..index.indexer import IndexError_, build_index
from ..index.retrieval import search as run_search

index_app = typer.Typer(
    name="index",
    help="Build embeddings and full-text indexes over documents and entities.",
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


@index_app.command("build")
def cmd_build(kb: Path = _KB_OPT, json_output: bool = _JSON_OPT) -> None:
    """(Re)build the search index from stored documents and graph entities."""
    try:
        config = KBConfig.load(kb)
        stats = build_index(kb, config)
    except (FileNotFoundError, KuzuNotInstalled, EmbedderError, IndexError_) as exc:
        _fail(str(exc), json_output)
        return
    if json_output:
        typer.echo(_json.dumps(stats.to_dict(), indent=2))
    else:
        _console.print(
            f"[green]index built:[/green] {stats.chunks} chunks "
            f"({stats.documents} documents, {stats.entities} entities) "
            f"via {stats.backend}/{stats.model}"
        )


def search_command(
    query: str = typer.Argument(..., help="Search query."),
    limit: int = typer.Option(5, "--limit", help="Max hits per retrieval mode."),
    kb: Path = _KB_OPT,
    json_output: bool = _JSON_OPT,
) -> None:
    """Hybrid retrieval (semantic + full-text + graph) returning a context bundle."""
    try:
        config = KBConfig.load(kb)
        bundle = run_search(kb, query, limit=limit, config=config)
    except (FileNotFoundError, KuzuNotInstalled, EmbedderError) as exc:
        _fail(str(exc), json_output)
        return
    if json_output:
        typer.echo(_json.dumps(bundle, indent=2))
        return
    _console.print(f"[bold]query:[/bold] {bundle['query']}")
    for section in ("semantic", "fulltext"):
        _console.print(f"[bold]{section}:[/bold] {len(bundle[section])} hit(s)")
        for hit in bundle[section]:
            preview = hit["text"][:80].replace("\n", " ")
            _console.print(f"  [{hit['score']:.4f}] {hit['chunk_id']}: {preview}")
    if bundle["entities"]:
        _console.print("[bold]entities:[/bold]")
        for ent in bundle["entities"]:
            _console.print(f"  {ent['label']}:{ent['id']} — {ent.get('name', '')}")
    if bundle["documents"]:
        _console.print("[bold]documents:[/bold]")
        for doc in bundle["documents"]:
            _console.print(f"  {doc['id']} ({doc['kind']}): {doc['title']}")
