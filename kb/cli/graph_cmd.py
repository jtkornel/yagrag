"""`kb graph` command group: upsert-node | upsert-edge | upsert-claim | query | export.

All writes enforce provenance (`origin` + non-empty `sources`). Properties are
passed as a JSON object via `--props`. `query` executes read Cypher and returns
rows; `export` dumps all nodes and relationships as JSON.
"""

from __future__ import annotations

import json as _json
from pathlib import Path
from typing import Any, Optional

import typer
from rich.console import Console

from ..config import KBConfig
from ..graph.connection import GraphDB, KuzuNotInstalled
from ..graph.upsert import (
    GraphWriteError,
    ProvenanceError,
    upsert_claim,
    upsert_edge,
    upsert_node,
)
from ..schema.migrations import MIGRATIONS_TABLE

graph_app = typer.Typer(
    name="graph",
    help="Property-graph writes and reads (provenance enforced on writes).",
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


def _open_db(kb: Path, json_output: bool) -> GraphDB:
    try:
        config = KBConfig.load(kb)
        return GraphDB(kb / config.paths.graph_db)
    except (FileNotFoundError, KuzuNotInstalled) as exc:
        _fail(str(exc), json_output)
        raise AssertionError  # unreachable


def _parse_props(props: str, json_output: bool) -> dict[str, Any]:
    try:
        data = _json.loads(props)
    except _json.JSONDecodeError as exc:
        _fail(f"--props is not valid JSON: {exc}", json_output)
        raise AssertionError  # unreachable
    if not isinstance(data, dict):
        _fail("--props must be a JSON object", json_output)
    return data


def _jsonable(value: Any) -> Any:
    """Coerce Kuzu row values (timestamps, nested structs) into JSON-safe data."""
    if isinstance(value, dict):
        return {k: _jsonable(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_jsonable(v) for v in value]
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    return str(value)


@graph_app.command("upsert-node")
def cmd_upsert_node(
    label: str = typer.Argument(..., help="Node table name (e.g. Concept)."),
    props: str = typer.Option(
        ..., "--props", help='JSON object incl. id, origin, sources (e.g. \'{"id": "c1", ...}\').'
    ),
    kb: Path = _KB_OPT,
    json_output: bool = _JSON_OPT,
) -> None:
    """MERGE a node by id; rejects writes lacking provenance."""
    data = _parse_props(props, json_output)
    g = _open_db(kb, json_output)
    try:
        result = upsert_node(g, label, data)
    except (ProvenanceError, GraphWriteError) as exc:
        _fail(str(exc), json_output)
        return
    finally:
        g.close()
    if json_output:
        typer.echo(_json.dumps(result))
    else:
        _console.print(f"[green]upserted:[/green] {result['label']} {result['id']}")


@graph_app.command("upsert-edge")
def cmd_upsert_edge(
    rel: str = typer.Argument(..., help="Relation table name (e.g. MENTIONS)."),
    from_ref: str = typer.Option(..., "--from", help="From node as Label:id."),
    to_ref: str = typer.Option(..., "--to", help="To node as Label:id."),
    props: str = typer.Option(
        "{}", "--props", help="JSON object incl. origin, sources."
    ),
    kb: Path = _KB_OPT,
    json_output: bool = _JSON_OPT,
) -> None:
    """MERGE an edge between two existing nodes; rejects writes lacking provenance."""
    data = _parse_props(props, json_output)
    refs = []
    for ref, side in ((from_ref, "--from"), (to_ref, "--to")):
        if ":" not in ref:
            _fail(f"{side} must be Label:id (got {ref!r})", json_output)
        refs.append(ref.split(":", 1))
    g = _open_db(kb, json_output)
    try:
        result = upsert_edge(
            g, rel, refs[0][0], refs[0][1], refs[1][0], refs[1][1], data
        )
    except (ProvenanceError, GraphWriteError) as exc:
        _fail(str(exc), json_output)
        return
    finally:
        g.close()
    if json_output:
        typer.echo(_json.dumps(result))
    else:
        _console.print(
            f"[green]upserted:[/green] ({result['from']})-[:{result['rel']}]->({result['to']})"
        )


@graph_app.command("upsert-claim")
def cmd_upsert_claim(
    claim_id: str = typer.Argument(..., help="Claim id."),
    subject: str = typer.Option(..., "--subject", help="Subject as Label:id."),
    predicate: str = typer.Option(..., "--predicate", help="Claim predicate."),
    object_ref: Optional[str] = typer.Option(  # noqa: UP007
        None, "--object", help="Object entity as Label:id."
    ),
    object_literal: Optional[str] = typer.Option(  # noqa: UP007
        None, "--object-literal", help="Literal object value."
    ),
    props: str = typer.Option(
        ..., "--props", help="JSON object incl. origin, sources, confidence."
    ),
    kb: Path = _KB_OPT,
    json_output: bool = _JSON_OPT,
) -> None:
    """Create/update a reified Claim node plus ABOUT/HAS_OBJECT edges."""
    data = _parse_props(props, json_output)
    if ":" not in subject:
        _fail(f"--subject must be Label:id (got {subject!r})", json_output)
    subj_label, subj_id = subject.split(":", 1)
    obj_label = obj_id = None
    if object_ref is not None:
        if ":" not in object_ref:
            _fail(f"--object must be Label:id (got {object_ref!r})", json_output)
        obj_label, obj_id = object_ref.split(":", 1)
    g = _open_db(kb, json_output)
    try:
        result = upsert_claim(
            g,
            claim_id,
            subj_label,
            subj_id,
            predicate,
            data,
            object_label=obj_label,
            object_id=obj_id,
            object_literal=object_literal,
        )
    except (ProvenanceError, GraphWriteError) as exc:
        _fail(str(exc), json_output)
        return
    finally:
        g.close()
    if json_output:
        typer.echo(_json.dumps(result))
    else:
        _console.print(f"[green]claim upserted:[/green] {result['id']}")


@graph_app.command("query")
def cmd_query(
    cypher: str = typer.Argument(..., help="Cypher query to execute."),
    params: str = typer.Option("{}", "--params", help="JSON object of query parameters."),
    kb: Path = _KB_OPT,
    json_output: bool = _JSON_OPT,
) -> None:
    """Execute a Cypher query and print the result rows."""
    param_data = _parse_props(params, json_output)
    g = _open_db(kb, json_output)
    try:
        rows = g.execute(cypher, param_data)
    except RuntimeError as exc:
        _fail(str(exc), json_output)
        return
    finally:
        g.close()
    rows = [_jsonable(r) for r in rows]
    if json_output:
        typer.echo(_json.dumps({"rows": rows}, indent=2))
    else:
        for row in rows:
            _console.print(row)


@graph_app.command("export")
def cmd_export(
    kb: Path = _KB_OPT,
    json_output: bool = _JSON_OPT,
) -> None:
    """Dump all nodes and relationships as JSON (excluding internal tables)."""
    g = _open_db(kb, json_output)
    try:
        nodes: dict[str, list[dict[str, Any]]] = {}
        for table in g.node_table_names():
            if table == MIGRATIONS_TABLE:
                continue
            rows = g.execute(f"MATCH (n:{table}) RETURN n.*")
            nodes[table] = [_jsonable(r) for r in rows]
        rels: dict[str, list[dict[str, Any]]] = {}
        for table in g.rel_table_names():
            rows = g.execute(
                f"MATCH (a)-[r:{table}]->(b) "
                "RETURN a.id AS from_id, b.id AS to_id, r.*"
            )
            rels[table] = [_jsonable(r) for r in rows]
    except RuntimeError as exc:
        _fail(str(exc), json_output)
        return
    finally:
        g.close()
    payload = {"nodes": nodes, "relationships": rels}
    typer.echo(_json.dumps(payload, indent=2))
