"""`kb math` command group: derive | glossary.

Mathematical derivation tracing, symbolic rendering, and glossaries.
"""

from __future__ import annotations

import json as _json
from pathlib import Path
from typing import Any

import typer
from rich import box
from rich.console import Console
from rich.panel import Panel
from rich.table import Table
from rich.text import Text

from ..config import KBConfig
from ..graph.connection import GrafeoNotInstalled, GraphDB
from ..math.rendering import latex_to_unicode, render_sympy_2d, symbol_name_to_unicode

math_app = typer.Typer(
    name="math",
    help="Mathematical derivation tracing, symbolic rendering, and glossaries.",
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


def _load_config(kb_root: Path, json_output: bool) -> KBConfig:
    try:
        return KBConfig.load(kb_root)
    except FileNotFoundError as exc:
        _fail(str(exc), json_output)
        raise AssertionError  # unreachable


def _open_db(kb_root: Path, config: KBConfig, json_output: bool) -> GraphDB:
    try:
        return GraphDB(kb_root / config.paths.graph_db)
    except GrafeoNotInstalled as exc:
        _fail(str(exc), json_output)
        raise AssertionError  # unreachable


def _clean_prop_dict(row_or_dict: Any) -> dict[str, Any]:
    """Extract and clean properties from a query row or dict."""
    if isinstance(row_or_dict, dict):
        d = row_or_dict
    elif hasattr(row_or_dict, "to_dict"):
        d = row_or_dict.to_dict()
    else:
        d = dict(row_or_dict)
    return {
        (k.split(".", 1)[-1] if "." in k else k): v
        for k, v in d.items()
        if not k.startswith("_")
    }


def _resolve_doc_refs(g: GraphDB, sources: list[str] | None) -> list[dict[str, Any]]:
    """Resolve document IDs to their title, year, and metadata."""
    if not sources:
        return []
    if isinstance(sources, str):
        sources = [sources]
    docs: list[dict[str, Any]] = []
    seen: set[str] = set()
    for s in sources:
        sid = str(s).strip()
        if not sid or sid in seen:
            continue
        seen.add(sid)
        safe_sid = sid.replace("'", "\\'")
        rows = g.execute(
            f"MATCH (d:Document {{id: '{safe_sid}'}}) RETURN d.id AS id, d.name AS name, d.year AS year"
        )
        if rows:
            r = rows[0]
            docs.append({
                "id": r.get("id") or sid,
                "name": r.get("name") or "",
                "year": r.get("year"),
            })
        else:
            docs.append({"id": sid, "name": "", "year": None})
    return docs


def _format_doc_refs(docs: list[dict[str, Any]]) -> str:
    """Format resolved document references into a clean string."""
    parts: list[str] = []
    for d in docs:
        did = d.get("id") or ""
        dname = d.get("name") or ""
        dyear = d.get("year")
        if dname and dyear:
            parts.append(f"{did} — {dname} ({dyear})")
        elif dname:
            parts.append(f"{did} — {dname}")
        else:
            parts.append(did)
    return ", ".join(parts)


def _find_target_nodes(g: GraphDB, target: str) -> list[dict[str, Any]]:
    """Find target nodes matching an ID, ref, symbol, or name."""
    target_clean = target.strip()
    node_tables = g.node_table_names()
    matches: list[dict[str, Any]] = []
    seen_ids: set[str] = set()

    def add_match(label: str, props: dict[str, Any]) -> None:
        nid = str(props.get("id") or "")
        if nid and nid not in seen_ids:
            seen_ids.add(nid)
            props["_label"] = label
            matches.append(props)

    # 1. Exact Ref form: <Label>:<id>
    if ":" in target_clean:
        parts = target_clean.split(":", 1)
        req_label, req_id = parts[0], parts[1]
        if req_label in node_tables:
            safe_id = req_id.replace("'", "\\'")
            rows = g.execute(f"MATCH (n:{req_label} {{id: '{safe_id}'}}) RETURN n")
            for r in rows:
                n_val = r.get("n", r)
                add_match(req_label, _clean_prop_dict(n_val))
            if matches:
                return matches

    # 2. Search by exact ID across all node tables
    safe_target = target_clean.replace("'", "\\'")
    for table in node_tables:
        rows = g.execute(f"MATCH (n:{table} {{id: '{safe_target}'}}) RETURN n")
        for r in rows:
            n_val = r.get("n", r)
            add_match(table, _clean_prop_dict(n_val))

    if matches:
        return matches

    # 3. Search by symbol property across symbol-bearing tables (Quantity, Variable, etc.)
    sym_variants = [target_clean]
    if target_clean.startswith("\\"):
        sym_variants.append(target_clean[1:])
    else:
        sym_variants.append(f"\\{target_clean}")

    for table in node_tables:
        for sym_var in sym_variants:
            safe_sym = sym_var.replace("'", "\\'")
            rows = g.execute(f"MATCH (n:{table} {{symbol: '{safe_sym}'}}) RETURN n")
            for r in rows:
                n_val = r.get("n", r)
                add_match(table, _clean_prop_dict(n_val))

    if matches:
        return matches

    # 4. Search by case-insensitive name
    for table in node_tables:
        rows = g.execute(f"MATCH (n:{table}) WHERE toLower(n.name) = toLower('{safe_target}') RETURN n")
        for r in rows:
            n_val = r.get("n", r)
            add_match(table, _clean_prop_dict(n_val))

    return matches


def _trace_quantity_derivations(
    g: GraphDB,
    kb_root: Path,
    qty_id: str,
    depth: int,
    visited: set[str],
) -> list[dict[str, Any]]:
    """Recursively trace equations defining a quantity and their upstream inputs."""
    if depth <= 0 or qty_id in visited:
        return []

    visited.add(qty_id)
    safe_qid = qty_id.replace("'", "\\'")
    eq_rows = g.execute(
        f"MATCH (q {{id: '{safe_qid}'}})-[r]-(e:Equation) "
        f"WHERE type(r) = 'DEFINED_BY' OR type(r) = 'EXPRESSED_BY' "
        f"RETURN e"
    )
    if not eq_rows:
        eq_rows = g.execute(
            f"MATCH (e:Equation)-[r:DEFINES]->(q {{id: '{safe_qid}'}}) RETURN e"
        )

    derivations: list[dict[str, Any]] = []
    seen_eqs: set[str] = set()

    for r in eq_rows:
        e_val = r.get("e", r)
        e_props = _clean_prop_dict(e_val)
        eq_id = str(e_props.get("id") or "")
        if not eq_id or eq_id in seen_eqs:
            continue
        seen_eqs.add(eq_id)

        latex = str(e_props.get("latex") or "")
        code_path_str = str(e_props.get("code_path") or "")
        latex_uni = latex_to_unicode(latex) if latex else None
        sympy_2d = None
        if code_path_str:
            sympy_2d = render_sympy_2d(kb_root / code_path_str)

        # Associated models
        safe_eid = eq_id.replace("'", "\\'")
        model_rows = g.execute(
            f"MATCH (m)-[r]-(e:Equation {{id: '{safe_eid}'}}) "
            f"WHERE type(r) = 'DEFINED_BY' OR type(r) = 'USES' "
            f"RETURN m, labels(m) AS lbls"
        )
        models: list[dict[str, Any]] = []
        for mr in model_rows:
            m_val = mr.get("m", mr)
            m_props = _clean_prop_dict(m_val)
            lbls = mr.get("lbls") or []
            lbl = lbls[0] if isinstance(lbls, list) and lbls else "Model"
            if lbl != "Equation" and lbl != "Quantity" and lbl != "Variable":
                models.append({
                    "id": m_props.get("id"),
                    "label": lbl,
                    "name": m_props.get("name"),
                })

        # Inputs used in this equation
        input_rows = g.execute(
            f"MATCH (e:Equation {{id: '{safe_eid}'}})-[r:USES_SYMBOL]->(q) RETURN q, labels(q) AS lbls"
        )
        inputs: list[dict[str, Any]] = []
        for ir in input_rows:
            q_val = ir.get("q", ir)
            q_props = _clean_prop_dict(q_val)
            lbls = ir.get("lbls") or []
            lbl = lbls[0] if isinstance(lbls, list) and lbls else "Quantity"
            inp_id = str(q_props.get("id") or "")
            inp_sym = str(q_props.get("symbol") or "")
            inp_uni = (
                latex_to_unicode(inp_sym)
                if inp_sym.startswith("\\")
                else symbol_name_to_unicode(inp_sym)
            )

            upstream_defs: list[dict[str, Any]] = []
            if depth > 1 and inp_id not in visited:
                upstream_defs = _trace_quantity_derivations(
                    g, kb_root, inp_id, depth - 1, visited.copy()
                )

            inputs.append({
                "id": inp_id,
                "label": lbl,
                "symbol": inp_sym,
                "symbol_unicode": inp_uni,
                "name": q_props.get("name"),
                "unit": q_props.get("unit"),
                "summary": q_props.get("summary"),
                "upstream_derivations": upstream_defs,
            })

        e_sources = e_props.get("sources") or []
        e_docs = _resolve_doc_refs(g, e_sources)

        derivations.append({
            "id": eq_id,
            "name": e_props.get("name"),
            "summary": e_props.get("summary"),
            "latex": latex,
            "latex_unicode": latex_uni,
            "sympy_2d": sympy_2d,
            "code_path": code_path_str,
            "sources": e_sources,
            "documents": e_docs,
            "models": models,
            "inputs": inputs,
        })

    return derivations


def _trace_equation_derivations(
    g: GraphDB,
    kb_root: Path,
    eq_props: dict[str, Any],
    depth: int,
) -> dict[str, Any]:
    """Trace an equation's LHS outputs, models, and upstream input derivations."""
    eq_id = str(eq_props.get("id") or "")
    safe_eid = eq_id.replace("'", "\\'")
    latex = str(eq_props.get("latex") or "")
    code_path_str = str(eq_props.get("code_path") or "")
    latex_uni = latex_to_unicode(latex) if latex else None
    sympy_2d = render_sympy_2d(kb_root / code_path_str) if code_path_str else None

    # Output quantities (LHS)
    out_rows = g.execute(
        f"MATCH (q)-[r]-(e:Equation {{id: '{safe_eid}'}}) "
        f"WHERE type(r) = 'DEFINED_BY' OR type(r) = 'EXPRESSED_BY' "
        f"RETURN q, labels(q) AS lbls"
    )
    outputs: list[dict[str, Any]] = []
    for r in out_rows:
        q_val = r.get("q", r)
        q_props = _clean_prop_dict(q_val)
        lbls = r.get("lbls") or []
        lbl = lbls[0] if isinstance(lbls, list) and lbls else "Quantity"
        if lbl in ("Quantity", "Variable"):
            sym = str(q_props.get("symbol") or "")
            outputs.append({
                "id": q_props.get("id"),
                "label": lbl,
                "symbol": sym,
                "symbol_unicode": symbol_name_to_unicode(sym),
                "name": q_props.get("name"),
                "unit": q_props.get("unit"),
            })

    # Models
    model_rows = g.execute(
        f"MATCH (m)-[r]-(e:Equation {{id: '{safe_eid}'}}) "
        f"WHERE type(r) = 'DEFINED_BY' OR type(r) = 'USES' "
        f"RETURN m, labels(m) AS lbls"
    )
    models: list[dict[str, Any]] = []
    for mr in model_rows:
        m_val = mr.get("m", mr)
        m_props = _clean_prop_dict(m_val)
        lbls = mr.get("lbls") or []
        lbl = lbls[0] if isinstance(lbls, list) and lbls else "Model"
        if lbl not in ("Equation", "Quantity", "Variable", "Document"):
            models.append({
                "id": m_props.get("id"),
                "label": lbl,
                "name": m_props.get("name"),
            })

    # Input symbols
    input_rows = g.execute(
        f"MATCH (e:Equation {{id: '{safe_eid}'}})-[r:USES_SYMBOL]->(q) RETURN q, labels(q) AS lbls"
    )
    inputs: list[dict[str, Any]] = []
    visited = {eq_id}
    for ir in input_rows:
        q_val = ir.get("q", ir)
        q_props = _clean_prop_dict(q_val)
        lbls = ir.get("lbls") or []
        lbl = lbls[0] if isinstance(lbls, list) and lbls else "Quantity"
        inp_id = str(q_props.get("id") or "")
        inp_sym = str(q_props.get("symbol") or "")
        inp_uni = (
            latex_to_unicode(inp_sym)
            if inp_sym.startswith("\\")
            else symbol_name_to_unicode(inp_sym)
        )

        upstream_defs: list[dict[str, Any]] = []
        if depth >= 1 and inp_id:
            upstream_defs = _trace_quantity_derivations(
                g, kb_root, inp_id, depth, visited.copy()
            )

        inputs.append({
            "id": inp_id,
            "label": lbl,
            "symbol": inp_sym,
            "symbol_unicode": inp_uni,
            "name": q_props.get("name"),
            "unit": q_props.get("unit"),
            "summary": q_props.get("summary"),
            "upstream_derivations": upstream_defs,
        })

    eq_sources = eq_props.get("sources") or []
    eq_docs = _resolve_doc_refs(g, eq_sources)

    return {
        "id": eq_id,
        "name": eq_props.get("name"),
        "summary": eq_props.get("summary"),
        "latex": latex,
        "latex_unicode": latex_uni,
        "sympy_2d": sympy_2d,
        "code_path": code_path_str,
        "sources": eq_sources,
        "documents": eq_docs,
        "outputs": outputs,
        "models": models,
        "inputs": inputs,
    }


def _trace_model_derivations(
    g: GraphDB,
    kb_root: Path,
    model_props: dict[str, Any],
    depth: int,
) -> dict[str, Any]:
    """Trace equations and symbols defining a Model."""
    model_id = str(model_props.get("id") or "")
    safe_mid = model_id.replace("'", "\\'")

    eq_rows = g.execute(
        f"MATCH (m {{id: '{safe_mid}'}})-[r]-(e:Equation) "
        f"WHERE type(r) = 'DEFINED_BY' OR type(r) = 'USES' "
        f"RETURN e"
    )
    equations: list[dict[str, Any]] = []
    for er in eq_rows:
        e_val = er.get("e", er)
        e_props = _clean_prop_dict(e_val)
        eq_trace = _trace_equation_derivations(g, kb_root, e_props, depth)
        equations.append(eq_trace)

    sym_rows = g.execute(
        f"MATCH (m {{id: '{safe_mid}'}})-[r:USES_SYMBOL]->(q) RETURN q, labels(q) AS lbls"
    )
    direct_symbols: list[dict[str, Any]] = []
    for sr in sym_rows:
        q_val = sr.get("q", sr)
        q_props = _clean_prop_dict(q_val)
        lbls = sr.get("lbls") or []
        lbl = lbls[0] if isinstance(lbls, list) and lbls else "Quantity"
        sym = str(q_props.get("symbol") or "")
        direct_symbols.append({
            "id": q_props.get("id"),
            "label": lbl,
            "symbol": sym,
            "symbol_unicode": symbol_name_to_unicode(sym),
            "name": q_props.get("name"),
            "unit": q_props.get("unit"),
        })

    model_sources = model_props.get("sources") or []
    model_docs = _resolve_doc_refs(g, model_sources)

    return {
        "id": model_id,
        "label": model_props.get("_label", "Model"),
        "name": model_props.get("name"),
        "summary": model_props.get("summary"),
        "sources": model_sources,
        "documents": model_docs,
        "equations": equations,
        "direct_symbols": direct_symbols,
    }


@math_app.command("derive")
def cmd_derive(
    target: str = typer.Argument(
        ...,
        help="Symbol, Quantity ID, Equation ID, or Model ID to derive/trace.",
    ),
    depth: int = typer.Option(
        1,
        "--depth",
        "-d",
        help="Upstream derivation depth.",
    ),
    latex: bool = typer.Option(
        False,
        "--latex",
        help="Display raw LaTeX formula and symbol strings.",
    ),
    kb: Path = _KB_OPT,
    json_output: bool = _JSON_OPT,
) -> None:
    """Trace upstream mathematical derivations for a Symbol, Quantity, Equation, or Model."""
    config = _load_config(kb, json_output)
    g = _open_db(kb, config, json_output)
    try:
        nodes = _find_target_nodes(g, target)
        if not nodes:
            _fail(f"target {target!r} not found in knowledge base graph", json_output, code=1)

        results: list[dict[str, Any]] = []
        for node in nodes:
            lbl = node.get("_label", "")
            if lbl in ("Quantity", "Variable"):
                sym = str(node.get("symbol") or "")
                sym_uni = (
                    latex_to_unicode(sym)
                    if sym.startswith("\\")
                    else symbol_name_to_unicode(sym)
                )
                node_sources = node.get("sources") or []
                node_docs = _resolve_doc_refs(g, node_sources)
                derivs = _trace_quantity_derivations(
                    g, kb, str(node.get("id")), depth, set()
                )
                results.append({
                    "target_type": "Quantity",
                    "id": node.get("id"),
                    "label": lbl,
                    "symbol": sym,
                    "symbol_unicode": sym_uni,
                    "name": node.get("name"),
                    "unit": node.get("unit"),
                    "summary": node.get("summary"),
                    "sources": node_sources,
                    "documents": node_docs,
                    "derivations": derivs,
                })
            elif lbl == "Equation":
                eq_res = _trace_equation_derivations(g, kb, node, depth)
                eq_res["target_type"] = "Equation"
                results.append(eq_res)
            else:
                model_res = _trace_model_derivations(g, kb, node, depth)
                model_res["target_type"] = "Model"
                results.append(model_res)
    finally:
        g.close()

    if json_output:
        typer.echo(_json.dumps({
            "target": target,
            "depth": depth,
            "found": True,
            "results": results,
        }, indent=2))
        return

    # Rich human-readable display
    for res in results:
        target_type = res["target_type"]
        top_sources = set(res.get("sources") or []) | {
            d["id"] for d in res.get("documents", []) if d.get("id")
        }

        if target_type == "Quantity":
            header_text = Text()
            header_text.append(f"Quantity: {res['symbol_unicode']}", style="bold bright_green")
            if latex and res.get("symbol") and res["symbol"] != res["symbol_unicode"]:
                header_text.append(f"  ({res['symbol']})", style="dim cyan")
            if res.get("unit"):
                header_text.append(f"  [{res['unit']}]", style="magenta")
            header_text.append(f"\n{res.get('name') or ''}", style="bold white")
            if res.get("summary"):
                header_text.append(f"\n{res['summary']}", style="dim white")
            if res.get("documents"):
                header_text.append(f"\nSource: {_format_doc_refs(res['documents'])}", style="dim yellow")
            elif res.get("sources"):
                header_text.append(f"\nSource: {', '.join(res['sources'])}", style="dim yellow")

            _console.print(Panel(
                header_text,
                title=f"[bold cyan]Derivation Trace: {res['id']}[/]",
                border_style="cyan",
            ))

            derivs = res.get("derivations", [])
            if not derivs:
                _console.print("[yellow]No defining equations found for this symbol in the graph.[/yellow]\n")
                continue

            for d in derivs:
                _console.print(f"\n[bold magenta]Defining Equation:[/] [bold cyan]{d['name']}[/] [dim]({d['id']})[/]")
                d_sources = set(d.get("sources") or []) | {
                    doc["id"] for doc in d.get("documents", []) if doc.get("id")
                }
                if d_sources and not d_sources.issubset(top_sources):
                    if d.get("documents"):
                        _console.print(f"  [dim]Source Document:[/] [yellow]{_format_doc_refs(d['documents'])}[/]")
                    elif d.get("sources"):
                        _console.print(f"  [dim]Source Document:[/] [yellow]{', '.join(d['sources'])}[/]")

                if d.get("sympy_2d"):
                    _console.print(Panel(
                        Text(d["sympy_2d"], style="bold yellow"),
                        title=f"[dim]SymPy 2D ({d.get('code_path') or ''})[/]",
                        border_style="yellow",
                    ))
                elif d.get("latex_unicode"):
                    _console.print(f"  [bold green]Formula:[/]  {d['latex_unicode']}")

                if latex and d.get("latex"):
                    _console.print(f"  [dim]LaTeX:[/]    {d['latex']}")

                if d.get("models"):
                    m_names = ", ".join(f"{m['name']} ({m['id']})" for m in d["models"])
                    _console.print(f"  [bold cyan]Associated Model(s):[/] {m_names}")

                inputs = d.get("inputs", [])
                if inputs:
                    table = Table(
                        title=f"Input Symbols used in {d['id']}",
                        box=box.SIMPLE_HEAVY,
                        header_style="bold cyan",
                    )
                    table.add_column("Symbol", style="bold green")
                    if latex:
                        table.add_column("LaTeX / ID", style="dim")
                    else:
                        table.add_column("ID", style="dim")
                    table.add_column("Name", style="white")
                    table.add_column("Unit", style="magenta")
                    table.add_column("Upstream Derivation", style="yellow")

                    for inp in inputs:
                        up_str = (
                            f"{len(inp['upstream_derivations'])} equation(s)"
                            if inp.get("upstream_derivations")
                            else "-"
                        )
                        id_col_val = (
                            f"{inp['symbol']}\n{inp['id']}"
                            if latex and inp.get("symbol")
                            else inp["id"]
                        )
                        table.add_row(
                            inp["symbol_unicode"],
                            id_col_val,
                            inp.get("name") or "-",
                            inp.get("unit") or "-",
                            up_str,
                        )
                    _console.print(table)

                    # Print recursive upstream derivations if any
                    for inp in inputs:
                        for up_d in inp.get("upstream_derivations", []):
                            up_sources = set(up_d.get("sources") or []) | {
                                doc["id"] for doc in up_d.get("documents", []) if doc.get("id")
                            }
                            doc_suffix = ""
                            if up_sources and not up_sources.issubset(top_sources):
                                doc_suffix = f" [dim]({', '.join(sorted(up_sources))})[/]"

                            _console.print(
                                f"    ↳ [dim]Upstream for {inp['symbol_unicode']}:[/] "
                                f"[bold cyan]{up_d['id']}[/] [green]{up_d.get('latex_unicode') or ''}[/]{doc_suffix}"
                            )
                            if latex and up_d.get("latex"):
                                _console.print(f"       [dim]LaTeX:[/] {up_d['latex']}")

        elif target_type == "Equation":
            header_text = Text()
            header_text.append(f"Equation: {res.get('name') or res['id']}", style="bold cyan")
            if res.get("summary"):
                header_text.append(f"\n{res['summary']}", style="dim white")
            if res.get("documents"):
                header_text.append(f"\nSource: {_format_doc_refs(res['documents'])}", style="dim yellow")
            elif res.get("sources"):
                header_text.append(f"\nSource: {', '.join(res['sources'])}", style="dim yellow")

            _console.print(Panel(
                header_text,
                title=f"[bold cyan]Equation: {res['id']}[/]",
                border_style="cyan",
            ))

            if res.get("sympy_2d"):
                _console.print(Panel(
                    Text(res["sympy_2d"], style="bold yellow"),
                    title=f"[dim]SymPy 2D ({res.get('code_path') or ''})[/]",
                    border_style="yellow",
                ))
            elif res.get("latex_unicode"):
                _console.print(f"  [bold green]Formula:[/]  {res['latex_unicode']}")

            if latex and res.get("latex"):
                _console.print(f"  [dim]LaTeX:[/]    {res['latex']}")

            if res.get("outputs"):
                out_strs = [
                    f"{o['symbol_unicode']} ({o['name']})" for o in res["outputs"]
                ]
                _console.print(f"  [bold bright_green]Defined Output(s):[/] {', '.join(out_strs)}")

            if res.get("models"):
                m_names = ", ".join(f"{m['name']} ({m['id']})" for m in res["models"])
                _console.print(f"  [bold cyan]Associated Model(s):[/] {m_names}")

            inputs = res.get("inputs", [])
            if inputs:
                table = Table(
                    title=f"Input Symbols for {res['id']}",
                    box=box.SIMPLE_HEAVY,
                    header_style="bold cyan",
                )
                table.add_column("Symbol", style="bold green")
                if latex:
                    table.add_column("LaTeX / ID", style="dim")
                else:
                    table.add_column("ID", style="dim")
                table.add_column("Name", style="white")
                table.add_column("Unit", style="magenta")
                table.add_column("Upstream Derivation", style="yellow")

                for inp in inputs:
                    up_str = (
                        f"{len(inp['upstream_derivations'])} equation(s)"
                        if inp.get("upstream_derivations")
                        else "-"
                    )
                    id_col_val = (
                        f"{inp['symbol']}\n{inp['id']}"
                        if latex and inp.get("symbol")
                        else inp["id"]
                    )
                    table.add_row(
                        inp["symbol_unicode"],
                        id_col_val,
                        inp.get("name") or "-",
                        inp.get("unit") or "-",
                        up_str,
                    )
                _console.print(table)

                # Print recursive upstream derivations if any
                for inp in inputs:
                    for up_d in inp.get("upstream_derivations", []):
                        up_sources = set(up_d.get("sources") or []) | {
                            doc["id"] for doc in up_d.get("documents", []) if doc.get("id")
                        }
                        doc_suffix = ""
                        if up_sources and not up_sources.issubset(top_sources):
                            doc_suffix = f" [dim]({', '.join(sorted(up_sources))})[/]"

                        _console.print(
                            f"    ↳ [dim]Upstream for {inp['symbol_unicode']}:[/] "
                            f"[bold cyan]{up_d['id']}[/] [green]{up_d.get('latex_unicode') or ''}[/]{doc_suffix}"
                        )
                        if latex and up_d.get("latex"):
                            _console.print(f"       [dim]LaTeX:[/] {up_d['latex']}")

        elif target_type == "Model":
            header_text = Text()
            header_text.append(f"{res['label']}: {res.get('name') or res['id']}", style="bold magenta")
            if res.get("summary"):
                header_text.append(f"\n{res['summary']}", style="dim white")
            if res.get("documents"):
                header_text.append(f"\nSource: {_format_doc_refs(res['documents'])}", style="dim yellow")
            elif res.get("sources"):
                header_text.append(f"\nSource: {', '.join(res['sources'])}", style="dim yellow")

            _console.print(Panel(
                header_text,
                title=f"[bold magenta]Model Derivation: {res['id']}[/]",
                border_style="magenta",
            ))

            for eq in res.get("equations", []):
                _console.print(f"\n[bold cyan]Equation:[/] {eq.get('name')} [dim]({eq['id']})[/]")
                eq_sources = set(eq.get("sources") or []) | {
                    doc["id"] for doc in eq.get("documents", []) if doc.get("id")
                }
                if eq_sources and not eq_sources.issubset(top_sources):
                    if eq.get("documents"):
                        _console.print(f"  [dim]Source Document:[/] [yellow]{_format_doc_refs(eq['documents'])}[/]")
                    elif eq.get("sources"):
                        _console.print(f"  [dim]Source Document:[/] [yellow]{', '.join(eq['sources'])}[/]")

                if eq.get("sympy_2d"):
                    _console.print(Panel(
                        Text(eq["sympy_2d"], style="bold yellow"),
                        title=f"[dim]SymPy 2D ({eq.get('code_path') or ''})[/]",
                        border_style="yellow",
                    ))
                elif eq.get("latex_unicode"):
                    _console.print(f"  [bold green]Formula:[/] {eq['latex_unicode']}")

                if latex and eq.get("latex"):
                    _console.print(f"  [dim]LaTeX:[/]    {eq['latex']}")


@math_app.command("glossary")
def cmd_glossary(
    doc: str | None = typer.Option(
        None,
        "--doc",
        help="Filter symbols by document ID (e.g. raw-0002).",
    ),
    model: str | None = typer.Option(
        None,
        "--model",
        help="Filter symbols by model ID.",
    ),
    latex: bool = typer.Option(
        False,
        "--latex",
        help="Display raw LaTeX symbols.",
    ),
    kb: Path = _KB_OPT,
    json_output: bool = _JSON_OPT,
) -> None:
    """List and inspect domain mathematical symbols, quantities, and variables."""
    config = _load_config(kb, json_output)
    g = _open_db(kb, config, json_output)
    try:
        node_tables = g.node_table_names()
        target_labels = [lbl for lbl in ("Quantity", "Variable") if lbl in node_tables]

        symbols_list: list[dict[str, Any]] = []
        seen_ids: set[str] = set()

        for lbl in target_labels:
            rows = g.execute(f"MATCH (q:{lbl}) RETURN q")
            for r in rows:
                q_val = r.get("q", r)
                props = _clean_prop_dict(q_val)
                qid = str(props.get("id") or "")
                if not qid or qid in seen_ids:
                    continue

                sources = props.get("sources") or []
                if isinstance(sources, str):
                    sources = [sources]

                # Filter by doc if requested
                if doc:
                    safe_doc = doc.strip().lower()
                    doc_match = any(safe_doc in str(s).lower() for s in sources)
                    if not doc_match:
                        # Check graph connection to doc
                        safe_qid = qid.replace("'", "\\'")
                        d_rows = g.execute(
                            f"MATCH (d:Document)-[r]-(q:{lbl} {{id: '{safe_qid}'}}) "
                            f"WHERE toLower(d.id) = '{safe_doc}' RETURN d"
                        )
                        if not d_rows:
                            continue

                # Filter by model if requested
                if model:
                    safe_mid = model.strip().replace("'", "\\'")
                    safe_qid = qid.replace("'", "\\'")
                    m_rows = g.execute(
                        f"MATCH (m)-[r]-(q:{lbl} {{id: '{safe_qid}'}}) "
                        f"WHERE m.id = '{safe_mid}' RETURN m"
                    )
                    if not m_rows:
                        # Check through equation
                        m_rows = g.execute(
                            f"MATCH (m)-[r1]-(e:Equation)-[r2]-(q:{lbl} {{id: '{safe_qid}'}}) "
                            f"WHERE m.id = '{safe_mid}' RETURN m"
                        )
                        if not m_rows:
                            continue

                seen_ids.add(qid)
                raw_sym = str(props.get("symbol") or "")
                uni_sym = (
                    latex_to_unicode(raw_sym)
                    if raw_sym.startswith("\\")
                    else symbol_name_to_unicode(raw_sym)
                )

                # Find defining equations & usages
                safe_qid = qid.replace("'", "\\'")
                def_rows = g.execute(
                    f"MATCH (q:{lbl} {{id: '{safe_qid}'}})-[r]-(e:Equation) "
                    f"WHERE type(r) = 'DEFINED_BY' OR type(r) = 'EXPRESSED_BY' "
                    f"RETURN e.id AS id"
                )
                def_eqs = sorted({str(dr.get("id")) for dr in def_rows if dr.get("id")})

                use_rows = g.execute(
                    f"MATCH (e:Equation)-[r:USES_SYMBOL]->(q:{lbl} {{id: '{safe_qid}'}}) "
                    f"RETURN e.id AS id"
                )
                used_eqs = sorted({str(ur.get("id")) for ur in use_rows if ur.get("id")})

                # Associated models
                mod_rows = g.execute(
                    f"MATCH (m)-[r]-(q:{lbl} {{id: '{safe_qid}'}}) "
                    f"WHERE type(r) = 'USES_SYMBOL' OR type(r) = 'DEFINED_BY' "
                    f"RETURN m.id AS id, labels(m) AS lbls"
                )
                mod_ids: list[str] = []
                for mr in mod_rows:
                    mid = str(mr.get("id") or "")
                    lbls = mr.get("lbls") or []
                    mlbl = lbls[0] if isinstance(lbls, list) and lbls else ""
                    if mlbl not in ("Quantity", "Variable", "Equation", "Document") and mid:
                        mod_ids.append(mid)
                mod_ids = sorted(set(mod_ids))

                symbols_list.append({
                    "id": qid,
                    "label": lbl,
                    "symbol": raw_sym,
                    "symbol_unicode": uni_sym,
                    "name": props.get("name") or "",
                    "unit": props.get("unit") or "-",
                    "summary": props.get("summary") or "",
                    "sources": sources,
                    "defining_equations": def_eqs,
                    "used_in_equations": used_eqs,
                    "models": mod_ids,
                })
    finally:
        g.close()

    symbols_list.sort(key=lambda s: (s["symbol_unicode"].lower(), s["id"]))

    if json_output:
        typer.echo(_json.dumps({
            "doc": doc,
            "model": model,
            "count": len(symbols_list),
            "symbols": symbols_list,
        }, indent=2))
        return

    if not symbols_list:
        _console.print("[dim]no symbols found matching criteria[/dim]")
        return

    title_parts = ["Mathematical Symbol Glossary"]
    if doc:
        title_parts.append(f"doc={doc}")
    if model:
        title_parts.append(f"model={model}")
    title = f"{' ('.join(title_parts)}{')' if len(title_parts) > 1 else ''} — {len(symbols_list)} symbols"

    table = Table(
        title=title,
        box=box.ROUNDED,
        header_style="bold cyan",
        show_lines=True,
    )
    table.add_column("Symbol", style="bold green")
    if latex:
        table.add_column("LaTeX / ID", style="dim", overflow="fold")
    else:
        table.add_column("ID", style="dim", overflow="fold")
    table.add_column("Name & Description", style="white", overflow="fold")
    table.add_column("Unit", style="magenta")
    table.add_column("Sources", style="yellow")
    table.add_column("Context", style="cyan", overflow="fold")

    for item in symbols_list:
        if latex and item["symbol"]:
            latex_and_id = f"{item['symbol']}\n[dim]{item['id']}[/dim]"
        else:
            latex_and_id = item["id"]
        name_desc = f"[bold]{item['name']}[/bold]"
        if item['summary']:
            name_desc += f"\n[dim]{item['summary']}[/dim]"
        srcs = ", ".join(item['sources']) if item['sources'] else "-"

        ctx_parts: list[str] = []
        if item["defining_equations"]:
            ctx_parts.append(f"Defined by: {', '.join(item['defining_equations'])}")
        if item["used_in_equations"]:
            ctx_parts.append(f"Downstream: {', '.join(item['used_in_equations'])}")
        if item["models"]:
            ctx_parts.append(f"Models: {', '.join(item['models'])}")
        ctx_str = "\n".join(ctx_parts) if ctx_parts else "-"

        table.add_row(
            item["symbol_unicode"],
            latex_and_id,
            name_desc,
            item["unit"],
            srcs,
            ctx_str,
        )

    _console.print(table)
