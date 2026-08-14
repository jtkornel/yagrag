"""Tests for the document store and `kb doc` CLI (Step 3)."""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest
from typer.testing import CliRunner

from kb.cli.main import app
from kb.config import KBConfig
from kb.store.documents import (
    DocumentStore,
    DuplicateDocumentError,
    StoreError,
    UnsupportedFormatError,
)

runner = CliRunner()


@pytest.fixture()
def kb_dir(tmp_path: Path) -> Path:
    result = runner.invoke(app, ["init", str(tmp_path / "kb")])
    assert result.exit_code == 0
    return tmp_path / "kb"


@pytest.fixture()
def store(kb_dir: Path) -> DocumentStore:
    return DocumentStore(kb_dir, KBConfig.load(kb_dir))


@pytest.fixture()
def sample_md(tmp_path: Path) -> Path:
    p = tmp_path / "note.md"
    p.write_text("# Factor graphs\n\nA factor graph is a bipartite graph.\n")
    return p


# --- library-level ---------------------------------------------------------------


def test_add_raw_document(store: DocumentStore, sample_md: Path) -> None:
    rec = store.add(sample_md, "raw", title="Factor graphs")
    assert rec.id == "raw-0001"
    assert rec.kind == "raw"
    assert rec.format == "md"
    stored = store.kb_root / rec.path
    assert stored.is_file()
    assert "documents/raw/" in rec.path
    # immutable: no write bits
    assert not os.access(stored, os.W_OK)
    # sidecar metadata exists
    assert stored.with_name(stored.name + ".meta.json").is_file()


def test_duplicate_ingest_detected(store: DocumentStore, sample_md: Path) -> None:
    store.add(sample_md, "raw")
    with pytest.raises(DuplicateDocumentError):
        store.add(sample_md, "raw")


def test_raw_must_not_declare_sources(store: DocumentStore, sample_md: Path) -> None:
    with pytest.raises(StoreError, match="must not declare sources"):
        store.add(sample_md, "raw", sources=["raw-0001"])


def test_synthesized_requires_sources(store: DocumentStore, sample_md: Path) -> None:
    with pytest.raises(StoreError, match="must declare at least one source"):
        store.add(sample_md, "synthesized")


def test_synthesized_rejects_unknown_sources(store: DocumentStore, sample_md: Path) -> None:
    with pytest.raises(StoreError, match="unknown source"):
        store.add(sample_md, "synthesized", sources=["raw-9999"])


def test_synthesized_with_valid_source(store: DocumentStore, sample_md: Path, tmp_path: Path) -> None:
    raw = store.add(sample_md, "raw")
    summary = tmp_path / "summary.md"
    summary.write_text("# Summary of factor graphs\n")
    rec = store.add(summary, "synthesized", sources=[raw.id])
    assert rec.id == "syn-0001"
    assert rec.sources == (raw.id,)
    assert "documents/synthesized/" in rec.path


def test_unsupported_format_rejected(store: DocumentStore, tmp_path: Path) -> None:
    bad = tmp_path / "page.html"
    bad.write_text("<html></html>")
    with pytest.raises(UnsupportedFormatError):
        store.add(bad, "raw")


def test_remove_blocked_by_dependents(store: DocumentStore, sample_md: Path, tmp_path: Path) -> None:
    raw = store.add(sample_md, "raw")
    summary = tmp_path / "summary.md"
    summary.write_text("summary\n")
    store.add(summary, "synthesized", sources=[raw.id])
    with pytest.raises(StoreError, match="derived documents depend"):
        store.remove(raw.id)


def test_remove_document(store: DocumentStore, sample_md: Path) -> None:
    rec = store.add(sample_md, "raw")
    store.remove(rec.id)
    assert store.records() == []
    assert not (store.kb_root / rec.path).exists()


def test_extract_text_md(store: DocumentStore, sample_md: Path) -> None:
    rec = store.add(sample_md, "raw")
    assert "bipartite graph" in store.extract_text(rec.id)


# --- CLI-level --------------------------------------------------------------------


def test_doc_add_list_show_cli(kb_dir: Path, sample_md: Path) -> None:
    result = runner.invoke(
        app,
        ["doc", "add", str(sample_md), "--kind", "raw", "--kb", str(kb_dir), "--json"],
    )
    assert result.exit_code == 0, result.output
    rec = json.loads(result.output)
    assert rec["id"] == "raw-0001"

    result = runner.invoke(app, ["doc", "list", "--kb", str(kb_dir), "--json"])
    assert result.exit_code == 0
    docs = json.loads(result.output)
    assert len(docs) == 1

    result = runner.invoke(app, ["doc", "show", "raw-0001", "--kb", str(kb_dir), "--json"])
    assert result.exit_code == 0
    assert json.loads(result.output)["title"] == "note"


def test_doc_add_with_url_cli(kb_dir: Path, sample_md: Path) -> None:
    url = "https://example.com/paper.pdf"
    result = runner.invoke(
        app,
        ["doc", "add", str(sample_md), "--kind", "raw", "--url", url, "--kb", str(kb_dir), "--json"],
    )
    assert result.exit_code == 0, result.output
    rec = json.loads(result.output)
    assert rec["id"] == "raw-0001"
    assert rec["url"] == url


def test_doc_add_duplicate_cli_nonzero(kb_dir: Path, sample_md: Path) -> None:
    args = ["doc", "add", str(sample_md), "--kind", "raw", "--kb", str(kb_dir), "--json"]
    assert runner.invoke(app, args).exit_code == 0
    result = runner.invoke(app, args)
    assert result.exit_code == 2
    assert "already stored" in json.loads(result.output)["error"]


def test_doc_add_invalid_kind_cli(kb_dir: Path, sample_md: Path) -> None:
    result = runner.invoke(
        app,
        ["doc", "add", str(sample_md), "--kind", "draft", "--kb", str(kb_dir), "--json"],
    )
    assert result.exit_code == 2


def test_doc_add_unsupported_format_cli(kb_dir: Path, tmp_path: Path) -> None:
    bad = tmp_path / "page.html"
    bad.write_text("<html></html>")
    result = runner.invoke(
        app, ["doc", "add", str(bad), "--kind", "raw", "--kb", str(kb_dir), "--json"]
    )
    assert result.exit_code == 2
    assert "unsupported document format" in json.loads(result.output)["error"]


def test_doc_stubs_match_and_reconcile_cli(kb_dir: Path, sample_md: Path, tmp_path: Path) -> None:
    # 1. Apply seed schema so Document and CITES tables exist
    mig = kb_dir / "schema" / "migrations" / "0001_init.json"
    mig.write_text(json.dumps({
        "id": "0001_init",
        "operations": [
            {"op": "create_node_table", "table": {"name": "Document", "properties": [
                {"name": "kind", "type": "STRING"},
                {"name": "path", "type": "STRING"},
                {"name": "format", "type": "STRING"},
                {"name": "year", "type": "INT64"},
                {"name": "url", "type": "STRING"}
            ]}},
            {"op": "create_rel_table", "table": {"name": "CITES", "pairs": [
                {"from": "Document", "to": "Document"}
            ]}}
        ]
    }), encoding="utf-8")
    assert runner.invoke(app, ["schema", "apply", "--kb", str(kb_dir)]).exit_code == 0

    # 2. Ingest a raw document
    res_add = runner.invoke(
        app,
        ["doc", "add", str(sample_md), "--kind", "raw", "--title", "Factor graphs paper", "--kb", str(kb_dir), "--json"],
    )
    assert res_add.exit_code == 0
    raw_id = json.loads(res_add.output)["id"]

    # 3. Upsert a placeholder stub for an external cited paper
    stub_id = "stub-kaess-2008-isam"
    runner.invoke(
        app,
        [
            "graph", "upsert-node", "Document",
            "--props", json.dumps({
                "id": stub_id,
                "name": "iSAM: Incremental Smoothing and Mapping",
                "kind": "stub",
                "year": 2008,
                "url": "https://doi.org/10.1109/TRO.2008.2006704",
                "origin": "raw",
                "sources": [raw_id],
                "summary": "M. Kaess et al., iSAM: Incremental Smoothing and Mapping, IEEE TRO 2008."
            }),
            "--kb", str(kb_dir), "--json"
        ],
    )

    # 4. Create CITES edge from raw document to stub
    runner.invoke(
        app,
        [
            "graph", "upsert-edge", "CITES",
            "--from", f"Document:{raw_id}",
            "--to", f"Document:{stub_id}",
            "--props", json.dumps({"origin": "raw", "sources": [raw_id]}),
            "--kb", str(kb_dir), "--json"
        ],
    )

    # 5. Test `kb doc stubs`
    res_stubs = runner.invoke(app, ["doc", "stubs", "--kb", str(kb_dir), "--json"])
    assert res_stubs.exit_code == 0, res_stubs.output
    stubs_data = json.loads(res_stubs.output)["stubs"]
    assert len(stubs_data) == 1
    assert stubs_data[0]["id"] == stub_id
    assert stubs_data[0]["cites_count"] == 1
    assert raw_id in stubs_data[0]["cited_by"]

    # 6. Test `kb doc match-stubs`
    res_match = runner.invoke(app, ["doc", "match-stubs", "incremental smoothing isam", "--kb", str(kb_dir), "--json"])
    assert res_match.exit_code == 0
    matches = json.loads(res_match.output)["matches"]
    assert len(matches) >= 1
    assert matches[0]["id"] == stub_id

    # 7. Ingest the actual paper and reconcile the stub
    paper_file = tmp_path / "isam_paper.md"
    paper_file.write_text("# iSAM\nFull text of iSAM paper.\n")
    res_add_new = runner.invoke(
        app,
        ["doc", "add", str(paper_file), "--kind", "raw", "--title", "iSAM Paper", "--kb", str(kb_dir), "--json"],
    )
    new_raw_id = json.loads(res_add_new.output)["id"]

    res_rec = runner.invoke(
        app,
        ["doc", "reconcile-stub", stub_id, "--to", new_raw_id, "--kb", str(kb_dir), "--json"],
    )
    assert res_rec.exit_code == 0, res_rec.output
    rec_payload = json.loads(res_rec.output)
    assert rec_payload["redirected_citations_count"] == 1

    # Verify stub is removed and CITES points to new_raw_id
    res_verify = runner.invoke(
        app,
        ["graph", "query", f"MATCH (d:Document {{id: '{raw_id}'}})-[:CITES]->(t:Document) RETURN t.id AS tid", "--kb", str(kb_dir), "--json"],
    )
    assert json.loads(res_verify.output)["rows"] == [{"tid": new_raw_id}]


def test_doc_cite_command_and_auto_reconcile(kb_dir: Path, sample_md: Path, tmp_path: Path) -> None:
    # Apply schema
    mig = kb_dir / "schema" / "migrations" / "0001_init.json"
    mig.write_text(json.dumps({
        "id": "0001_init",
        "operations": [
            {"op": "create_node_table", "table": {"name": "Document", "properties": [
                {"name": "kind", "type": "STRING"},
                {"name": "path", "type": "STRING"},
                {"name": "format", "type": "STRING"},
                {"name": "year", "type": "INT64"},
                {"name": "url", "type": "STRING"}
            ]}},
            {"op": "create_rel_table", "table": {"name": "CITES", "pairs": [
                {"from": "Document", "to": "Document"}
            ]}}
        ]
    }), encoding="utf-8")
    assert runner.invoke(app, ["schema", "apply", "--kb", str(kb_dir)]).exit_code == 0

    # Ingest citing raw document
    res_add1 = runner.invoke(
        app,
        ["doc", "add", str(sample_md), "--kind", "raw", "--title", "Citing Paper 1", "--kb", str(kb_dir), "--json"],
    )
    doc1_id = json.loads(res_add1.output)["id"]

    # 1. Use `kb doc cite` to cite a paper not yet in the KB (creates stub automatically)
    res_cite1 = runner.invoke(
        app,
        [
            "doc", "cite", doc1_id,
            "--title", "GTSAM Factor Graph Library Manual",
            "--year", "2020",
            "--url", "https://borg.cc.gatech.edu/gtsam",
            "--kb", str(kb_dir), "--json",
        ],
    )
    assert res_cite1.exit_code == 0, res_cite1.output
    cite1_payload = json.loads(res_cite1.output)
    assert cite1_payload["action"] == "created_stub"
    stub_id = cite1_payload["target_doc"]
    assert stub_id.startswith("stub-gtsam-factor-graph-library")

    # 2. Ingest second citing document and cite the SAME paper (matches existing stub automatically)
    paper2 = tmp_path / "paper2.md"
    paper2.write_text("# Paper 2\n")
    res_add2 = runner.invoke(
        app,
        ["doc", "add", str(paper2), "--kind", "raw", "--title", "Citing Paper 2", "--kb", str(kb_dir), "--json"],
    )
    doc2_id = json.loads(res_add2.output)["id"]

    res_cite2 = runner.invoke(
        app,
        [
            "doc", "cite", doc2_id,
            "--title", "GTSAM Factor Graph Library Manual",
            "--ref", "F. Dellaert, Factor Graphs and GTSAM, Tech Report 2021",
            "--kb", str(kb_dir), "--json",
        ],
    )
    assert res_cite2.exit_code == 0, res_cite2.output
    cite2_payload = json.loads(res_cite2.output)
    assert cite2_payload["action"] == "matched_stub"
    assert cite2_payload["target_doc"] == stub_id

    # Check `kb doc stubs` shows 2 citations and enriched summary
    res_stubs = runner.invoke(app, ["doc", "stubs", "--kb", str(kb_dir), "--json"])
    assert res_stubs.exit_code == 0
    stubs = json.loads(res_stubs.output)["stubs"]
    assert len(stubs) == 1
    assert stubs[0]["cites_count"] == 2
    assert set(stubs[0]["cited_by"]) == {doc1_id, doc2_id}
    assert "Dellaert" in stubs[0]["summary"]

    # 3. Now ingest the GTSAM manual paper -> kb doc add automatically reconciles the stub!
    gtsam_file = tmp_path / "gtsam_manual.md"
    gtsam_file.write_text("# GTSAM Manual\nFull text of GTSAM.\n")
    res_add_gtsam = runner.invoke(
        app,
        [
            "doc", "add", str(gtsam_file),
            "--kind", "raw",
            "--title", "GTSAM Factor Graph Library Manual",
            "--kb", str(kb_dir), "--json",
        ],
    )
    assert res_add_gtsam.exit_code == 0, res_add_gtsam.output
    gtsam_payload = json.loads(res_add_gtsam.output)
    new_doc_id = gtsam_payload["id"]
    assert "reconciled_stub" in gtsam_payload
    assert gtsam_payload["reconciled_stub"]["redirected_citations"] == 2

    # Check that both doc1 and doc2 now point to new_doc_id
    query_res = runner.invoke(
        app,
        ["graph", "query", "MATCH (d:Document)-[:CITES]->(t:Document) RETURN d.id AS citing, t.id AS target", "--kb", str(kb_dir), "--json"],
    )
    cites = json.loads(query_res.output)["rows"]
    assert len(cites) == 2
    assert {c["citing"] for c in cites} == {doc1_id, doc2_id}
    assert {c["target"] for c in cites} == {new_doc_id}


def test_doc_clean_command_dry_run_and_apply(kb_dir: Path, sample_md: Path) -> None:
    # 1. Apply schema
    mig = kb_dir / "schema" / "migrations" / "0001_init.json"
    mig.write_text(json.dumps({
        "id": "0001_init",
        "operations": [
            {"op": "create_node_table", "table": {"name": "Document", "properties": [
                {"name": "kind", "type": "STRING"},
                {"name": "path", "type": "STRING"},
                {"name": "format", "type": "STRING"},
                {"name": "year", "type": "INT64"},
                {"name": "url", "type": "STRING"}
            ]}},
            {"op": "create_rel_table", "table": {"name": "CITES", "pairs": [
                {"from": "Document", "to": "Document"}
            ]}}
        ]
    }), encoding="utf-8")
    assert runner.invoke(app, ["schema", "apply", "--kb", str(kb_dir)]).exit_code == 0

    # Ingest a citing document
    res_add = runner.invoke(
        app,
        ["doc", "add", str(sample_md), "--kind", "raw", "--title", "Main Paper", "--kb", str(kb_dir), "--json"],
    )
    doc_id = json.loads(res_add.output)["id"]

    # Upsert a stub with uncanonical DOI URL and bracketed title
    runner.invoke(
        app,
        [
            "graph", "upsert-node", "Document",
            "--props", json.dumps({
                "id": "stub-noisy-url",
                "name": "[12] SLIP-BASED TERRAIN ESTIMATION FOR ROBOTS",
                "kind": "stub",
                "url": "http://dx.doi.org/10.1080/00423114?utm_source=scholar#page=1",
                "origin": "raw",
                "sources": [doc_id],
            }),
            "--kb", str(kb_dir), "--json"
        ],
    )
    runner.invoke(
        app,
        [
            "graph", "upsert-edge", "CITES",
            "--from", f"Document:{doc_id}",
            "--to", "Document:stub-noisy-url",
            "--props", json.dumps({"origin": "raw", "sources": [doc_id]}),
            "--kb", str(kb_dir), "--json"
        ],
    )

    # 2. Test `kb doc clean` dry-run
    res_dry = runner.invoke(app, ["doc", "clean", "--kb", str(kb_dir), "--json"])
    assert res_dry.exit_code == 0, res_dry.output
    dry_data = json.loads(res_dry.output)
    assert dry_data["applied"] is False
    assert len(dry_data["url_canonicalizations"]) == 1
    assert dry_data["url_canonicalizations"][0]["new_url"] == "https://doi.org/10.1080/00423114"
    assert len(dry_data["title_cleanups"]) == 1
    assert dry_data["title_cleanups"][0]["new_title"] == "Slip-Based Terrain Estimation For Robots"

    # 3. Test `kb doc clean --apply`
    res_apply = runner.invoke(app, ["doc", "clean", "--apply", "--kb", str(kb_dir), "--json"])
    assert res_apply.exit_code == 0, res_apply.output
    apply_data = json.loads(res_apply.output)
    assert apply_data["applied"] is True

    # 4. Verify in graph
    res_q = runner.invoke(
        app,
        ["graph", "query", "MATCH (d:Document {id: 'stub-noisy-url'}) RETURN d.name AS name, d.url AS url", "--kb", str(kb_dir), "--json"],
    )
    row = json.loads(res_q.output)["rows"][0]
    assert row["url"] == "https://doi.org/10.1080/00423114"
    assert row["name"] == "Slip-Based Terrain Estimation For Robots"
