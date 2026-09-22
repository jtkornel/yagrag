"""`kb doc` command group: add | list | show | remove | text.

Documents are ingested into the KB's document store with strict raw vs
synthesized separation and mandatory provenance for synthesized documents.
"""

from __future__ import annotations

import contextlib
import json as _json
from pathlib import Path
from typing import Any, cast

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
    title: str | None = typer.Option(None, "--title"),
    source: list[str] = typer.Option(
        [], "--source", help="Source document id (repeatable; synthesized only)."
    ),
    tag: list[str] = typer.Option([], "--tag", help="Tag (repeatable)."),
    notes: str = typer.Option("", "--notes"),
    url: str = typer.Option("", "--url", help="Origin URL of the document."),
    doi: str = typer.Option("", "--doi", help="Digital Object Identifier (DOI) for raw documents."),
    allow_no_doi: bool = typer.Option(
        False,
        "--no-doi",
        "--allow-no-doi",
        help="Allow ingesting a raw document without a DOI (overrides require_doi).",
    ),
    kb: Path = _KB_OPT,
    json_output: bool = _JSON_OPT,
) -> None:
    """Ingest a document into the store (immutable copy for raw)."""
    if kind not in ("raw", "synthesized"):
        _fail(f"invalid --kind {kind!r}; expected raw or synthesized", json_output)

    # Auto-extract DOI from url or doi argument if not already a clean DOI
    resolved_doi = doi.strip()
    import re

    if resolved_doi:
        # If user passed a DOI URL or arXiv ID/URL into --doi, normalize it
        m_doi = re.search(
            r"(?:doi\.org/|doi:)(10\.\d{4,9}/[-._;()/:A-Za-z0-9]+)", resolved_doi, re.IGNORECASE
        )
        if m_doi:
            resolved_doi = m_doi.group(1)
        else:
            m_arx = re.search(
                r"(?:arxiv\.org/(?:abs|pdf)/|arxiv:)(\d{4}\.\d{4,5}(?:v\d+)?)",
                resolved_doi,
                re.IGNORECASE,
            )
            if m_arx:
                resolved_doi = f"10.48550/arXiv.{m_arx.group(1)}"

    if not resolved_doi and url:
        m_doi = re.search(
            r"(?:doi\.org/|doi:)(10\.\d{4,9}/[-._;()/:A-Za-z0-9]+)", url, re.IGNORECASE
        )
        if m_doi:
            resolved_doi = m_doi.group(1)
        else:
            m_arxiv = re.search(
                r"(?:arxiv\.org/(?:abs|pdf)/|arxiv:)(\d{4}\.\d{4,5}(?:v\d+)?)",
                url,
                re.IGNORECASE,
            )
            if m_arxiv:
                resolved_doi = f"10.48550/arXiv.{m_arxiv.group(1)}"

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
            doi=resolved_doi,
            allow_no_doi=allow_no_doi,
        )
    except StoreError as exc:
        _fail(str(exc), json_output)
        return

    # Automatically upsert Document node in graph and reconcile matching stubs
    cfg = KBConfig.load(kb)
    db_path = kb / cfg.paths.graph_db
    reconciled_stub_info = None
    if db_path.exists():
        with contextlib.suppress(Exception):
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
                if rec.doi:
                    props["doi"] = rec.doi
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
    kind: str | None = typer.Option(None, "--kind", help="Filter: raw | synthesized."),
    missing: bool = typer.Option(False, "--missing", help="Filter by documents whose file is missing on disk."),
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
    if missing:
        records = [r for r in records if not (kb / r.path).is_file()]
    if json_output:
        typer.echo(_json.dumps([r.to_dict() for r in records], indent=2))
        return
    table = Table("id", "kind", "format", "title", "status", "path")
    for r in records:
        on_disk = (kb / r.path).is_file()
        status = "[green]present[/green]" if on_disk else "[red]missing[/red]"
        table.add_row(r.id, r.kind, r.format, r.title, status, r.path)
    _console.print(table)


@doc_app.command("fetch")
def cmd_fetch(
    doc_id: str | None = typer.Argument(
        None,
        help="Specific document ID to fetch (e.g. raw-0001). If omitted, fetches all missing documents.",
    ),
    all_docs: bool = typer.Option(
        False,
        "--all",
        "-a",
        help="Fetch all documents in the manifest with URLs, including existing ones (overwrites).",
    ),
    kb: Path = _KB_OPT,
    json_output: bool = _JSON_OPT,
) -> None:
    """Download raw document files from URLs recorded in manifest.json and verify SHA-256."""
    import hashlib
    import urllib.request

    from ..store.documents import content_hash

    store = _open_store(kb, json_output)
    records = store.records()

    if doc_id:
        targets = [r for r in records if r.id == doc_id]
        if not targets:
            _fail(f"document {doc_id!r} not found in manifest", json_output)
            return
    else:
        targets = records

    fetched = []
    skipped = []
    failed = []

    for rec in targets:
        dest = kb / rec.path
        if dest.is_file() and not all_docs and not doc_id and content_hash(dest) == rec.hash:
            skipped.append({"id": rec.id, "reason": "already present and hash matches"})
            continue

        target_url = rec.url
        if not target_url and rec.doi:
            if rec.doi.startswith("10.48550/arXiv."):
                arxiv_id = rec.doi.removeprefix("10.48550/arXiv.")
                target_url = f"https://arxiv.org/abs/{arxiv_id}"
            else:
                target_url = f"https://doi.org/{rec.doi}"

        if not target_url:
            skipped.append({"id": rec.id, "reason": "no URL or DOI specified in manifest"})
            continue

        # If it's an arXiv abstract URL, translate to PDF URL for download
        download_url = target_url
        if "arxiv.org/abs/" in target_url:
            download_url = target_url.replace("arxiv.org/abs/", "arxiv.org/pdf/") + ".pdf"
        elif target_url.startswith("https://doi.org/10.48550/arXiv."):
            arxiv_id = target_url.removeprefix("https://doi.org/10.48550/arXiv.")
            download_url = f"https://arxiv.org/pdf/{arxiv_id}.pdf"

        try:
            dest.parent.mkdir(parents=True, exist_ok=True)
            req = urllib.request.Request(
                download_url,
                headers={"User-Agent": "Mozilla/5.0 (yagrag-kb-fetch/0.1.0)"},
            )
            with urllib.request.urlopen(req, timeout=30) as resp:
                data = resp.read()

            # Verify checksum if manifest records a non-empty hash
            computed_hash = hashlib.sha256(data).hexdigest()
            hash_matches = (computed_hash == rec.hash) if rec.hash else True

            dest.write_bytes(data)
            if rec.kind == "raw":
                dest.chmod(dest.stat().st_mode & 0o555)

            status_entry = {
                "id": rec.id,
                "path": rec.path,
                "url": download_url,
                "bytes": len(data),
                "hash_matches": hash_matches,
            }
            if not hash_matches:
                status_entry["warning"] = f"hash mismatch: expected {rec.hash[:12]}..., got {computed_hash[:12]}..."

            fetched.append(status_entry)
        except Exception as exc:  # noqa: BLE001
            failed.append({"id": rec.id, "url": download_url, "error": str(exc)})

    result = {
        "ok": len(failed) == 0,
        "fetched": fetched,
        "skipped": skipped,
        "failed": failed,
    }

    if json_output:
        typer.echo(_json.dumps(result, indent=2))
    else:
        if fetched:
            _console.print(f"[green]fetched {len(fetched)} document(s):[/green]")
            for item in fetched:
                warn = f" [yellow]({item['warning']})[/yellow]" if "warning" in item else ""
                _console.print(f"  + {item['id']}: {item['path']} ({item['bytes']} bytes){warn}")
        if skipped:
            _console.print(f"[dim]skipped {len(skipped)} document(s):[/dim]")
            for item in skipped:
                _console.print(f"  · {item['id']}: {item['reason']}")
        if failed:
            _console.print(f"[red]failed to fetch {len(failed)} document(s):[/red]")
            for item in failed:
                _console.print(f"  x {item['id']} ({item['url']}): {item['error']}")
        if not fetched and not skipped and not failed:
            _console.print("[yellow]no documents to fetch[/yellow]")


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
    year: int | None = typer.Option(None, "--year", "-y", help="Publication year of cited paper."),
    url: str | None = typer.Option(None, "--url", "-u", help="URL, DOI, or arXiv link."),
    ref: str | None = typer.Option(None, "--ref", "-r", help="Full citation string from bibliography."),
    to_doc: str | None = typer.Option(None, "--to-doc", help="Explicit existing target document id."),
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
                set(
                    [c["citing_id"] for c in citing_rows]
                    + (r.get("sources") or [])
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
                        "matched_tokens": sorted(common),
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
    cache: bool = typer.Option(
        False, "--cache", help="Clean all cached extracted documents and figures."
    ),
    kb: Path = _KB_OPT,
    json_output: bool = _JSON_OPT,
) -> None:
    """Audit and canonicalize document URLs, titles, and merge near-duplicate stubs (or clean extraction cache)."""
    if cache:
        store = _open_store(kb, json_output)
        purged = store.cache.clean_all()
        res = {"cache_purged": True, "count": purged}
        if json_output:
            typer.echo(_json.dumps(res, indent=2))
        else:
            _console.print(f"[green]Purged extraction cache for {purged} document(s).[/green]")
        return

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


@doc_app.command("figures")
def cmd_figures(
    doc_id: str = typer.Argument(..., help="Document id (e.g. raw-0001)."),
    kb: Path = _KB_OPT,
    json_output: bool = _JSON_OPT,
) -> None:
    """List extracted figures and diagram PNGs with metadata for a document."""
    store = _open_store(kb, json_output)
    try:
        rec = store.get(doc_id)
    except StoreError as exc:
        _fail(str(exc), json_output)
        return

    # If document hasn't been parsed yet, parse it first to populate cache
    if not store.cache.is_valid(doc_id, rec.hash):
        try:
            store.parse_document(doc_id)
        except StoreError as exc:
            _fail(str(exc), json_output)
            return

    figures = store.cache.list_figures(doc_id)
    fig_dicts = [
        {
            "id": fig.id,
            "page": fig.page,
            "bbox": fig.bbox,
            "caption": fig.caption,
            "image_path": fig.image_path,
        }
        for fig in figures
    ]

    if json_output:
        typer.echo(_json.dumps({"id": doc_id, "figures": fig_dicts}, indent=2))
        return

    if not figures:
        _console.print(f"[dim]No figures found for {doc_id}.[/dim]")
        return

    table = Table("Figure ID", "Page", "Caption", "Image Path")
    for fig in figures:
        caption_disp = (fig.caption[:50] + "...") if len(fig.caption) > 50 else (fig.caption or "[dim]No caption[/dim]")
        table.add_row(fig.id, str(fig.page), caption_disp, fig.image_path)
    _console.print(table)


def _ensure_docling_document(store: DocumentStore, doc_id: str, json_output: bool) -> tuple[Any, Any]:
    """Helper to ensure document is parsed and return (rec, docling_doc)."""
    try:
        rec = store.get(doc_id)
    except StoreError as exc:
        _fail(str(exc), json_output)
        raise AssertionError

    if not store.cache.is_valid(doc_id, rec.hash):
        try:
            store.parse_document(doc_id)
        except StoreError as exc:
            _fail(str(exc), json_output)
            raise AssertionError

    doc = store.cache.read_docling_document(doc_id)
    if doc is None:
        _fail(f"no cached Docling AST found for document {doc_id!r}", json_output)
        raise AssertionError

    return rec, doc


@doc_app.command("tables")
def cmd_tables(
    doc_id: str = typer.Argument(..., help="Document id (e.g. raw-0001)."),
    index: int | None = typer.Option(None, "--index", "-i", help="Zero-based index of table to inspect."),
    format_: str = typer.Option("md", "--format", help="Output format when inspecting: md | html | json."),
    kb: Path = _KB_OPT,
    json_output: bool = _JSON_OPT,
) -> None:
    """Discover or inspect structured tables in a document."""
    store = _open_store(kb, json_output)
    _, doc = _ensure_docling_document(store, doc_id, json_output)

    tables = getattr(doc, "tables", [])

    if index is not None:
        if index < 0 or index >= len(tables):
            _fail(f"table index {index} out of range (document has {len(tables)} tables)", json_output)
            return
        tbl = tables[index]
        p_no = 1
        bbox_coords = []
        if hasattr(tbl, "prov") and tbl.prov:
            p_no = getattr(tbl.prov[0], "page_no", 1)
            bb = getattr(tbl.prov[0], "bbox", None)
            if bb is not None:
                bbox_coords = [getattr(bb, "l", 0.0), getattr(bb, "t", 0.0), getattr(bb, "r", 0.0), getattr(bb, "b", 0.0)]
        caption = getattr(tbl, "caption_text", lambda d: "")(doc) or ""
        cref = getattr(tbl, "self_ref", None) or (tbl.get_ref().cref if hasattr(tbl, "get_ref") else f"#/tables/{index}")

        # Render format
        md_content = tbl.export_to_markdown(doc) if hasattr(tbl, "export_to_markdown") else ""
        html_content = tbl.export_to_html(doc) if hasattr(tbl, "export_to_html") else ""
        grid_data = [[cell.text for cell in row] for row in tbl.data.grid] if hasattr(tbl, "data") and hasattr(tbl.data, "grid") else []

        if json_output or format_ == "json":
            out_data = {
                "id": doc_id,
                "index": index,
                "cref": cref,
                "page": p_no,
                "bbox": bbox_coords,
                "caption": caption,
                "rows": getattr(tbl.data, "num_rows", len(grid_data)),
                "cols": getattr(tbl.data, "num_cols", len(grid_data[0]) if grid_data else 0),
                "grid": grid_data,
                "markdown": md_content,
                "html": html_content,
            }
            typer.echo(_json.dumps(out_data, indent=2))
            return

        if format_ == "html":
            typer.echo(html_content)
        else:
            typer.echo(md_content)
        return

    # List overview of tables
    table_summaries = []
    for idx, tbl in enumerate(tables):
        p_no = 1
        bbox_coords = []
        if hasattr(tbl, "prov") and tbl.prov:
            p_no = getattr(tbl.prov[0], "page_no", 1)
            bb = getattr(tbl.prov[0], "bbox", None)
            if bb is not None:
                bbox_coords = [getattr(bb, "l", 0.0), getattr(bb, "t", 0.0), getattr(bb, "r", 0.0), getattr(bb, "b", 0.0)]
        caption = getattr(tbl, "caption_text", lambda d: "")(doc) or ""
        cref = getattr(tbl, "self_ref", None) or (tbl.get_ref().cref if hasattr(tbl, "get_ref") else f"#/tables/{idx}")
        rows = getattr(tbl.data, "num_rows", 0)
        cols = getattr(tbl.data, "num_cols", 0)
        table_summaries.append({
            "index": idx,
            "cref": cref,
            "page": p_no,
            "bbox": bbox_coords,
            "rows": rows,
            "cols": cols,
            "caption": caption,
        })

    if json_output:
        typer.echo(_json.dumps({"id": doc_id, "tables": table_summaries}, indent=2))
        return

    if not table_summaries:
        _console.print(f"[dim]No tables found for {doc_id}.[/dim]")
        return

    table_view = Table("Index", "Pointer", "Page", "Size", "Caption")
    for s in table_summaries:
        cap = (s["caption"][:50] + "...") if len(s["caption"]) > 50 else (s["caption"] or "[dim]No caption[/dim]")
        table_view.add_row(str(s["index"]), s["cref"], str(s["page"]), f"{s['rows']}x{s['cols']}", cap)
    _console.print(table_view)


@doc_app.command("equations")
def cmd_equations(
    doc_id: str = typer.Argument(..., help="Document id (e.g. raw-0001)."),
    index: int | None = typer.Option(None, "--index", "-i", help="Zero-based index of equation to view."),
    page: int | None = typer.Option(None, "--page", "-p", help="Filter equations by page number."),
    kb: Path = _KB_OPT,
    json_output: bool = _JSON_OPT,
) -> None:
    """Discover or extract isolated mathematical formulas from a document."""
    store = _open_store(kb, json_output)
    _, doc = _ensure_docling_document(store, doc_id, json_output)

    # Collect formulas from iterate_items
    formulas = []
    for item, _ in doc.iterate_items():
        if type(item).__name__ == "FormulaItem":
            p_no = 1
            bbox_coords = []
            if hasattr(item, "prov") and item.prov:
                p_no = getattr(item.prov[0], "page_no", 1)
                bb = getattr(item.prov[0], "bbox", None)
                if bb is not None:
                    bbox_coords = [getattr(bb, "l", 0.0), getattr(bb, "t", 0.0), getattr(bb, "r", 0.0), getattr(bb, "b", 0.0)]
            cref = getattr(item, "self_ref", None) or (item.get_ref().cref if hasattr(item, "get_ref") else "")
            formulas.append({
                "item": item,
                "cref": cref,
                "page": p_no,
                "bbox": bbox_coords,
                "text": getattr(item, "text", ""),
            })

    if page is not None:
        formulas = [f for f in formulas if f["page"] == page]

    for idx, f in enumerate(formulas):
        f["index"] = idx

    if index is not None:
        if index < 0 or index >= len(formulas):
            _fail(f"equation index {index} out of range ({len(formulas)} equation(s) available)", json_output)
            return
        f = formulas[index]
        if json_output:
            typer.echo(_json.dumps({
                "id": doc_id,
                "index": index,
                "cref": f["cref"],
                "page": f["page"],
                "bbox": f["bbox"],
                "latex": f["text"],
            }, indent=2))
            return
        typer.echo(f["text"])
        return

    eq_summaries = [
        {
            "index": f["index"],
            "cref": f["cref"],
            "page": f["page"],
            "bbox": f["bbox"],
            "latex": f["text"],
        }
        for f in formulas
    ]

    if json_output:
        typer.echo(_json.dumps({"id": doc_id, "equations": eq_summaries}, indent=2))
        return

    if not eq_summaries:
        _console.print(f"[dim]No equations found for {doc_id}.[/dim]")
        return

    table_view = Table("Index", "Pointer", "Page", "LaTeX Formula")
    for eq in eq_summaries:
        text_disp = (eq["latex"][:60] + "...") if len(eq["latex"]) > 60 else eq["latex"]
        table_view.add_row(str(eq["index"]), eq["cref"], str(eq["page"]), text_disp)
    _console.print(table_view)


@doc_app.command("outline")
def cmd_outline(
    doc_id: str = typer.Argument(..., help="Document id (e.g. raw-0001)."),
    depth: int | None = typer.Option(None, "--depth", "-d", help="Maximum section nesting depth to display."),
    items: bool = typer.Option(False, "--items", "-i", help="Include leaf items (tables, formulas, pictures) under sections."),
    kb: Path = _KB_OPT,
    json_output: bool = _JSON_OPT,
) -> None:
    """Show the hierarchical document outline with JSON Pointer (cref) annotations."""
    store = _open_store(kb, json_output)
    _, doc = _ensure_docling_document(store, doc_id, json_output)

    # Detect if TitleItem exists to offset SectionHeaderItem levels cleanly
    has_title = any(type(item).__name__ == "TitleItem" for item, _ in doc.iterate_items())

    root: dict[str, Any] = {"title": "Root", "level": 0, "cref": None, "page": 1, "items": [], "children": []}
    stack: list[dict[str, Any]] = [root]

    for item, _ in doc.iterate_items():
        itype = type(item).__name__
        cref = getattr(item, "self_ref", None) or (item.get_ref().cref if hasattr(item, "get_ref") else None)
        p_no = 1
        if hasattr(item, "prov") and item.prov:
            p_no = getattr(item.prov[0], "page_no", 1)

        if itype in ("TitleItem", "SectionHeaderItem"):
            if itype == "TitleItem":
                lvl = 1
            else:
                base_lvl = getattr(item, "level", 1)
                lvl = base_lvl + 1 if has_title else base_lvl

            node = {
                "title": getattr(item, "text", ""),
                "level": lvl,
                "cref": cref,
                "page": p_no,
                "items": [],
                "children": [],
            }
            while len(stack) > 1 and stack[-1]["level"] >= node["level"]:
                stack.pop()
            stack[-1]["children"].append(node)
            stack.append(node)

        elif itype in ("TableItem", "FormulaItem", "PictureItem"):
            label = "table" if itype == "TableItem" else ("formula" if itype == "FormulaItem" else "picture")
            summary = ""
            if itype == "FormulaItem":
                summary = getattr(item, "text", "")
            elif itype == "TableItem":
                summary = getattr(item, "caption_text", lambda d: "")(doc) or (
                    f"{item.data.num_rows}x{item.data.num_cols} table" if hasattr(item, "data") else "Table"
                )
            elif itype == "PictureItem":
                summary = getattr(item, "caption_text", lambda d: "")(doc) or "Picture"

            stack[-1]["items"].append({
                "label": label,
                "cref": cref,
                "page": p_no,
                "summary": summary,
            })

    def _filter_depth(nodes: list[dict[str, Any]], current_depth: int) -> list[dict[str, Any]]:
        filtered = []
        for n in nodes:
            entry: dict[str, Any] = {
                "title": n["title"],
                "level": n["level"],
                "cref": n["cref"],
                "page": n["page"],
            }
            if items:
                entry["items"] = n["items"]
            if depth is None or current_depth < depth:
                entry["children"] = _filter_depth(n["children"], current_depth + 1)
            else:
                entry["children"] = []
            filtered.append(entry)
        return filtered

    tree = _filter_depth(root["children"], 1)

    if json_output:
        typer.echo(_json.dumps({"id": doc_id, "outline": tree}, indent=2))
        return

    if not tree:
        _console.print(f"[dim]No section headers found in {doc_id}.[/dim]")
        return

    def _render_text(nodes: list[dict[str, Any]], indent_level: int = 0) -> None:
        prefix = "  " * indent_level
        for n in nodes:
            _console.print(f"{prefix}[bold]{n['title']}[/bold] [dim]\\[p.{n['page']}][/dim] [cyan]({n['cref']})[/cyan]")
            if items and n.get("items"):
                for itm in n["items"]:
                    _console.print(f"{prefix}  [yellow]• {itm['label']}:[/yellow] {itm['summary']} [cyan]({itm['cref']})[/cyan]")
            if n.get("children"):
                _render_text(n["children"], indent_level + 1)

    _render_text(tree)


@doc_app.command("item")
def cmd_item(
    doc_id: str = typer.Argument(..., help="Document id (e.g. raw-0001)."),
    cref: str = typer.Argument(..., help="RFC 6901 JSON pointer (e.g. #/tables/0, #/texts/4)."),
    format_: str = typer.Option("text", "--format", help="Rendering format: text | md | html | json."),
    kb: Path = _KB_OPT,
    json_output: bool = _JSON_OPT,
) -> None:
    """Fetch and inspect a specific AST item by its JSON Pointer (cref)."""
    from docling_core.types.doc.common.reference import RefItem

    store = _open_store(kb, json_output)
    _, doc = _ensure_docling_document(store, doc_id, json_output)

    normalized_cref = cref.strip()
    if not normalized_cref.startswith("#/"):
        normalized_cref = "#/" + normalized_cref.lstrip("#/")

    ref = RefItem(cref=normalized_cref)
    try:
        resolved = ref.resolve(doc)
    except (AttributeError, IndexError, KeyError, TypeError, ValueError) as exc:
        _fail(f"could not resolve pointer {cref!r} in document {doc_id!r}: {exc}", json_output)
        return

    itype = type(resolved).__name__
    p_no = 1
    bbox_coords = []
    if hasattr(resolved, "prov") and resolved.prov:
        p_no = getattr(resolved.prov[0], "page_no", 1)
        bb = getattr(resolved.prov[0], "bbox", None)
        if bb is not None:
            bbox_coords = [getattr(bb, "l", 0.0), getattr(bb, "t", 0.0), getattr(bb, "r", 0.0), getattr(bb, "b", 0.0)]

    if json_output or format_ == "json":
        data = resolved.model_dump() if hasattr(resolved, "model_dump") else str(resolved)
        typer.echo(_json.dumps({
            "id": doc_id,
            "cref": normalized_cref,
            "type": itype,
            "page": p_no,
            "bbox": bbox_coords,
            "data": data,
        }, indent=2))
        return

    if itype == "TableItem":
        if format_ == "html" and hasattr(resolved, "export_to_html"):
            typer.echo(resolved.export_to_html(doc))
        elif hasattr(resolved, "export_to_markdown"):
            typer.echo(resolved.export_to_markdown(doc))
        else:
            typer.echo(str(resolved))
    elif itype == "FormulaItem":
        typer.echo(getattr(resolved, "text", ""))
    elif itype == "PictureItem":
        cap = getattr(resolved, "caption_text", lambda d: "")(doc) or ""
        _console.print(f"[bold]Picture Item[/bold] ({normalized_cref}) [dim]\\[Page {p_no}][/dim]")
        if cap:
            _console.print(f"Caption: {cap}")
        if bbox_coords:
            _console.print(f"BBox: {bbox_coords}")
    else:
        text = getattr(resolved, "text", "")
        typer.echo(text or str(resolved))

