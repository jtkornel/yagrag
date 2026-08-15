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
from rich.table import Table

from ..config import KBConfig
from ..graph.connection import GrafeoNotInstalled, GraphDB
from ..graph.upsert import (
    GraphWriteError,
    ProvenanceError,
    execute_batch,
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
    except (FileNotFoundError, GrafeoNotInstalled) as exc:
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
    except Exception as exc:
        _fail(str(exc), json_output)
        return
    finally:
        g.close()
    payload = {"nodes": nodes, "relationships": rels}
    typer.echo(_json.dumps(payload, indent=2))


@graph_app.command("batch")
def cmd_batch(
    batch_file: Optional[Path] = typer.Option(  # noqa: UP007
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
                "c.predicate AS predicate, c.sources AS sources"
            )
            for c in claims:
                cid = c["id"]
                name = c["name"] or ""
                summary = c["summary"] or ""
                predicate = c["predicate"] or ""
                sources = c["sources"] or []

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
                clean_latex = latex.replace("\\", "").strip()

                if "USES_SYMBOL" in rel_tables:
                    symbols = g.execute(
                        "MATCH (e:Equation {id: $id})-[:USES_SYMBOL]->(q) RETURN q.id AS qid, q.symbol AS symbol",
                        {"id": eqid},
                    )
                    for s in symbols:
                        sym = s["symbol"] or ""
                        clean_sym = sym.replace("\\", "").strip()
                        if clean_sym and clean_sym not in clean_latex:
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
                            clean_sym = sym.replace("\\", "").strip()
                            if clean_sym and clean_sym not in clean_latex:
                                issues.append(
                                    {
                                        "category": "symbolic_consistency",
                                        "severity": "warning",
                                        "id": eqid,
                                        "node_type": "Equation",
                                        "message": f"Equation {eqid} linked via {rel_name} to quantity {s['qid']} ({sym!r}) but symbol does not appear in LaTeX formula {latex!r}.",
                                    }
                                )
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
    label: Optional[str] = typer.Option(
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
                nodes.append(
                    {
                        "id": nid,
                        "name": name,
                        "summary": summary,
                        "sources": sources,
                        "symbol": symbol,
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
                    if n1.get("symbol") and n2.get("symbol"):
                        if n1["symbol"].strip() != n2["symbol"].strip():
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

                    def get_degree(item: dict[str, Any]) -> int:
                        cnt = 0
                        for rt in rel_tables:
                            res1 = g.execute(
                                f"MATCH (n:{nt} {{id: $id}})-[r:{rt}]->() RETURN count(r) AS c",
                                {"id": item["id"]},
                            )
                            res2 = g.execute(
                                f"MATCH ()-[r:{rt}]->(n:{nt} {{id: $id}}) RETURN count(r) AS c",
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
