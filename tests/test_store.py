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
