"""Tests for `kb graph` CLI: upserts, claims, provenance enforcement, query/export."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from typer.testing import CliRunner

from kb.cli.main import app

pytest.importorskip("kuzu")

runner = CliRunner()


MIGRATION = {
    "id": "0001_init",
    "operations": [
        {"op": "create_node_table", "table": {"name": "Document"}},
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
            "table": {
                "name": "MENTIONS",
                "pairs": [{"from": "Document", "to": "Concept"}],
            },
        },
        {
            "op": "create_rel_table",
            "table": {
                "name": "ABOUT",
                "pairs": [{"from": "Claim", "to": "Concept"}],
            },
        },
        {
            "op": "create_rel_table",
            "table": {
                "name": "HAS_OBJECT",
                "pairs": [{"from": "Claim", "to": "Concept"}],
            },
        },
        {
            "op": "create_rel_table",
            "table": {
                "name": "SUPPORTS",
                "pairs": [{"from": "Document", "to": "Claim"}],
            },
        },
    ],
}


@pytest.fixture()
def kb_dir(tmp_path: Path) -> Path:
    kb = tmp_path / "kb"
    assert runner.invoke(app, ["init", str(kb)]).exit_code == 0
    mig = kb / "schema" / "migrations" / "0001_init.json"
    mig.write_text(json.dumps(MIGRATION), encoding="utf-8")
    assert runner.invoke(app, ["schema", "apply", "--kb", str(kb)]).exit_code == 0
    return kb


def _upsert_concept(kb: Path, cid: str = "c1") -> None:
    result = runner.invoke(
        app,
        [
            "graph", "upsert-node", "Concept",
            "--props",
            json.dumps({"id": cid, "name": "FactorGraph", "origin": "raw",
                        "sources": ["raw-0001"], "confidence": 0.9}),
            "--kb", str(kb), "--json",
        ],
    )
    assert result.exit_code == 0, result.output


def test_upsert_node_and_query(kb_dir: Path) -> None:
    _upsert_concept(kb_dir)
    result = runner.invoke(
        app,
        ["graph", "query", "MATCH (c:Concept) RETURN c.id AS id, c.name AS name",
         "--kb", str(kb_dir), "--json"],
    )
    assert result.exit_code == 0, result.output
    rows = json.loads(result.output)["rows"]
    assert rows == [{"id": "c1", "name": "FactorGraph"}]


def test_upsert_node_is_idempotent_update(kb_dir: Path) -> None:
    _upsert_concept(kb_dir)
    result = runner.invoke(
        app,
        ["graph", "upsert-node", "Concept", "--props",
         json.dumps({"id": "c1", "name": "Factor graph (updated)", "origin": "raw",
                     "sources": ["raw-0001"]}),
         "--kb", str(kb_dir), "--json"],
    )
    assert result.exit_code == 0
    result = runner.invoke(
        app,
        ["graph", "query", "MATCH (c:Concept) RETURN count(c) AS n, collect(c.name) AS names",
         "--kb", str(kb_dir), "--json"],
    )
    rows = json.loads(result.output)["rows"]
    assert rows[0]["n"] == 1
    assert rows[0]["names"] == ["Factor graph (updated)"]


def test_upsert_node_without_provenance_rejected(kb_dir: Path) -> None:
    result = runner.invoke(
        app,
        ["graph", "upsert-node", "Concept", "--props",
         json.dumps({"id": "c1", "name": "FactorGraph"}),
         "--kb", str(kb_dir), "--json"],
    )
    assert result.exit_code == 2
    assert "origin" in json.loads(result.output)["error"]


def test_upsert_node_empty_sources_rejected(kb_dir: Path) -> None:
    result = runner.invoke(
        app,
        ["graph", "upsert-node", "Concept", "--props",
         json.dumps({"id": "c1", "origin": "raw", "sources": []}),
         "--kb", str(kb_dir), "--json"],
    )
    assert result.exit_code == 2
    assert "sources" in json.loads(result.output)["error"]


def test_upsert_edge_document_mentions_concept(kb_dir: Path) -> None:
    _upsert_concept(kb_dir)
    assert runner.invoke(
        app,
        ["graph", "upsert-node", "Document", "--props",
         json.dumps({"id": "raw-0001", "name": "paper", "origin": "raw",
                     "sources": ["raw-0001"]}),
         "--kb", str(kb_dir), "--json"],
    ).exit_code == 0
    result = runner.invoke(
        app,
        ["graph", "upsert-edge", "MENTIONS",
         "--from", "Document:raw-0001", "--to", "Concept:c1",
         "--props", json.dumps({"origin": "raw", "sources": ["raw-0001"]}),
         "--kb", str(kb_dir), "--json"],
    )
    assert result.exit_code == 0, result.output
    rows = json.loads(
        runner.invoke(
            app,
            ["graph", "query",
             "MATCH (d:Document)-[:MENTIONS]->(c:Concept) RETURN d.id AS d, c.id AS c",
             "--kb", str(kb_dir), "--json"],
        ).output
    )["rows"]
    assert rows == [{"d": "raw-0001", "c": "c1"}]


def test_upsert_edge_missing_endpoint_rejected(kb_dir: Path) -> None:
    result = runner.invoke(
        app,
        ["graph", "upsert-edge", "MENTIONS",
         "--from", "Document:missing", "--to", "Concept:c1",
         "--props", json.dumps({"origin": "raw", "sources": ["raw-0001"]}),
         "--kb", str(kb_dir), "--json"],
    )
    assert result.exit_code == 2
    assert "not found" in json.loads(result.output)["error"]


def test_upsert_edge_without_provenance_rejected(kb_dir: Path) -> None:
    _upsert_concept(kb_dir)
    result = runner.invoke(
        app,
        ["graph", "upsert-edge", "MENTIONS",
         "--from", "Document:raw-0001", "--to", "Concept:c1",
         "--props", "{}", "--kb", str(kb_dir), "--json"],
    )
    assert result.exit_code == 2


def test_upsert_claim_with_literal_object(kb_dir: Path) -> None:
    _upsert_concept(kb_dir)
    result = runner.invoke(
        app,
        ["graph", "upsert-claim", "cl1",
         "--subject", "Concept:c1", "--predicate", "specializes",
         "--object-literal", "graphical model",
         "--props", json.dumps({"origin": "raw", "sources": ["raw-0001"],
                                "confidence": 0.8}),
         "--kb", str(kb_dir), "--json"],
    )
    assert result.exit_code == 0, result.output
    rows = json.loads(
        runner.invoke(
            app,
            ["graph", "query",
             "MATCH (cl:Claim)-[:ABOUT]->(c:Concept) "
             "RETURN cl.predicate AS p, cl.object_literal AS o, c.id AS c",
             "--kb", str(kb_dir), "--json"],
        ).output
    )["rows"]
    assert rows == [{"p": "specializes", "o": "graphical model", "c": "c1"}]


def test_upsert_claim_with_entity_object(kb_dir: Path) -> None:
    _upsert_concept(kb_dir, "c1")
    _upsert_concept(kb_dir, "c2")
    result = runner.invoke(
        app,
        ["graph", "upsert-claim", "cl2",
         "--subject", "Concept:c1", "--predicate", "specializes",
         "--object", "Concept:c2",
         "--props", json.dumps({"origin": "raw", "sources": ["raw-0001"]}),
         "--kb", str(kb_dir), "--json"],
    )
    assert result.exit_code == 0, result.output
    rows = json.loads(
        runner.invoke(
            app,
            ["graph", "query",
             "MATCH (a:Concept)<-[:ABOUT]-(cl:Claim)-[:HAS_OBJECT]->(b:Concept) "
             "RETURN a.id AS a, cl.id AS cl, b.id AS b",
             "--kb", str(kb_dir), "--json"],
        ).output
    )["rows"]
    assert rows == [{"a": "c1", "cl": "cl2", "b": "c2"}]


def test_upsert_claim_requires_exactly_one_object(kb_dir: Path) -> None:
    _upsert_concept(kb_dir)
    result = runner.invoke(
        app,
        ["graph", "upsert-claim", "cl1",
         "--subject", "Concept:c1", "--predicate", "p",
         "--props", json.dumps({"origin": "raw", "sources": ["raw-0001"]}),
         "--kb", str(kb_dir), "--json"],
    )
    assert result.exit_code == 2
    assert "exactly one" in json.loads(result.output)["error"]


def test_graph_export(kb_dir: Path) -> None:
    _upsert_concept(kb_dir)
    result = runner.invoke(app, ["graph", "export", "--kb", str(kb_dir)])
    assert result.exit_code == 0
    payload = json.loads(result.output)
    assert "Concept" in payload["nodes"]
    assert len(payload["nodes"]["Concept"]) == 1
    assert "_kb_migrations" not in payload["nodes"]


def test_graph_batch_execution(kb_dir: Path) -> None:
    batch = [
        {
            "op": "node",
            "label": "Document",
            "props": {"id": "raw-0001", "name": "doc1", "origin": "raw", "sources": ["raw-0001"]},
        },
        {
            "op": "node",
            "label": "Concept",
            "props": {"id": "c10", "name": "concept10", "origin": "raw", "sources": ["raw-0001"]},
        },
        {
            "op": "edge",
            "rel": "MENTIONS",
            "from": "Document:raw-0001",
            "to": "Concept:c10",
            "props": {"origin": "raw", "sources": ["raw-0001"]},
        },
        {
            "op": "claim",
            "id": "cl-batch-1",
            "subject": "Concept:c10",
            "predicate": "is_valid",
            "object_literal": "true",
            "props": {"origin": "raw", "sources": ["raw-0001"]},
        },
    ]
    batch_file = kb_dir / "batch.json"
    batch_file.write_text(json.dumps(batch), encoding="utf-8")

    result = runner.invoke(
        app,
        ["graph", "batch", "--file", str(batch_file), "--kb", str(kb_dir), "--json"],
    )
    assert result.exit_code == 0, result.output
    summary = json.loads(result.output)
    assert summary["nodes_upserted"] == 2
    assert summary["edges_upserted"] == 1
    assert summary["claims_upserted"] == 1
    assert summary["total_operations"] == 4

    # Verify state in DB
    query_res = runner.invoke(
        app,
        ["graph", "query", "MATCH (c:Concept {id: 'c10'}) RETURN c.name AS name", "--kb", str(kb_dir), "--json"],
    )
    assert json.loads(query_res.output)["rows"] == [{"name": "concept10"}]
