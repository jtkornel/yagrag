"""`kb doc` command group: add | list | show | remove | text.

Documents are ingested into the KB's document store with strict raw vs
synthesized separation and mandatory provenance for synthesized documents.
"""

from __future__ import annotations

import json as _json
from pathlib import Path
from typing import Any, List, Optional, cast

import typer
from rich.console import Console
from rich.table import Table

from ..config import KBConfig
from ..store.documents import DocKind, DocumentStore, StoreError

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


def _slugify(text: str, max_words: int = 5) -> str:
    import re

    words = re.findall(r"[A-Za-z0-9]+", text.lower())[:max_words]
    return "-".join(words) if words else "doc"


def _canonicalize_url(url: str) -> str:
    if not url:
        return ""
    import re

    u = url.strip()
    m_doi = re.search(
        r"(?:doi\.org/|doi:)(10\.\d{4,9}/[-._;()/:A-Za-z0-9]+)", u, re.IGNORECASE
    )
    if m_doi:
        return f"https://doi.org/{m_doi.group(1)}"
    m_arxiv = re.search(
        r"arxiv\.org/(?:abs|pdf)/(\d{4}\.\d{4,5}(?:v\d+)?)", u, re.IGNORECASE
    )
    if m_arxiv:
        return f"https://arxiv.org/abs/{m_arxiv.group(1)}"
    u = re.sub(r"[?&]utm_[^&#]*", "", u)
    u = re.sub(r"#.*$", "", u)
    u = u.rstrip("?&")
    if u.startswith("http://"):
        u = "https://" + u[7:]
    return u


def _clean_title(title: str) -> str:
    if not title:
        return ""
    import re

    t = title.strip()
    t = re.sub(r"^\[\d+\]\s*", "", t)
    t = re.sub(r"^\(\d+\)\s*", "", t)
    t = re.sub(r"^\d+\.\s*", "", t)
    t = t.strip(' ".,;:').strip()
    words = t.split()
    if len(words) >= 3 and t.isupper():
        t = t.title()
    return t


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
    url: str = typer.Option("", "--url", help="Origin URL of the document."),
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
            cast(DocKind, kind),
            title=title,
            sources=list(source),
            tags=list(tag),
            notes=notes,
            url=url,
        )
    except StoreError as exc:
        _fail(str(exc), json_output)
        return

    # Automatically upsert Document node in graph and reconcile matching stubs
    cfg = KBConfig.load(kb)
    db_path = kb / cfg.paths.graph_db
    reconciled_stub_info = None
    if db_path.exists():
        try:
            import re
            from ..graph.connection import open_graph
            from ..graph.upsert import upsert_edge, upsert_node

            with open_graph(db_path) as g:
                props: dict[str, Any] = {
                    "id": rec.id,
                    "name": rec.title,
                    "kind": rec.kind,
                    "path": rec.path,
                    "format": rec.format,
                    "origin": rec.kind,
                    "sources": [rec.id],
                }
                if rec.url:
                    props["url"] = rec.url
                upsert_node(g, "Document", props)

                # Automatic stub reconciliation for raw ingested papers
                if rec.kind == "raw" and rec.title:
                    title_tokens = set(re.findall(r"\w+", rec.title.lower()))
                    if title_tokens:
                        stubs = g.execute(
                            "MATCH (s:Document) WHERE s.kind = 'stub' OR s.kind = 'placeholder' "
                            "RETURN s.id AS id, s.name AS name, s.url AS url, s.sources AS sources"
                        )
                        matched_stub = None
                        for s in stubs:
                            if rec.url and s.get("url") and s.get("url") == rec.url:
                                matched_stub = s
                                break
                            s_tokens = set(re.findall(r"\w+", (s.get("name") or "").lower()))
                            if s_tokens:
                                score = len(title_tokens.intersection(s_tokens)) / max(len(title_tokens), len(s_tokens))
                                if score >= 0.75:
                                    matched_stub = s
                                    break

                        if matched_stub:
                            sid = matched_stub["id"]
                            citing_rows = g.execute(
                                "MATCH (d:Document)-[:CITES]->(s:Document {id: $id}) RETURN d.id AS citing_id",
                                {"id": sid},
                            )
                            for c in citing_rows:
                                cid = c["citing_id"]
                                upsert_edge(
                                    g,
                                    "CITES",
                                    "Document",
                                    cid,
                                    "Document",
                                    rec.id,
                                    {"origin": "raw", "sources": [cid], "confidence": 1.0},
                                )
                            g.execute("MATCH (s:Document {id: $id}) DETACH DELETE s", {"id": sid})
                            reconciled_stub_info = {
                                "stub_id": sid,
                                "redirected_citations": len(citing_rows),
                            }
        except Exception:
            pass  # Do not block store creation if graph DB is uninitialized or fails

    out_data = rec.to_dict()
    if reconciled_stub_info:
        out_data["reconciled_stub"] = reconciled_stub_info

    if json_output:
        typer.echo(_json.dumps(out_data, indent=2))
    else:
        msg = f"[green]added:[/green] {rec.id} → {rec.path}"
        if reconciled_stub_info:
            msg += f" (reconciled stub {reconciled_stub_info['stub_id']}, {reconciled_stub_info['redirected_citations']} citations redirected)"
        _console.print(msg)


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


@doc_app.command("cite")
def cmd_cite(
    citing_id: str = typer.Argument(..., help="Citing document id (e.g. raw-0001)."),
    title: str = typer.Option(..., "--title", "-t", help="Title of cited paper."),
    year: Optional[int] = typer.Option(None, "--year", "-y", help="Publication year of cited paper."),
    url: Optional[str] = typer.Option(None, "--url", "-u", help="URL, DOI, or arXiv link."),
    ref: Optional[str] = typer.Option(None, "--ref", "-r", help="Full citation string from bibliography."),
    to_doc: Optional[str] = typer.Option(None, "--to-doc", help="Explicit existing target document id."),
    kb: Path = _KB_OPT,
    json_output: bool = _JSON_OPT,
) -> None:
    """Record a citation from an ingested document, automatically matching or creating a placeholder stub."""
    cfg = KBConfig.load(kb)
    db_path = kb / cfg.paths.graph_db
    if not db_path.exists():
        _fail("Graph database not found; run migrations first", json_output)

    import re
    from ..graph.connection import open_graph
    from ..graph.upsert import upsert_edge, upsert_node

    with open_graph(db_path) as g:
        citing_rows = g.execute("MATCH (d:Document {id: $id}) RETURN d.id AS id, d.kind AS kind", {"id": citing_id})
        if not citing_rows:
            _fail(f"Citing document {citing_id!r} not found in graph", json_output)

        target_id: str
        target_kind: str
        action: str

        if to_doc:
            tgt_rows = g.execute("MATCH (d:Document {id: $id}) RETURN d.id AS id, d.kind AS kind", {"id": to_doc})
            if not tgt_rows:
                _fail(f"Target document {to_doc!r} not found in graph", json_output)
            target_id = to_doc
            target_kind = tgt_rows[0].get("kind") or "raw"
            action = "linked_explicit"
        else:
            found = None
            if url:
                url_rows = g.execute(
                    "MATCH (d:Document) WHERE d.url = $url RETURN d.id AS id, d.kind AS kind, d.sources AS sources",
                    {"url": url},
                )
                if url_rows:
                    found = url_rows[0]

            if not found:
                query_tokens = set(re.findall(r"\w+", title.lower()))
                all_docs = g.execute(
                    "MATCH (d:Document) RETURN d.id AS id, d.name AS name, d.kind AS kind, d.sources AS sources"
                )
                best_score = 0.0
                best_doc = None
                for d in all_docs:
                    d_tokens = set(re.findall(r"\w+", (d.get("name") or "").lower()))
                    if not d_tokens:
                        continue
                    score = len(query_tokens.intersection(d_tokens)) / max(len(query_tokens), len(d_tokens))
                    if score > best_score:
                        best_score = score
                        best_doc = d
                if best_score >= 0.75 and best_doc:
                    found = best_doc

            if found:
                target_id = found["id"]
                target_kind = found.get("kind") or "raw"
                action = f"matched_{target_kind}"
                if target_kind in ("stub", "placeholder"):
                    curr_sources = list(found.get("sources") or [])
                    if citing_id not in curr_sources:
                        curr_sources.append(citing_id)

                    set_clauses = ["d.sources = $sources"]
                    params: dict[str, Any] = {"id": target_id, "sources": curr_sources}

                    # Additive enrichment of missing metadata
                    if year and not found.get("year"):
                        set_clauses.append("d.year = $year")
                        params["year"] = int(year)
                    if url and not found.get("url"):
                        set_clauses.append("d.url = $url")
                        params["url"] = url
                    if ref:
                        curr_sum = found.get("summary") or ""
                        if not curr_sum:
                            set_clauses.append("d.summary = $summary")
                            params["summary"] = ref
                        elif ref not in curr_sum:
                            set_clauses.append("d.summary = $summary")
                            params["summary"] = f"{curr_sum}\n\n[Cited in {citing_id}]: {ref}"

                    g.execute(
                        f"MATCH (d:Document {{id: $id}}) SET {', '.join(set_clauses)}",
                        params,
                    )
            else:
                slug = _slugify(title, max_words=4)
                year_part = f"-{year}" if year else ""
                target_id = f"stub-{slug}{year_part}"
                existing = g.execute("MATCH (d:Document {id: $id}) RETURN d.id AS id", {"id": target_id})
                if existing:
                    import uuid

                    target_id = f"stub-{slug}{year_part}-{str(uuid.uuid4())[:4]}"

                target_kind = "stub"
                action = "created_stub"
                stub_props: dict[str, Any] = {
                    "id": target_id,
                    "name": title,
                    "kind": "stub",
                    "origin": "raw",
                    "sources": [citing_id],
                }
                if year:
                    stub_props["year"] = int(year)
                if url:
                    stub_props["url"] = url
                if ref:
                    stub_props["summary"] = ref

                upsert_node(g, "Document", stub_props)

        upsert_edge(
            g,
            "CITES",
            "Document",
            citing_id,
            "Document",
            target_id,
            {"origin": "raw", "sources": [citing_id], "confidence": 1.0},
        )

    res = {
        "citing_doc": citing_id,
        "target_doc": target_id,
        "target_kind": target_kind,
        "action": action,
        "title": title,
    }
    if json_output:
        typer.echo(_json.dumps(res, indent=2))
    else:
        _console.print(
            f"[green]cited:[/green] ({citing_id})-[:CITES]->({target_kind}:{target_id}) [{action}]"
        )


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


@doc_app.command("stubs")
def cmd_stubs(
    min_cites: int = typer.Option(1, "--min-cites", "-m", help="Minimum incoming citations."),
    kb: Path = _KB_OPT,
    json_output: bool = _JSON_OPT,
) -> None:
    """List cited external literature placeholder stubs sorted by citation count."""
    cfg = KBConfig.load(kb)
    db_path = kb / cfg.paths.graph_db
    if not db_path.exists():
        _fail("Graph database not found; run migrations first", json_output)

    from ..graph.connection import open_graph

    with open_graph(db_path) as g:
        rows = g.execute(
            "MATCH (s:Document) WHERE s.kind = 'stub' OR s.kind = 'placeholder' "
            "RETURN s.id AS id, s.name AS title, s.year AS year, s.url AS url, "
            "s.summary AS summary, s.sources AS sources"
        )
        stubs = []
        for r in rows:
            sid = r["id"]
            citing_rows = g.execute(
                "MATCH (d:Document)-[:CITES]->(s:Document {id: $id}) RETURN d.id AS citing_id",
                {"id": sid},
            )
            citing_ids = sorted(
                list(
                    set(
                        [c["citing_id"] for c in citing_rows]
                        + (r.get("sources") or [])
                    )
                )
            )
            cites_count = len(citing_ids)
            if cites_count >= min_cites:
                stubs.append(
                    {
                        "id": sid,
                        "title": r.get("title") or sid,
                        "year": r.get("year"),
                        "url": r.get("url") or "",
                        "summary": r.get("summary") or "",
                        "cites_count": cites_count,
                        "cited_by": citing_ids,
                    }
                )

        stubs.sort(key=lambda s: s["cites_count"], reverse=True)

    if json_output:
        typer.echo(_json.dumps({"stubs": stubs}, indent=2))
        return

    if not stubs:
        _console.print("[dim]No placeholder stub documents found.[/dim]")
        return

    table = Table("cites", "id", "year", "title", "cited_by")
    for s in stubs:
        table.add_row(
            str(s["cites_count"]),
            s["id"],
            str(s["year"] or "-"),
            s["title"],
            ", ".join(s["cited_by"]),
        )
    _console.print(table)


@doc_app.command("match-stubs")
def cmd_match_stubs(
    query: str = typer.Argument(..., help="Title, author, or keyword query to match."),
    kb: Path = _KB_OPT,
    json_output: bool = _JSON_OPT,
) -> None:
    """Find matching placeholder stubs or existing documents by title/keyword."""
    cfg = KBConfig.load(kb)
    db_path = kb / cfg.paths.graph_db
    if not db_path.exists():
        _fail("Graph database not found", json_output)

    import re

    tokens = set(re.findall(r"\w+", query.lower()))
    if not tokens:
        _fail("Query must contain alphanumeric words", json_output)

    from ..graph.connection import open_graph

    with open_graph(db_path) as g:
        rows = g.execute(
            "MATCH (d:Document) RETURN d.id AS id, d.name AS title, d.kind AS kind, "
            "d.year AS year, d.url AS url, d.summary AS summary"
        )
        matches = []
        for r in rows:
            title = r.get("title") or ""
            doc_tokens = set(re.findall(r"\w+", title.lower()))
            if not doc_tokens:
                continue
            common = tokens.intersection(doc_tokens)
            if common:
                score = len(common) / len(tokens)
                matches.append(
                    {
                        "id": r["id"],
                        "kind": r.get("kind"),
                        "title": title,
                        "year": r.get("year"),
                        "url": r.get("url") or "",
                        "score": round(score, 2),
                        "matched_tokens": sorted(list(common)),
                    }
                )

        matches.sort(key=lambda m: m["score"], reverse=True)

    if json_output:
        typer.echo(_json.dumps({"matches": matches}, indent=2))
        return

    if not matches:
        _console.print("[dim]No matching documents or stubs found.[/dim]")
        return

    table = Table("score", "id", "kind", "title")
    for m in matches:
        table.add_row(f"{m['score']:.2f}", m["id"], m["kind"], m["title"])
    _console.print(table)


@doc_app.command("reconcile-stub")
def cmd_reconcile_stub(
    stub_id: str = typer.Argument(..., help="Existing placeholder stub id."),
    to_doc_id: str = typer.Option(..., "--to", help="Target ingested document id (e.g. raw-0009)."),
    kb: Path = _KB_OPT,
    json_output: bool = _JSON_OPT,
) -> None:
    """Reconcile a placeholder stub by redirecting all incoming CITES edges to target document."""
    cfg = KBConfig.load(kb)
    db_path = kb / cfg.paths.graph_db
    if not db_path.exists():
        _fail("Graph database not found", json_output)

    from ..graph.connection import open_graph
    from ..graph.upsert import upsert_edge

    with open_graph(db_path) as g:
        stub_rows = g.execute("MATCH (s:Document {id: $id}) RETURN s.id AS id", {"id": stub_id})
        if not stub_rows:
            _fail(f"Stub document {stub_id!r} not found in graph", json_output)
        tgt_rows = g.execute("MATCH (t:Document {id: $id}) RETURN t.id AS id", {"id": to_doc_id})
        if not tgt_rows:
            _fail(f"Target document {to_doc_id!r} not found in graph", json_output)

        citing_rows = g.execute(
            "MATCH (d:Document)-[:CITES]->(s:Document {id: $id}) RETURN d.id AS citing_id",
            {"id": stub_id},
        )
        redirected = []
        for c in citing_rows:
            cid = c["citing_id"]
            upsert_edge(
                g,
                "CITES",
                "Document",
                cid,
                "Document",
                to_doc_id,
                {"origin": "raw", "sources": [cid], "confidence": 1.0},
            )
            redirected.append(cid)

        g.execute("MATCH (s:Document {id: $id}) DETACH DELETE s", {"id": stub_id})

    res = {
        "reconciled_stub": stub_id,
        "target_doc": to_doc_id,
        "redirected_citations_count": len(redirected),
        "citing_docs": redirected,
    }
    if json_output:
        typer.echo(_json.dumps(res, indent=2))
    else:
        _console.print(
            f"[green]reconciled:[/green] {stub_id} → {to_doc_id} ({len(redirected)} citations redirected)"
        )


@doc_app.command("clean")
def cmd_clean(
    apply: bool = typer.Option(
        False, "--apply", help="Apply cleanups to the graph database (default is dry-run)."
    ),
    kb: Path = _KB_OPT,
    json_output: bool = _JSON_OPT,
) -> None:
    """Audit and canonicalize document URLs, titles, and merge near-duplicate stubs."""
    cfg = KBConfig.load(kb)
    db_path = kb / cfg.paths.graph_db
    if not db_path.exists():
        _fail("Graph database not found", json_output)

    import re
    from ..graph.connection import open_graph
    from ..graph.upsert import upsert_edge

    url_changes = []
    title_changes = []
    merged_stubs = []
    orphaned_stubs = []

    with open_graph(db_path) as g:
        docs = g.execute(
            "MATCH (d:Document) RETURN d.id AS id, d.name AS name, d.kind AS kind, "
            "d.url AS url, d.year AS year, d.summary AS summary, d.sources AS sources"
        )

        for d in docs:
            did = d["id"]
            orig_url = d.get("url") or ""
            canon_url = _canonicalize_url(orig_url)
            if orig_url and canon_url and orig_url != canon_url:
                url_changes.append({"id": did, "old_url": orig_url, "new_url": canon_url})
                if apply:
                    g.execute(
                        "MATCH (d:Document {id: $id}) SET d.url = $url",
                        {"id": did, "url": canon_url},
                    )

            orig_title = d.get("name") or ""
            cleaned_title = _clean_title(orig_title)
            if orig_title and cleaned_title and orig_title != cleaned_title:
                title_changes.append(
                    {"id": did, "old_title": orig_title, "new_title": cleaned_title}
                )
                if apply:
                    g.execute(
                        "MATCH (d:Document {id: $id}) SET d.name = $title",
                        {"id": did, "title": cleaned_title},
                    )

        stubs = [d for d in docs if d.get("kind") in ("stub", "placeholder")]
        visited = set()

        for i, s1 in enumerate(stubs):
            sid1 = s1["id"]
            if sid1 in visited:
                continue
            t1_tokens = set(re.findall(r"\w+", (s1.get("name") or "").lower()))
            url1 = _canonicalize_url(s1.get("url") or "")

            duplicates = []
            for j in range(i + 1, len(stubs)):
                s2 = stubs[j]
                sid2 = s2["id"]
                if sid2 in visited:
                    continue
                url2 = _canonicalize_url(s2.get("url") or "")

                is_match = False
                if url1 and url2 and url1 == url2:
                    is_match = True
                elif t1_tokens:
                    t2_tokens = set(re.findall(r"\w+", (s2.get("name") or "").lower()))
                    if t2_tokens:
                        score = len(t1_tokens.intersection(t2_tokens)) / max(
                            len(t1_tokens), len(t2_tokens)
                        )
                        if score >= 0.85:
                            is_match = True

                if is_match:
                    duplicates.append(s2)
                    visited.add(sid2)

            if duplicates:
                visited.add(sid1)
                for dup in duplicates:
                    dupid = dup["id"]
                    citing_rows = g.execute(
                        "MATCH (d:Document)-[:CITES]->(s:Document {id: $id}) RETURN d.id AS citing_id",
                        {"id": dupid},
                    )
                    citing_ids = [c["citing_id"] for c in citing_rows]
                    merged_stubs.append(
                        {
                            "canonical_stub": sid1,
                            "merged_stub": dupid,
                            "title": dup.get("name"),
                            "redirected_cites": citing_ids,
                        }
                    )
                    if apply:
                        for cid in citing_ids:
                            upsert_edge(
                                g,
                                "CITES",
                                "Document",
                                cid,
                                "Document",
                                sid1,
                                {"origin": "raw", "sources": [cid], "confidence": 1.0},
                            )
                        combined_sources = list(
                            set(
                                (s1.get("sources") or [])
                                + (dup.get("sources") or [])
                                + citing_ids
                            )
                        )
                        g.execute(
                            "MATCH (d:Document {id: $id}) SET d.sources = $sources",
                            {"id": sid1, "sources": combined_sources},
                        )
                        g.execute(
                            "MATCH (s:Document {id: $id}) DETACH DELETE s", {"id": dupid}
                        )

        for s in stubs:
            sid = s["id"]
            if sid in visited:
                continue
            c_res = g.execute(
                "MATCH ()-[r:CITES]->(s:Document {id: $id}) RETURN count(r) AS c",
                {"id": sid},
            )
            if c_res[0]["c"] == 0:
                orphaned_stubs.append({"id": sid, "title": s.get("name")})
                if apply:
                    g.execute("MATCH (s:Document {id: $id}) DETACH DELETE s", {"id": sid})

    report = {
        "applied": apply,
        "url_canonicalizations": url_changes,
        "title_cleanups": title_changes,
        "merged_stubs": merged_stubs,
        "orphaned_stubs_removed": orphaned_stubs,
        "total_actions": len(url_changes)
        + len(title_changes)
        + len(merged_stubs)
        + len(orphaned_stubs),
    }

    if json_output:
        typer.echo(_json.dumps(report, indent=2))
        return

    mode_str = (
        "[green]Applied[/green]"
        if apply
        else "[yellow]Dry Run (use --apply to execute)[/yellow]"
    )
    _console.print(
        f"Document & Citation Cleanup ({mode_str}, {report['total_actions']} proposed action(s)):"
    )

    if url_changes:
        _console.print(f"\n[bold]URL Canonicalizations[/bold] ({len(url_changes)}):")
        for u in url_changes:
            _console.print(f"  • {u['id']}: {u['old_url']} → {u['new_url']}")

    if title_changes:
        _console.print(f"\n[bold]Title Cleanups[/bold] ({len(title_changes)}):")
        for t in title_changes:
            _console.print(f"  • {t['id']}: {t['old_title']} → {t['new_title']}")

    if merged_stubs:
        _console.print(
            f"\n[bold]Merged Near-Duplicate Stubs[/bold] ({len(merged_stubs)}):"
        )
        for m in merged_stubs:
            _console.print(
                f"  • {m['merged_stub']} → {m['canonical_stub']} ({len(m['redirected_cites'])} citations redirected)"
            )

    if orphaned_stubs:
        _console.print(f"\n[bold]Orphaned Stubs[/bold] ({len(orphaned_stubs)}):")
        for o in orphaned_stubs:
            _console.print(f"  • {o['id']} ({o['title']})")

    if not report["total_actions"]:
        _console.print(
            "[green]Everything is clean:[/green] 0 URL or title adjustments needed."
        )
