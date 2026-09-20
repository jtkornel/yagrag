"""`kb graph` command group: upsert-node | upsert-edge | upsert-claim | query | export.

All writes enforce provenance (`origin` + non-empty `sources`). Properties are
passed as a JSON object via `--props`. `query` executes read Cypher and returns
rows; `export` dumps all nodes and relationships as JSON.
"""

from __future__ import annotations

import contextlib
import json as _json
import re
from pathlib import Path
from typing import Any

import typer
from rich.console import Console
from rich.table import Table

from ..config import KBConfig
from ..graph.connection import GraphDB, TraverseNotInstalled
from ..graph.upsert import (
    GraphWriteError,
    ProvenanceError,
    execute_batch,
    upsert_claim,
    upsert_edge,
    upsert_node,
)
from ..schema.companion import load_companion
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
    except (FileNotFoundError, TraverseNotInstalled) as exc:
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
    """Coerce database row values (timestamps, nested structs) into JSON-safe data."""
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
    object_ref: str | None = typer.Option(
        None, "--object", help="Object entity as Label:id."
    ),
    object_literal: str | None = typer.Option(
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
        if not rows:
            _console.print("[dim]0 rows returned[/dim]")
            return
        if all(isinstance(r, dict) for r in rows):
            table = Table(show_header=True, header_style="bold cyan")
            cols = list(rows[0].keys())
            for col in cols:
                table.add_column(str(col))
            for r in rows:
                table.add_row(*[str(r.get(c, "")) for c in cols])
            _console.print(table)
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
            if table == MIGRATIONS_TABLE or table.startswith("_"):
                continue
            rows = g.execute(f"MATCH (n:{table}) RETURN n")
            cleaned_rows: list[dict[str, Any]] = []
            for r in rows:
                n_val = r.get("n")
                props: dict[str, Any] = n_val if isinstance(n_val, dict) else r
                cleaned_rows.append({
                    (k.split(".", 1)[-1] if "." in k else k): v
                    for k, v in props.items()
                    if not k.startswith("_")
                })
            nodes[table] = [_jsonable(r) for r in cleaned_rows]
        rels: dict[str, list[dict[str, Any]]] = {}
        for table in g.rel_table_names():
            rows = g.execute(
                f"MATCH (a)-[r:{table}]->(b) "
                "RETURN a.id AS from_id, b.id AS to_id, r"
            )
            cleaned_rels: list[dict[str, Any]] = []
            for r in rows:
                from_id = r.get("from_id")
                to_id = r.get("to_id")
                r_val = r.get("r")
                rel_props: dict[str, Any] = r_val if isinstance(r_val, dict) else r
                rel_dict = {"from_id": from_id, "to_id": to_id}
                for k, v in rel_props.items():
                    k_clean = k.split(".", 1)[-1] if "." in k else k
                    if not k_clean.startswith("_") and k_clean not in ("from_id", "to_id"):
                        rel_dict[k_clean] = v
                cleaned_rels.append(rel_dict)
            rels[table] = [_jsonable(r) for r in cleaned_rels]
    except Exception as exc:  # noqa: BLE001
        _fail(str(exc), json_output)
        return
    finally:
        g.close()
    payload = {"nodes": nodes, "relationships": rels}
    typer.echo(_json.dumps(payload, indent=2))


@graph_app.command("dump")
def cmd_dump(
    output_file: Path = typer.Option(
        Path("graph_dump.json.gz"),
        "--output",
        "-o",
        help="Target dump path (.json or .json.gz).",
    ),
    kb: Path = _KB_OPT,
    json_output: bool = _JSON_OPT,
) -> None:
    """Export property graph, claims, and migration metadata to a portable compressed dump file."""
    import gzip

    from ..schema.migrations import applied_migration_ids

    try:
        cfg = KBConfig.load(kb)
    except Exception as exc:  # noqa: BLE001
        _fail(str(exc), json_output)
        return
    g = _open_db(kb, json_output)
    try:
        # Collect nodes
        nodes: dict[str, list[dict[str, Any]]] = {}
        for table in g.node_table_names():
            if table.startswith("_"):
                continue
            rows = g.execute(f"MATCH (n:{table}) RETURN n")
            cleaned_rows: list[dict[str, Any]] = []
            for r in rows:
                n_val = r.get("n")
                props: dict[str, Any] = n_val if isinstance(n_val, dict) else r
                cleaned_rows.append({
                    (k.split(".", 1)[-1] if "." in k else k): v
                    for k, v in props.items()
                    if not k.startswith("_")
                })
            nodes[table] = [_jsonable(r) for r in cleaned_rows]

        # Collect relationships
        rels: dict[str, list[dict[str, Any]]] = {}
        for table in g.rel_table_names():
            if table.startswith("_"):
                continue
            rows = g.execute(
                f"MATCH (a)-[r:{table}]->(b) "
                "RETURN labels(a)[0] AS from_label, a.id AS from_id, labels(b)[0] AS to_label, b.id AS to_id, r"
            )
            cleaned_rels: list[dict[str, Any]] = []
            for r in rows:
                from_label = r.get("from_label")
                from_id = r.get("from_id")
                to_label = r.get("to_label")
                to_id = r.get("to_id")
                r_val = r.get("r")
                rel_props: dict[str, Any] = r_val if isinstance(r_val, dict) else r
                rel_dict = {
                    "from_label": from_label,
                    "from_id": from_id,
                    "to_label": to_label,
                    "to_id": to_id,
                }
                for k, v in rel_props.items():
                    k_clean = k.split(".", 1)[-1] if "." in k else k
                    if not k_clean.startswith("_") and k_clean not in ("from_id", "to_id", "from_label", "to_label"):
                        rel_dict[k_clean] = v
                cleaned_rels.append(rel_dict)
            rels[table] = [_jsonable(r) for r in cleaned_rels]

        # Collect applied migrations
        applied_migrations = applied_migration_ids(g)
    except Exception as exc:  # noqa: BLE001
        _fail(str(exc), json_output)
        return
    finally:
        g.close()

    total_nodes = sum(len(v) for v in nodes.values())
    total_rels = sum(len(v) for v in rels.values())

    payload = {
        "version": 1,
        "format_version": cfg.format_version,
        "min_software_version": cfg.min_software_version,
        "applied_migrations": applied_migrations,
        "nodes": nodes,
        "relationships": rels,
    }

    serialized = _json.dumps(payload, indent=2).encode("utf-8")
    out_path = Path(output_file)
    if out_path.suffix == ".gz":
        with gzip.open(out_path, "wb") as f:
            f.write(serialized)
    else:
        out_path.write_bytes(serialized)

    result = {
        "dump_file": str(out_path),
        "nodes_count": total_nodes,
        "relationships_count": total_rels,
        "migrations_count": len(applied_migrations),
    }

    if json_output:
        typer.echo(_json.dumps(result))
    else:
        _console.print(
            f"[green]graph dump created:[/green] {out_path} "
            f"({total_nodes} nodes, {total_rels} relationships, {len(applied_migrations)} migrations)"
        )


@graph_app.command("restore")
def cmd_restore(
    input_file: Path = typer.Option(
        Path("graph_dump.json.gz"),
        "--input",
        "-i",
        help="Source dump file (.json or .json.gz).",
    ),
    kb: Path = _KB_OPT,
    apply_schema: bool = typer.Option(
        True,
        "--apply-schema/--no-apply-schema",
        help="Apply pending migrations before restoring nodes and relationships.",
    ),
    json_output: bool = _JSON_OPT,
) -> None:
    """Restore property graph, claims, and schema from a portable dump file."""
    import gzip

    from ..schema.migrations import MIGRATIONS_TABLE, apply_migrations

    src_path = Path(input_file)
    if not src_path.is_file():
        _fail(f"dump file not found: {src_path}", json_output)
        return

    try:
        if src_path.suffix == ".gz":
            with gzip.open(src_path, "rb") as f:
                data = _json.loads(f.read().decode("utf-8"))
        else:
            data = _json.loads(src_path.read_text(encoding="utf-8"))
    except Exception as exc:  # noqa: BLE001
        _fail(f"failed to read dump file: {exc}", json_output)
        return

    try:
        cfg = KBConfig.load(kb)
    except Exception as exc:  # noqa: BLE001
        _fail(str(exc), json_output)
        return

    # Check dump version compatibility if present
    dump_min_sw = data.get("min_software_version")
    if dump_min_sw:
        from .. import __version__ as current_sw_version
        from ..config import _parse_semver
        if _parse_semver(dump_min_sw) > _parse_semver(current_sw_version):
            _fail(
                f"Dump requires yagrag/kb >= {dump_min_sw} (found {current_sw_version}). "
                "Please update your yagrag installation.",
                json_output,
            )
            return

    # Apply schema migrations if requested
    migrations_dir = kb / cfg.paths.schema_dir / "migrations"
    g = _open_db(kb, json_output)
    try:
        if apply_schema and migrations_dir.is_dir():
            apply_migrations(g, migrations_dir)

        # Restore migration records if any are in the dump
        applied_in_dump = data.get("applied_migrations") or []
        for mid in applied_in_dump:
            with contextlib.suppress(Exception):
                g.execute(
                    f"CREATE (:{MIGRATIONS_TABLE} {{id: $id, applied_at: current_timestamp()}})",
                    {"id": mid},
                )

        nodes_dict: dict[str, list[dict[str, Any]]] = data.get("nodes") or {}
        rels_dict: dict[str, list[dict[str, Any]]] = data.get("relationships") or {}

        # 1. Restore nodes
        total_nodes = 0
        from ..graph.upsert import upsert_node
        for label, items in nodes_dict.items():
            for item in items:
                props = dict(item)
                upsert_node(g, label, props)
                total_nodes += 1

        # 2. Restore relationships
        total_rels = 0
        from ..graph.upsert import upsert_edge
        for rel_type, items in rels_dict.items():
            for item in items:
                props = dict(item)
                from_id = props.pop("from_id", None)
                to_id = props.pop("to_id", None)
                from_label = props.pop("from_label", None)
                to_label = props.pop("to_label", None)

                # Fallback label lookup if not present in legacy dumps
                if not from_label or not to_label:
                    for l, node_list in nodes_dict.items():
                        for n in node_list:
                            if n.get("id") == from_id and not from_label:
                                from_label = l
                            if n.get("id") == to_id and not to_label:
                                to_label = l

                if from_id and to_id and from_label and to_label:
                    upsert_edge(g, rel_type, str(from_label), str(from_id), str(to_label), str(to_id), props)
                    total_rels += 1
    except Exception as exc:  # noqa: BLE001
        _fail(f"restore failed: {exc}", json_output)
        return
    finally:
        g.close()

    result = {
        "restored_from": str(src_path),
        "nodes_restored": total_nodes,
        "relationships_restored": total_rels,
    }

    if json_output:
        typer.echo(_json.dumps(result))
    else:
        _console.print(
            f"[green]graph restore complete:[/green] restored from {src_path} "
            f"({total_nodes} nodes, {total_rels} relationships)"
        )


@graph_app.command("batch")
def cmd_batch(
    batch_file: Path | None = typer.Option(
        None,
        "--file",
        "-f",
        help="JSON file containing array of graph operations (omit or use '-' for stdin).",
    ),
    kb: Path = _KB_OPT,
    json_output: bool = _JSON_OPT,
) -> None:
    """Execute a batch of graph operations (nodes, edges, claims) from JSON."""
    import sys

    raw_json: str
    if batch_file is None or str(batch_file) == "-":
        raw_json = sys.stdin.read()
    else:
        if not batch_file.is_file():
            _fail(f"batch file not found: {batch_file}", json_output)
            return
        raw_json = batch_file.read_text(encoding="utf-8")

    try:
        data = _json.loads(raw_json)
    except _json.JSONDecodeError as exc:
        _fail(f"batch JSON decode error: {exc}", json_output)
        return

    if not isinstance(data, list):
        _fail("batch input must be a JSON array of operation objects", json_output)
        return

    g = _open_db(kb, json_output)
    try:
        result = execute_batch(g, data)
    except (ProvenanceError, GraphWriteError) as exc:
        _fail(str(exc), json_output)
        return
    finally:
        g.close()

    if json_output:
        typer.echo(_json.dumps(result))
    else:
        _console.print(
            f"[green]batch complete:[/green] {result['nodes_upserted']} nodes, "
            f"{result['edges_upserted']} edges, {result['claims_upserted']} claims "
            f"upserted ({result['total_operations']} total operations)"
        )


@graph_app.command("lint")
def cmd_lint(
    kb: Path = _KB_OPT,
    json_output: bool = _JSON_OPT,
) -> None:
    """Audit graph quality: check for floating nodes, Claim schema issues, and garbled symbols."""
    g = _open_db(kb, json_output)
    issues: list[dict[str, Any]] = []
    try:
        node_tables = [t for t in g.node_table_names() if not t.startswith("_")]
        rel_tables = g.rel_table_names()

        # 1. Floating node check
        for nt in node_tables:
            rows = g.execute(f"MATCH (n:{nt}) RETURN n.id AS id, n.name AS name")
            for r in rows:
                nid = r["id"]
                connected = False
                for rt in rel_tables:
                    res1 = g.execute(
                        f"MATCH (n:{nt} {{id: $id}})-[r:{rt}]->() RETURN count(r) AS c",
                        {"id": nid},
                    )
                    res2 = g.execute(
                        f"MATCH ()-[r:{rt}]->(n:{nt} {{id: $id}}) RETURN count(r) AS c",
                        {"id": nid},
                    )
                    if res1[0]["c"] > 0 or res2[0]["c"] > 0:
                        connected = True
                        break
                if not connected:
                    issues.append(
                        {
                            "category": "floating_node",
                            "severity": "warning",
                            "id": nid,
                            "node_type": nt,
                            "message": f"Node {nt}:{nid} is floating with 0 relationship edges.",
                        }
                    )

        # 2. Claim node checks
        if "Claim" in node_tables:
            claims = g.execute(
                "MATCH (c:Claim) RETURN c.id AS id, c.name AS name, c.summary AS summary, "
                "c.predicate AS predicate, c.sources AS sources, c.qualifiers AS qualifiers"
            )
            for c in claims:
                cid = c["id"]
                name = c["name"] or ""
                summary = c["summary"] or ""
                predicate = c["predicate"] or ""
                sources = c["sources"] or []
                qualifiers_raw = c.get("qualifiers")

                if not summary:
                    issues.append(
                        {
                            "category": "claim_quality",
                            "severity": "error",
                            "id": cid,
                            "node_type": "Claim",
                            "message": f"Claim {cid} is missing 'summary' assertion sentence.",
                        }
                    )
                if not name:
                    issues.append(
                        {
                            "category": "claim_quality",
                            "severity": "warning",
                            "id": cid,
                            "node_type": "Claim",
                            "message": f"Claim {cid} is missing 'name' short label.",
                        }
                    )
                elif len(name) > 80:
                    issues.append(
                        {
                            "category": "claim_quality",
                            "severity": "warning",
                            "id": cid,
                            "node_type": "Claim",
                            "message": f"Claim {cid} 'name' is excessively long ({len(name)} chars); should be concise title.",
                        }
                    )
                if not predicate:
                    issues.append(
                        {
                            "category": "claim_quality",
                            "severity": "error",
                            "id": cid,
                            "node_type": "Claim",
                            "message": f"Claim {cid} is missing 'predicate'.",
                        }
                    )
                if not sources:
                    issues.append(
                        {
                            "category": "provenance",
                            "severity": "error",
                            "id": cid,
                            "node_type": "Claim",
                            "message": f"Claim {cid} is missing provenance 'sources'.",
                        }
                    )

                # Validate qualifiers (citation-augmenting claims)
                if qualifiers_raw:
                    try:
                        q_data = _json.loads(qualifiers_raw) if isinstance(qualifiers_raw, str) else qualifiers_raw
                        if isinstance(q_data, dict):
                            valid_ref_types = {
                                "ADOPTS_FORMULATION",
                                "EXTENDS_METHOD",
                                "REVISES_ASSUMPTION",
                                "EVALUATES_PROPERTY",
                                "BENCHMARKS_AGAINST",
                                "BACKGROUND_CONTEXT",
                            }
                            valid_attitudes = {"Positive", "Negative", "Neutral"}

                            ref_type = q_data.get("reference_type")
                            if ref_type is not None and ref_type not in valid_ref_types:
                                issues.append(
                                    {
                                        "category": "claim_qualifier",
                                        "severity": "error",
                                        "id": cid,
                                        "node_type": "Claim",
                                        "message": (
                                            f"Claim {cid} qualifiers has invalid 'reference_type': {ref_type!r}. "
                                            f"Must be one of: {sorted(valid_ref_types)}"
                                        ),
                                    }
                                )

                            attitude = q_data.get("attitude")
                            if attitude is not None and attitude not in valid_attitudes:
                                issues.append(
                                    {
                                        "category": "claim_qualifier",
                                        "severity": "error",
                                        "id": cid,
                                        "node_type": "Claim",
                                        "message": (
                                            f"Claim {cid} qualifiers has invalid 'attitude': {attitude!r}. "
                                            f"Must be one of: {sorted(valid_attitudes)}"
                                        ),
                                    }
                                )
                        else:
                            issues.append(
                                {
                                    "category": "claim_qualifier",
                                    "severity": "error",
                                    "id": cid,
                                    "node_type": "Claim",
                                    "message": f"Claim {cid} 'qualifiers' JSON is not an object.",
                                }
                            )
                    except Exception as e:
                        issues.append(
                            {
                                "category": "claim_qualifier",
                                "severity": "error",
                                "id": cid,
                                "node_type": "Claim",
                                "message": f"Claim {cid} 'qualifiers' failed JSON parsing: {e}",
                            }
                        )

        # 3. Dynamic capability & schema property checks (symbol, code_status, provenance)
        for nt in node_tables:
            rows = g.execute(f"MATCH (n:{nt}) RETURN n")
            for r in rows:
                n_val = r.get("n")
                props: dict[str, Any] = n_val if isinstance(n_val, dict) else r
                nid = props.get("id")
                sources = props.get("sources")

                # Provenance check (all domain nodes)
                if sources is not None and (not sources or len(sources) == 0):
                    issues.append(
                        {
                            "category": "provenance",
                            "severity": "error",
                            "id": nid,
                            "node_type": nt,
                            "message": f"Node {nt}:{nid} is missing required provenance 'sources'.",
                        }
                    )

                # Symbol check: if table carries a 'symbol' property
                if "symbol" in props:
                    sym = props.get("symbol") or ""
                    if sym and len(sym) > 30:
                        issues.append(
                            {
                                "category": "symbol_quality",
                                "severity": "warning",
                                "id": nid,
                                "node_type": nt,
                                "message": f"Node {nt}:{nid} has an unusually long or concatenated symbol string: {sym!r}",
                            }
                        )

                # Code status check: if table carries 'code_status' property
                if "code_status" in props:
                    status = props.get("code_status")
                    if status and status == "failed":
                        issues.append(
                            {
                                "category": "code_check",
                                "severity": "warning",
                                "id": nid,
                                "node_type": nt,
                                "message": f"Node {nt}:{nid} failed static code/symbol checking.",
                            }
                        )

        # 4. Symbolic-Equation Audit (check if connected symbols appear in LaTeX formula)
        if "Equation" in node_tables:
            eq_rows = g.execute("MATCH (e:Equation) RETURN e.id AS id, e.latex AS latex")
            for eq in eq_rows:
                eqid = eq["id"]
                latex = eq["latex"] or ""
                if not latex:
                    continue

                from ..code.checker import symbol_matches_candidate

                def _symbol_in_latex(symbol_str: str, latex_formula: str) -> bool:
                    if not symbol_str:
                        return False
                    clean_s = symbol_str.replace("\\", "").strip()
                    clean_f = latex_formula.replace("\\", "").strip()
                    if clean_s in clean_f or symbol_matches_candidate(symbol_str, latex_formula):
                        return True
                    latex_no_braces = latex_formula.replace("{", "").replace("}", "")
                    if clean_s in latex_no_braces.replace("\\", "") or symbol_matches_candidate(symbol_str, latex_no_braces):
                        return True
                    # Check tokenized subwords
                    tokens = [t for t in re.split(r"[^a-zA-Z0-9_]+", latex_no_braces) if t]
                    return any(symbol_matches_candidate(symbol_str, t) for t in tokens)

                if "USES_SYMBOL" in rel_tables:
                    symbols = g.execute(
                        "MATCH (e:Equation {id: $id})-[:USES_SYMBOL]->(q) RETURN q.id AS qid, q.symbol AS symbol",
                        {"id": eqid},
                    )
                    for s in symbols:
                        sym = s["symbol"] or ""
                        if sym and not _symbol_in_latex(sym, latex):
                            issues.append(
                                {
                                    "category": "symbolic_consistency",
                                    "severity": "warning",
                                    "id": eqid,
                                    "node_type": "Equation",
                                    "message": f"Equation {eqid} references symbol {s['qid']} ({sym!r}) which does not appear in LaTeX formula {latex!r}.",
                                }
                            )

                for rel_name in ("EXPRESSED_BY", "DEFINED_BY"):
                    if rel_name in rel_tables:
                        lhs_nodes = g.execute(
                            f"MATCH (q)-[:{rel_name}]->(e:Equation {{id: $id}}) RETURN q.id AS qid, q.symbol AS symbol",
                            {"id": eqid},
                        )
                        for s in lhs_nodes:
                            sym = s["symbol"] or ""
                            if sym and not _symbol_in_latex(sym, latex):
                                issues.append(
                                    {
                                        "category": "symbolic_consistency",
                                        "severity": "warning",
                                        "id": eqid,
                                        "node_type": "Equation",
                                        "message": f"Equation {eqid} linked via {rel_name} to quantity {s['qid']} ({sym!r}) but symbol does not appear in LaTeX formula {latex!r}.",
                                    }
                                )

        # 4b. Two-Way SymPy vs Graph Audit (LHS/RHS role matching & completeness)
        if "Equation" in node_tables:
            from ..code.checker import extract_sympy_equation_roles, symbol_matches_candidate

            eq_code_rows = g.execute(
                "MATCH (e:Equation) WHERE e.code_path IS NOT NULL AND e.code_path <> '' "
                "RETURN e.id AS id, e.code_path AS code_path, e.code_language AS code_language"
            )
            for eq in eq_code_rows:
                eqid = eq["id"]
                code_path_str = eq.get("code_path")
                if not code_path_str:
                    continue
                file_path = kb / code_path_str
                if not file_path.is_file() or file_path.suffix.lower() != ".sympy":
                    continue

                try:
                    source = file_path.read_text(encoding="utf-8")
                except OSError:
                    continue

                lhs_syms, rhs_syms, parse_errs = extract_sympy_equation_roles(source)
                if parse_errs:
                    continue

                # Query existing graph edges connected to this equation (specifically for Quantity/Variable)
                expressed_rows = g.execute(
                    "MATCH (q:Quantity)-[:EXPRESSED_BY]->(e:Equation {id: $id}) "
                    "RETURN q.id AS qid, q.name AS name, q.symbol AS symbol",
                    {"id": eqid},
                ) if "EXPRESSED_BY" in rel_tables else []

                defined_rows = g.execute(
                    "MATCH (q:Quantity)-[:DEFINED_BY]->(e:Equation {id: $id}) "
                    "RETURN q.id AS qid, q.name AS name, q.symbol AS symbol",
                    {"id": eqid},
                ) if "DEFINED_BY" in rel_tables else []

                used_rows = g.execute(
                    "MATCH (e:Equation {id: $id})-[:USES_SYMBOL]->(q:Quantity) "
                    "RETURN q.id AS qid, q.name AS name, q.symbol AS symbol",
                    {"id": eqid},
                ) if "USES_SYMBOL" in rel_tables else []

                def _matches_any(target_sym: str, nodes: list[dict[str, Any]]) -> dict[str, Any] | None:
                    for n in nodes:
                        cands = [str(n.get("symbol") or ""), str(n.get("id") or ""), str(n.get("name") or "")]
                        if any(symbol_matches_candidate(target_sym, c) for c in cands if c):
                            return n
                    return None

                def _find_matching_sym(q_node: dict[str, Any], sym_set: set[str]) -> str | None:
                    cands = [str(q_node.get("symbol") or ""), str(q_node.get("id") or ""), str(q_node.get("name") or "")]
                    for sym in sym_set:
                        if any(symbol_matches_candidate(sym, c) for c in cands if c):
                            return sym
                    return None

                # 1. SymPy LHS -> Graph: must have EXPRESSED_BY
                for l_sym in lhs_syms:
                    matched_exp = _matches_any(l_sym, expressed_rows)
                    if not matched_exp:
                        issues.append(
                            {
                                "category": "symbolic_consistency",
                                "severity": "warning",
                                "id": eqid,
                                "node_type": "Equation",
                                "message": f"Equation {eqid} defines LHS output {l_sym!r} in SymPy, but is missing required incoming edge (Quantity)-[:EXPRESSED_BY]->(Equation:{eqid}).",
                            }
                        )

                # 2. SymPy RHS -> Graph: must have USES_SYMBOL
                for r_sym in rhs_syms:
                    matched_used = _matches_any(r_sym, used_rows)
                    if not matched_used:
                        issues.append(
                            {
                                "category": "symbolic_consistency",
                                "severity": "warning",
                                "id": eqid,
                                "node_type": "Equation",
                                "message": f"Equation {eqid} uses free symbol {r_sym!r} in SymPy, but is missing edge (Equation:{eqid})-[:USES_SYMBOL]->(Quantity).",
                            }
                        )

                # 3. Graph EXPRESSED_BY -> SymPy: must be on LHS
                for row in expressed_rows:
                    qid = row["qid"]
                    sym = row.get("symbol") or qid
                    matched_lhs = _find_matching_sym(row, lhs_syms)
                    if not matched_lhs:
                        matched_rhs = _find_matching_sym(row, rhs_syms)
                        if matched_rhs:
                            issues.append(
                                {
                                    "category": "symbolic_consistency",
                                    "severity": "warning",
                                    "id": eqid,
                                    "node_type": "Equation",
                                    "message": f"Quantity {qid} ({sym!r}) is linked via EXPRESSED_BY to Equation {eqid}, but appears on the RHS in SymPy (expected USES_SYMBOL).",
                                }
                            )
                        else:
                            issues.append(
                                {
                                    "category": "symbolic_consistency",
                                    "severity": "warning",
                                    "id": eqid,
                                    "node_type": "Equation",
                                    "message": f"Quantity {qid} ({sym!r}) is linked via EXPRESSED_BY to Equation {eqid}, but does not appear in SymPy file {code_path_str!r}.",
                                }
                            )

                # 4. Graph DEFINED_BY -> SymPy / Graph: DEFINED_BY is an optional refinement on LHS
                for row in defined_rows:
                    qid = row["qid"]
                    sym = row.get("symbol") or qid
                    matched_lhs = _find_matching_sym(row, lhs_syms)
                    if not matched_lhs:
                        issues.append(
                            {
                                "category": "symbolic_consistency",
                                "severity": "warning",
                                "id": eqid,
                                "node_type": "Equation",
                                "message": f"Quantity {qid} ({sym!r}) is linked via DEFINED_BY to Equation {eqid}, but does not appear as LHS output in SymPy file {code_path_str!r}.",
                            }
                        )
                    # Also check: DEFINED_BY refining edge requires EXPRESSED_BY
                    if not any(exp_row["qid"] == qid for exp_row in expressed_rows):
                        issues.append(
                            {
                                "category": "symbolic_consistency",
                                "severity": "warning",
                                "id": eqid,
                                "node_type": "Equation",
                                "message": f"Quantity {qid} is linked via DEFINED_BY to Equation {eqid} without the mandatory EXPRESSED_BY edge.",
                            }
                        )

                # 5. Graph USES_SYMBOL -> SymPy: must be on RHS / input
                for row in used_rows:
                    qid = row["qid"]
                    sym = row.get("symbol") or qid
                    matched_rhs = _find_matching_sym(row, rhs_syms)
                    if not matched_rhs:
                        matched_lhs = _find_matching_sym(row, lhs_syms)
                        if matched_lhs:
                            issues.append(
                                {
                                    "category": "symbolic_consistency",
                                    "severity": "warning",
                                    "id": eqid,
                                    "node_type": "Equation",
                                    "message": f"Quantity {qid} ({sym!r}) is linked via USES_SYMBOL from Equation {eqid}, but is the isolated LHS output in SymPy (expected EXPRESSED_BY).",
                                }
                            )
                        else:
                            issues.append(
                                {
                                    "category": "symbolic_consistency",
                                    "severity": "warning",
                                    "id": eqid,
                                    "node_type": "Equation",
                                    "message": f"Quantity {qid} ({sym!r}) is linked via USES_SYMBOL from Equation {eqid}, but does not appear in SymPy file {code_path_str!r}.",
                                }
                            )

        # 5. Acronym node audit
        if "Acronym" in node_tables:
            acronyms = g.execute(
                "MATCH (a:Acronym) RETURN a.id AS id, a.name AS name, "
                "coalesce(a.short_form, '') AS short_form, "
                "coalesce(a.expansion, '') AS expansion"
            )
            for acr in acronyms:
                aid = acr["id"]
                short_form = (acr.get("short_form") or "").strip()
                expansion = (acr.get("expansion") or "").strip()
                if not short_form:
                    issues.append(
                        {
                            "category": "acronym_quality",
                            "severity": "error",
                            "id": aid,
                            "node_type": "Acronym",
                            "message": f"Acronym {aid} is missing 'short_form' property.",
                        }
                    )
                elif short_form.startswith("(") and short_form.endswith(")"):
                    issues.append(
                        {
                            "category": "acronym_quality",
                            "severity": "warning",
                            "id": aid,
                            "node_type": "Acronym",
                            "message": f"Acronym {aid} short_form {short_form!r} has redundant enclosing parentheses.",
                        }
                    )
                if not expansion:
                    issues.append(
                        {
                            "category": "acronym_quality",
                            "severity": "error",
                            "id": aid,
                            "node_type": "Acronym",
                            "message": f"Acronym {aid} is missing 'expansion' property.",
                        }
                    )

        # 6. Edge Domain & Range Audit against schema companion
        companion_path = g.db_path.parent / "schema" / "schema_companion.json"
        if not companion_path.is_file():
            companion_path = g.db_path.parent.parent / "schema" / "schema_companion.json"
        if companion_path.is_file():
            with contextlib.suppress(Exception):
                comp = load_companion(companion_path)
                for rel in rel_tables:
                    sem = comp.canonical_edge(rel) or comp.edge(rel)
                    if sem and (sem.domain or sem.range):
                        edges = g.execute(
                            f"MATCH (a)-[r:{rel}]->(b) "
                            f"RETURN labels(a) AS fl, a.id AS fid, labels(b) AS tl, b.id AS tid"
                        )
                        for e in edges:
                            fl = (e.get("fl") or [None])[0]
                            tl = (e.get("tl") or [None])[0]
                            fid = e.get("fid")
                            tid = e.get("tid")
                            if sem.domain and fl not in sem.domain:
                                issues.append(
                                    {
                                        "category": "domain_range_violation",
                                        "severity": "error",
                                        "id": f"{fid}-[{rel}]->{tid}",
                                        "node_type": rel,
                                        "message": f"Edge ({fl}:{fid})-[:{rel}]->({tl}:{tid}) violates domain: {fl!r} not in {sem.domain}",
                                    }
                                )
                            if sem.range and tl not in sem.range:
                                issues.append(
                                    {
                                        "category": "domain_range_violation",
                                        "severity": "error",
                                        "id": f"{fid}-[{rel}]->{tid}",
                                        "node_type": rel,
                                        "message": f"Edge ({fl}:{fid})-[:{rel}]->({tl}:{tid}) violates range: {tl!r} not in {sem.range}",
                                    }
                                )

        # 7. Reference edge provenance and valid entity audit
        ref_edge_types = [
            "ADOPTS_FORMULATION",
            "EXTENDS_METHOD",
            "REVISES_ASSUMPTION",
            "EVALUATES_PROPERTY",
            "BENCHMARKS_AGAINST",
            "BACKGROUND_CONTEXT",
        ]
        for ret in ref_edge_types:
            if ret in rel_tables:
                try:
                    edges = g.execute(
                        f"MATCH (a)-[r:{ret}]->(b) "
                        f"RETURN a.id AS fid, b.id AS tid, r.origin AS origin, r.sources AS sources"
                    )
                    for e in edges:
                        fid = e.get("fid")
                        tid = e.get("tid")
                        origin = e.get("origin")
                        sources = e.get("sources")
                        edge_id = f"{fid}-[{ret}]->{tid}"

                        if not fid or not tid:
                            issues.append(
                                {
                                    "category": "reference_edge",
                                    "severity": "error",
                                    "id": edge_id,
                                    "node_type": ret,
                                    "message": f"Reference edge {edge_id} has invalid/missing endpoint id.",
                                }
                            )

                        if not origin or origin not in ("raw", "synthesized", "inferred", "mardi"):
                            issues.append(
                                {
                                    "category": "provenance",
                                    "severity": "error",
                                    "id": edge_id,
                                    "node_type": ret,
                                    "message": f"Reference edge {edge_id} has missing or invalid origin: {origin!r}.",
                                }
                            )

                        if not sources or not isinstance(sources, list) or len(sources) == 0:
                            issues.append(
                                {
                                    "category": "provenance",
                                    "severity": "error",
                                    "id": edge_id,
                                    "node_type": ret,
                                    "message": f"Reference edge {edge_id} is missing required provenance 'sources'.",
                                }
                            )
                except Exception:
                    pass
    finally:
        g.close()

    ok = len([i for i in issues if i["severity"] == "error"]) == 0
    result = {"ok": ok, "issue_count": len(issues), "issues": issues}

    if json_output:
        typer.echo(_json.dumps(result, indent=2))
    else:
        if not issues:
            _console.print(
                "[green]graph lint passed:[/green] 0 issues found across all nodes and edges."
            )
        else:
            status = "[yellow]warnings found[/yellow]" if ok else "[red]errors found[/red]"
            _console.print(f"graph lint ({status}, {len(issues)} issue(s)):")
            for iss in issues:
                color = "red" if iss["severity"] == "error" else "yellow"
                _console.print(
                    f"  [{color}]{iss['severity'].upper()}[/{color}] [{iss['category']}] {iss['message']}"
                )

    if not ok:
        raise typer.Exit(code=1)


@graph_app.command("dedupe")
def cmd_dedupe(
    label: str | None = typer.Option(
        None, "--label", "-l", help="Node table to deduplicate (default: all domain node tables)."
    ),
    threshold: float = typer.Option(
        0.85, "--threshold", "-t", help="Similarity threshold for candidate matching (0.0 - 1.0)."
    ),
    apply: bool = typer.Option(
        False, "--apply", help="Apply deduplication merges (default is dry-run)."
    ),
    kb: Path = _KB_OPT,
    json_output: bool = _JSON_OPT,
) -> None:
    """Detect and merge duplicate domain entities across node tables."""
    import re

    from ..graph.upsert import upsert_edge

    g = _open_db(kb, json_output)
    all_node_tables = [t for t in g.node_table_names() if not t.startswith("_")]
    rel_tables = g.rel_table_names()

    if label:
        target_labels = [label] if label in all_node_tables else []
        if not target_labels:
            _fail(f"Unknown node table {label!r}", json_output)
    else:
        target_labels = [t for t in all_node_tables if t != "Document"]

    proposed_merges: list[dict[str, Any]] = []

    try:
        for nt in target_labels:
            rows = g.execute(f"MATCH (n:{nt}) RETURN n")
            if len(rows) < 2:
                continue

            nodes = []
            for r in rows:
                n_val = r.get("n")
                props: dict[str, Any] = n_val if isinstance(n_val, dict) else r
                nid = props.get("id")
                name = props.get("name") or ""
                summary = props.get("summary") or ""
                sources = props.get("sources") or []
                symbol = props.get("symbol")
                short_form = props.get("short_form")
                expansion = props.get("expansion")
                nodes.append(
                    {
                        "id": nid,
                        "name": name,
                        "summary": summary,
                        "sources": sources,
                        "symbol": symbol,
                        "short_form": short_form,
                        "expansion": expansion,
                        "row": props,
                    }
                )

            visited = set()
            for i, n1 in enumerate(nodes):
                nid1 = n1["id"]
                if nid1 in visited:
                    continue

                t1_tokens = set(re.findall(r"\w+", n1["name"].lower()))
                slug1 = set(re.findall(r"\w+", nid1.lower()))

                duplicates = []
                for j in range(i + 1, len(nodes)):
                    n2 = nodes[j]
                    nid2 = n2["id"]
                    if nid2 in visited:
                        continue

                    # For symbol-bearing nodes, distinct symbols must never be merged
                    if (
                        n1.get("symbol")
                        and n2.get("symbol")
                        and n1["symbol"].strip() != n2["symbol"].strip()
                    ):
                        continue

                    # For acronym nodes, distinct short forms must never be merged
                    if (
                        n1.get("short_form")
                        and n2.get("short_form")
                        and n1["short_form"].strip().upper() != n2["short_form"].strip().upper()
                    ):
                        continue

                    is_match = False
                    if (
                        n1["name"]
                        and n2["name"]
                        and n1["name"].strip().lower() == n2["name"].strip().lower()
                    ):
                        is_match = True
                    else:
                        t2_tokens = set(re.findall(r"\w+", n2["name"].lower()))
                        slug2 = set(re.findall(r"\w+", nid2.lower()))

                        if t1_tokens and t2_tokens:
                            score = len(t1_tokens.intersection(t2_tokens)) / max(
                                len(t1_tokens), len(t2_tokens)
                            )
                            if score >= threshold:
                                is_match = True

                        if not is_match and slug1 and slug2:
                            slug_score = len(slug1.intersection(slug2)) / max(
                                len(slug1), len(slug2)
                            )
                            if slug_score >= threshold:
                                is_match = True

                    if is_match:
                        duplicates.append(n2)
                        visited.add(nid2)

                if duplicates:
                    visited.add(nid1)
                    cluster = [n1, *duplicates]

                    def get_degree(item: dict[str, Any], target_label: str = nt) -> int:
                        cnt = 0
                        for rt in rel_tables:
                            res1 = g.execute(
                                f"MATCH (n:{target_label} {{id: $id}})-[r:{rt}]->() RETURN count(r) AS c",
                                {"id": item["id"]},
                            )
                            res2 = g.execute(
                                f"MATCH ()-[r:{rt}]->(n:{target_label} {{id: $id}}) RETURN count(r) AS c",
                                {"id": item["id"]},
                            )
                            cnt += res1[0]["c"] + res2[0]["c"]
                        return cnt

                    cluster.sort(
                        key=lambda item: (get_degree(item), len(item["name"])),
                        reverse=True,
                    )
                    canonical = cluster[0]
                    dups_to_merge = cluster[1:]

                    for dup in dups_to_merge:
                        dup_id = dup["id"]
                        can_id = canonical["id"]

                        redirected_incoming = []
                        redirected_outgoing = []

                        for rt in rel_tables:
                            in_rows = g.execute(
                                f"MATCH (a)-[r:{rt}]->(b:{nt} {{id: $id}}) RETURN labels(a) AS a_lbl, a.id AS a_id",
                                {"id": dup_id},
                            )
                            for in_r in in_rows:
                                redirected_incoming.append(
                                    (rt, in_r["a_lbl"], in_r["a_id"])
                                )

                            out_rows = g.execute(
                                f"MATCH (a:{nt} {{id: $id}})-[r:{rt}]->(b) RETURN labels(b) AS b_lbl, b.id AS b_id",
                                {"id": dup_id},
                            )
                            for out_r in out_rows:
                                redirected_outgoing.append(
                                    (rt, out_r["b_lbl"], out_r["b_id"])
                                )

                        merge_entry = {
                            "node_type": nt,
                            "canonical_id": can_id,
                            "canonical_name": canonical["name"],
                            "merged_id": dup_id,
                            "merged_name": dup["name"],
                            "redirected_edges_count": len(redirected_incoming)
                            + len(redirected_outgoing),
                        }
                        proposed_merges.append(merge_entry)

                        if apply:
                            for rt, from_lbl, from_id in redirected_incoming:
                                if from_id != can_id:
                                    upsert_edge(
                                        g,
                                        rt,
                                        from_lbl,
                                        from_id,
                                        nt,
                                        can_id,
                                        {
                                            "origin": "raw",
                                            "sources": canonical.get("sources")
                                            or [can_id],
                                            "confidence": 1.0,
                                        },
                                    )

                            for rt, to_lbl, to_id in redirected_outgoing:
                                if to_id != can_id:
                                    upsert_edge(
                                        g,
                                        rt,
                                        nt,
                                        can_id,
                                        to_lbl,
                                        to_id,
                                        {
                                            "origin": "raw",
                                            "sources": canonical.get("sources")
                                            or [can_id],
                                            "confidence": 1.0,
                                        },
                                    )

                            combined_sources = list(
                                set(
                                    (canonical.get("sources") or [])
                                    + (dup.get("sources") or [])
                                )
                            )
                            g.execute(
                                f"MATCH (n:{nt} {{id: $id}}) SET n.sources = $sources",
                                {"id": can_id, "sources": combined_sources},
                            )

                            g.execute(
                                f"MATCH (n:{nt} {{id: $id}}) DETACH DELETE n",
                                {"id": dup_id},
                            )
    finally:
        g.close()

    result = {
        "applied": apply,
        "merged_count": len(proposed_merges),
        "merges": proposed_merges,
    }

    if json_output:
        typer.echo(_json.dumps(result, indent=2))
        return

    mode_str = (
        "[green]Applied[/green]"
        if apply
        else "[yellow]Dry Run (use --apply to execute)[/yellow]"
    )
    if not proposed_merges:
        _console.print(
            f"[green]graph dedupe:[/green] 0 duplicate clusters detected ({mode_str})."
        )
        return

    _console.print(
        f"Graph Deduplication ({mode_str}, {len(proposed_merges)} candidate merge(s)):"
    )
    table = Table(
        "Node Type", "Merged Duplicate", "Canonical Node", "Redirected Edges"
    )
    for m in proposed_merges:
        table.add_row(
            m["node_type"],
            f"{m['merged_id']} ({m['merged_name']})",
            f"{m['canonical_id']} ({m['canonical_name']})",
            str(m["redirected_edges_count"]),
        )
    _console.print(table)


_REFERENCE_REL_TYPES = [
    "ADOPTS_FORMULATION",
    "EXTENDS_METHOD",
    "REVISES_ASSUMPTION",
    "EVALUATES_PROPERTY",
    "BENCHMARKS_AGAINST",
    "BACKGROUND_CONTEXT",
]


@graph_app.command("lineage")
def cmd_lineage(
    entity_id: str = typer.Argument(..., help="Entity ID to trace lineage for."),
    direction: str = typer.Option(
        "both", "--direction", "-d", help="Lineage direction: 'upstream', 'downstream', or 'both'."
    ),
    max_depth: int = typer.Option(
        5, "--max-depth", "-m", help="Maximum traversal depth (1-10)."
    ),
    kb: Path = _KB_OPT,
    json_output: bool = _JSON_OPT,
) -> None:
    """Trace deep technical reference lineage upstream (prior art) and downstream (derivatives)."""
    dir_norm = direction.lower().strip()
    if dir_norm not in ("upstream", "downstream", "both"):
        _fail(f"Invalid direction {direction!r}; must be 'upstream', 'downstream', or 'both'", json_output)

    max_depth = max(1, min(10, max_depth))
    g = _open_db(kb, json_output)

    rel_tables = set(g.rel_table_names())
    active_rel_types = [r for r in _REFERENCE_REL_TYPES if r in rel_tables]

    try:
        # Check that entity exists
        node_res = g.execute(
            "MATCH (n) WHERE n.id = $id RETURN labels(n) AS lbl, n.id AS id, coalesce(n.name, '') AS name",
            {"id": entity_id},
        )
        if not node_res:
            _fail(f"Entity {entity_id!r} not found in graph.", json_output)

        root_label = (node_res[0].get("lbl") or [None])[0] or "Entity"
        root_name = node_res[0].get("name") or entity_id

        upstream_chain: list[dict[str, Any]] = []
        downstream_chain: list[dict[str, Any]] = []

        # BFS traversal helper
        def _traverse(start_id: str, is_upstream: bool) -> list[dict[str, Any]]:
            results: list[dict[str, Any]] = []
            visited: set[str] = {start_id}
            queue: list[tuple[str, int]] = [(start_id, 0)]

            while queue:
                curr_id, depth = queue.pop(0)
                if depth >= max_depth:
                    continue

                for rel in active_rel_types:
                    if is_upstream:
                        # n -> target (n adopts / extends target)
                        q = (
                            f"MATCH (curr {{id: $curr_id}})-[r:{rel}]->(target) "
                            f"RETURN labels(target) AS tl, target.id AS tid, coalesce(target.name, '') AS tname, "
                            f"coalesce(r.aspect, '') AS aspect, coalesce(r.section, '') AS section, "
                            f"coalesce(r.context, '') AS context"
                        )
                    else:
                        # source -> curr (source adopts / extends curr)
                        q = (
                            f"MATCH (source)-[r:{rel}]->(curr {{id: $curr_id}}) "
                            f"RETURN labels(source) AS tl, source.id AS tid, coalesce(source.name, '') AS tname, "
                            f"coalesce(r.aspect, '') AS aspect, coalesce(r.section, '') AS section, "
                            f"coalesce(r.context, '') AS context"
                        )

                    try:
                        rows = g.execute(q, {"curr_id": curr_id})
                    except Exception:
                        rows = []

                    for row in rows:
                        tid = row.get("tid")
                        if not tid:
                            continue
                        tl = (row.get("tl") or [None])[0] or "Entity"
                        item = {
                            "from_id": curr_id if is_upstream else tid,
                            "to_id": tid if is_upstream else curr_id,
                            "rel": rel,
                            "target_id": tid,
                            "target_label": tl,
                            "target_name": row.get("tname") or tid,
                            "depth": depth + 1,
                            "aspect": row.get("aspect") or None,
                            "section": row.get("section") or None,
                            "context": row.get("context") or None,
                        }
                        results.append(item)
                        if tid not in visited:
                            visited.add(tid)
                            queue.append((tid, depth + 1))
            return results

        if dir_norm in ("upstream", "both"):
            upstream_chain = _traverse(entity_id, is_upstream=True)
        if dir_norm in ("downstream", "both"):
            downstream_chain = _traverse(entity_id, is_upstream=False)

    finally:
        g.close()

    result = {
        "entity_id": entity_id,
        "label": root_label,
        "name": root_name,
        "upstream_count": len(upstream_chain),
        "upstream": upstream_chain,
        "downstream_count": len(downstream_chain),
        "downstream": downstream_chain,
    }

    if json_output:
        typer.echo(_json.dumps(result, indent=2))
        return

    _console.print(f"[bold]Lineage trace for {root_label}:{entity_id} ({root_name}):[/bold]")

    if dir_norm in ("upstream", "both"):
        _console.print(f"\n[cyan]Upstream Lineage (Prior art / Dependencies adopted/extended) [{len(upstream_chain)}]:[/cyan]")
        if not upstream_chain:
            _console.print("  (none)")
        else:
            table = Table("Depth", "Relation", "Target Entity", "Aspect / Section / Context")
            for u in upstream_chain:
                details = " | ".join(
                    f"{k}: {v}" for k, v in [("aspect", u["aspect"]), ("sec", u["section"]), ("ctx", u["context"])] if v
                )
                table.add_row(
                    str(u["depth"]),
                    f"-[:{u['rel']}]->",
                    f"{u['target_label']}:{u['target_id']} ({u['target_name']})",
                    details,
                )
            _console.print(table)

    if dir_norm in ("downstream", "both"):
        _console.print(f"\n[cyan]Downstream Lineage (Derivatives / Works adopting/evaluating this) [{len(downstream_chain)}]:[/cyan]")
        if not downstream_chain:
            _console.print("  (none)")
        else:
            table = Table("Depth", "Relation", "Derivative Entity", "Aspect / Section / Context")
            for d in downstream_chain:
                details = " | ".join(
                    f"{k}: {v}" for k, v in [("aspect", d["aspect"]), ("sec", d["section"]), ("ctx", d["context"])] if v
                )
                table.add_row(
                    str(d["depth"]),
                    f"<-[:{d['rel']}]-",
                    f"{d['target_label']}:{d['target_id']} ({d['target_name']})",
                    details,
                )
            _console.print(table)


@graph_app.command("consensus")
def cmd_consensus(
    entity_id: str = typer.Argument(..., help="Entity ID to analyze consensus and evaluations for."),
    kb: Path = _KB_OPT,
    json_output: bool = _JSON_OPT,
) -> None:
    """Aggregate evaluative claims and typed reference edges evaluating an entity to determine consensus."""
    g = _open_db(kb, json_output)

    node_tables = set(g.node_table_names())
    rel_tables = set(g.rel_table_names())

    try:
        # Check that entity exists
        node_res = g.execute(
            "MATCH (n) WHERE n.id = $id RETURN labels(n) AS lbl, n.id AS id, coalesce(n.name, '') AS name",
            {"id": entity_id},
        )
        if not node_res:
            _fail(f"Entity {entity_id!r} not found in graph.", json_output)

        root_label = (node_res[0].get("lbl") or [None])[0] or "Entity"
        root_name = node_res[0].get("name") or entity_id

        evaluations: list[dict[str, Any]] = []
        attitude_counts = {"Positive": 0, "Negative": 0, "Neutral": 0, "Unknown": 0}

        # 1. Query reified Claims about or targeting this entity
        if "Claim" in node_tables:
            claim_q = (
                "MATCH (c:Claim) "
                "WHERE (c)-[:ABOUT]->({id: $id}) OR (c)-[:HAS_OBJECT]->({id: $id}) "
                "RETURN c.id AS id, c.name AS name, c.summary AS summary, "
                "c.predicate AS predicate, coalesce(c.qualifiers, '') AS qualifiers, "
                "c.sources AS sources, coalesce(c.confidence, 1.0) AS confidence"
            )
            try:
                c_rows = g.execute(claim_q, {"id": entity_id})
            except Exception:
                c_rows = []

            for r in c_rows:
                cid = r.get("id")
                q_raw = r.get("qualifiers") or ""
                attitude = "Neutral"
                ref_type = None
                aspect = None

                if q_raw:
                    try:
                        q_parsed = _json.loads(q_raw) if isinstance(q_raw, str) else q_raw
                        if isinstance(q_parsed, dict):
                            attitude = q_parsed.get("attitude") or "Neutral"
                            ref_type = q_parsed.get("reference_type")
                            aspect = q_parsed.get("aspect") or q_parsed.get("target_anchor")
                    except Exception:
                        pass

                att_key = attitude if attitude in attitude_counts else "Unknown"
                attitude_counts[att_key] += 1

                evaluations.append({
                    "kind": "claim",
                    "id": cid,
                    "name": r.get("name") or cid,
                    "summary": r.get("summary") or "",
                    "predicate": r.get("predicate"),
                    "reference_type": ref_type,
                    "attitude": attitude,
                    "aspect": aspect,
                    "sources": r.get("sources") or [],
                    "confidence": r.get("confidence"),
                })

        # 2. Query direct reference edges targeting this entity (incoming reference edges)
        active_ref_types = [r for r in _REFERENCE_REL_TYPES if r in rel_tables]
        for rel in active_ref_types:
            edge_q = (
                f"MATCH (source)-[r:{rel}]->(target {{id: $id}}) "
                f"RETURN labels(source) AS sl, source.id AS sid, coalesce(source.name, '') AS sname, "
                f"coalesce(r.aspect, '') AS aspect, coalesce(r.section, '') AS section, "
                f"coalesce(r.context, '') AS context, r.sources AS sources, "
                f"coalesce(r.confidence, 1.0) AS confidence"
            )
            try:
                e_rows = g.execute(edge_q, {"id": entity_id})
            except Exception:
                e_rows = []

            for er in e_rows:
                sid = er.get("sid")
                sl = (er.get("sl") or [None])[0] or "Entity"
                # For reference edges, default attitude is Neutral unless specified in aspect/context
                attitude = "Neutral"
                attitude_counts["Neutral"] += 1

                evaluations.append({
                    "kind": "edge",
                    "id": f"{sid}-[{rel}]->{entity_id}",
                    "source_id": sid,
                    "source_label": sl,
                    "source_name": er.get("sname") or sid,
                    "reference_type": rel,
                    "attitude": attitude,
                    "aspect": er.get("aspect") or er.get("section") or None,
                    "context": er.get("context") or None,
                    "sources": er.get("sources") or [],
                    "confidence": er.get("confidence"),
                })

    finally:
        g.close()

    total_evals = len(evaluations)
    result = {
        "entity_id": entity_id,
        "label": root_label,
        "name": root_name,
        "total_evaluations": total_evals,
        "attitudes": attitude_counts,
        "evaluations": evaluations,
    }

    if json_output:
        typer.echo(_json.dumps(result, indent=2))
        return

    _console.print(f"[bold]Consensus & Evaluation Summary for {root_label}:{entity_id} ({root_name}):[/bold]")
    _console.print(f"Total Evaluations / Inbound References: [bold]{total_evals}[/bold]")
    _console.print(
        f"Attitude breakdown: [green]Positive: {attitude_counts['Positive']}[/green] | "
        f"[red]Negative: {attitude_counts['Negative']}[/red] | "
        f"[cyan]Neutral: {attitude_counts['Neutral']}[/cyan]"
        + (f" | [yellow]Unknown: {attitude_counts['Unknown']}[/yellow]" if attitude_counts["Unknown"] else "")
    )

    if not evaluations:
        _console.print("\n  [yellow]No evaluative claims or typed inbound reference edges found for this entity.[/yellow]")
        return

    _console.print("\n[bold]Evaluations & Citations:[/bold]")
    table = Table("Kind", "Attitude", "Ref Type", "Source / ID", "Aspect / Summary")
    for ev in evaluations:
        att = ev.get("attitude") or "Neutral"
        color = "green" if att == "Positive" else ("red" if att == "Negative" else "cyan")
        if ev["kind"] == "claim":
            src = f"Claim:{ev['id']}"
            desc = ev.get("summary") or ev.get("name") or ""
            if ev.get("aspect"):
                desc = f"[{ev['aspect']}] {desc}"
        else:
            src = f"{ev['source_label']}:{ev['source_id']}"
            desc = ev.get("context") or ev.get("aspect") or ""

        table.add_row(
            ev["kind"],
            f"[{color}]{att}[/{color}]",
            ev.get("reference_type") or "-",
            src,
            desc,
        )
    _console.print(table)

