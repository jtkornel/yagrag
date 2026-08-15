"""Tests for index build and hybrid retrieval (Step 4).

Uses the deterministic `hash` embedder backend so tests are offline and
reproducible without any ML model.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from typer.testing import CliRunner

from kb.cli.main import app
from kb.index.embedder import HashEmbedder
from kb.index.indexer import chunk_text

pytest.importorskip("grafeo")

runner = CliRunner()


MIGRATION = {
    "id": "0001_init",
    "operations": [
        {"op": "create_node_table", "table": {"name": "Concept"}},
        {
            "op": "create_node_table",
            "table": {
                "name": "Claim",
                "properties": [
                    {"name": "predicate", "type": "STRING"},
                    {"name": "object_literal", "type": "STRING"},
                ],
            },
        },
        {
            "op": "create_rel_table",
            "table": {"name": "ABOUT", "pairs": [{"from": "Claim", "to": "Concept"}]},
        },
    ],
}


def _use_hash_backend(kb: Path) -> None:
    config = (kb / "kb.toml").read_text()
    config = config.replace('backend = "local"', 'backend = "hash"')
    (kb / "kb.toml").write_text(config)


@pytest.fixture()
def kb_dir(tmp_path: Path) -> Path:
    kb = tmp_path / "kb"
    assert runner.invoke(app, ["init", str(kb)]).exit_code == 0
    _use_hash_backend(kb)
    (kb / "schema" / "migrations" / "0001_init.json").write_text(json.dumps(MIGRATION))
    assert runner.invoke(app, ["schema", "apply", "--kb", str(kb)]).exit_code == 0
    return kb


@pytest.fixture()
def populated_kb(kb_dir: Path, tmp_path: Path) -> Path:
    doc = tmp_path / "factor_graphs.md"
    doc.write_text(
        "# Factor graphs\n\n"
        "A factor graph is a bipartite graph connecting variables and factors.\n\n"
        "Factor graphs are widely used for SLAM and sensor fusion on UGVs.\n"
    )
    assert runner.invoke(
        app, ["doc", "add", str(doc), "--kind", "raw", "--kb", str(kb_dir), "--json"]
    ).exit_code == 0
    assert runner.invoke(
        app,
        ["graph", "upsert-node", "Concept", "--props",
         json.dumps({"id": "c1", "name": "factor graph",
                     "summary": "bipartite probabilistic graphical model",
                     "origin": "raw", "sources": ["raw-0001"]}),
         "--kb", str(kb_dir), "--json"],
    ).exit_code == 0
    assert runner.invoke(
        app,
        ["graph", "upsert-claim", "cl1",
         "--subject", "Concept:c1", "--predicate", "used_for",
         "--object-literal", "SLAM",
         "--props", json.dumps({"origin": "raw", "sources": ["raw-0001"],
                                "confidence": 0.9}),
         "--kb", str(kb_dir), "--json"],
    ).exit_code == 0
    return kb_dir


# --- unit ------------------------------------------------------------------------


def test_chunk_text_paragraph_split() -> None:
    text = "para one\n\npara two\n\npara three"
    assert chunk_text(text, max_chars=1000) == ["para one\n\npara two\n\npara three"]
    assert len(chunk_text(text, max_chars=10)) == 3


def test_hash_embedder_deterministic_and_normalized() -> None:
    e = HashEmbedder(dim=64)
    v1, v2 = e.embed(["factor graph", "factor graph"])
    assert v1 == v2
    assert abs(sum(x * x for x in v1) - 1.0) < 1e-6
    assert e.embed(["something else"])[0] != v1


# --- index build -------------------------------------------------------------------


def test_index_build_counts(populated_kb: Path) -> None:
    result = runner.invoke(app, ["index", "build", "--kb", str(populated_kb), "--json"])
    assert result.exit_code == 0, result.output
    stats = json.loads(result.output)
    assert stats["documents"] == 1
    assert stats["entities"] == 1  # Concept c1 (Claim excluded)
    assert stats["chunks"] >= 2
    assert stats["backend"] == "hash"


def test_index_rebuild_idempotent(populated_kb: Path) -> None:
    for _ in range(2):
        result = runner.invoke(
            app, ["index", "build", "--kb", str(populated_kb), "--json"]
        )
        assert result.exit_code == 0, result.output


# --- search ------------------------------------------------------------------------


def test_search_empty_index_returns_valid_bundle(kb_dir: Path) -> None:
    result = runner.invoke(app, ["search", "factor graphs", "--kb", str(kb_dir), "--json"])
    assert result.exit_code == 0, result.output
    bundle = json.loads(result.output)
    assert bundle["query"] == "factor graphs"
    assert bundle["semantic"] == []
    assert bundle["fulltext"] == []
    assert bundle["entities"] == []
    assert bundle["documents"] == []


def test_search_returns_hybrid_bundle(populated_kb: Path) -> None:
    assert runner.invoke(
        app, ["index", "build", "--kb", str(populated_kb), "--json"]
    ).exit_code == 0
    result = runner.invoke(
        app, ["search", "factor graph", "--kb", str(populated_kb), "--json"]
    )
    assert result.exit_code == 0, result.output
    bundle = json.loads(result.output)

    assert set(bundle) == {"query", "semantic", "fulltext", "entities", "documents"}
    assert bundle["semantic"], "expected vector hits"
    assert bundle["fulltext"], "expected FTS hits"
    for hit in bundle["semantic"] + bundle["fulltext"]:
        assert {"chunk_id", "kind", "ref", "label", "text", "score"} <= set(hit)

    # The Concept entity should be surfaced, enriched with its claim.
    entity_ids = {e["id"] for e in bundle["entities"]}
    assert "c1" in entity_ids
    ent = next(e for e in bundle["entities"] if e["id"] == "c1")
    assert ent["claims"] and ent["claims"][0]["predicate"] == "used_for"

    # Document reference present with provenance info.
    doc_ids = {d["id"] for d in bundle["documents"]}
    assert "raw-0001" in doc_ids
