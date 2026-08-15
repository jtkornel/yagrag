"""Tests for `kb graph` CLI: upserts, claims, provenance enforcement, query/export."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from typer.testing import CliRunner

from kb.cli.main import app

pytest.importorskip("grafeo")

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
            "op": "create_node_table",
            "table": {
                "name": "Acronym",
                "properties": [
                    {"name": "short_form", "type": "STRING"},
                    {"name": "expansion", "type": "STRING"},
                    {"name": "domain_context", "type": "STRING"},
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
        {
            "op": "create_rel_table",
            "table": {
                "name": "USES_ACRONYM",
                "pairs": [{"from": "Concept", "to": "Acronym"}],
            },
        },
        {
            "op": "create_rel_table",
            "table": {
                "name": "STANDS_FOR",
                "pairs": [{"from": "Acronym", "to": "Concept"}],
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
             ("MATCH (cl:Claim)-[:ABOUT]->(c:Concept) "
              "RETURN cl.predicate AS p, cl.object_literal AS o, c.id AS c"),
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
             ("MATCH (a:Concept)<-[:ABOUT]-(cl:Claim)-[:HAS_OBJECT]->(b:Concept) "
              "RETURN a.id AS a, cl.id AS cl, b.id AS b"),
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


def test_acronym_upsert_and_relations(kb_dir: Path) -> None:
    _upsert_concept(kb_dir, "concept-slam")
    # Upsert Acronym node
    res = runner.invoke(
        app,
        [
            "graph", "upsert-node", "Acronym",
            "--props", json.dumps({
                "id": "acronym:slam:simultaneous_localization_and_mapping",
                "name": "SLAM (Simultaneous Localization and Mapping)",
                "short_form": "SLAM",
                "expansion": "Simultaneous Localization and Mapping",
                "domain_context": "robotics",
                "summary": "Simultaneous Localization and Mapping",
                "origin": "raw",
                "sources": ["raw-0001"],
            }),
            "--kb", str(kb_dir), "--json",
        ],
    )
    assert res.exit_code == 0, res.output

    # Upsert USES_ACRONYM edge from Concept to Acronym
    res = runner.invoke(
        app,
        [
            "graph", "upsert-edge", "USES_ACRONYM",
            "--from", "Concept:concept-slam",
            "--to", "Acronym:acronym:slam:simultaneous_localization_and_mapping",
            "--props", json.dumps({"origin": "raw", "sources": ["raw-0001"]}),
            "--kb", str(kb_dir), "--json",
        ],
    )
    assert res.exit_code == 0, res.output

    # Upsert STANDS_FOR edge from Acronym to Concept
    res = runner.invoke(
        app,
        [
            "graph", "upsert-edge", "STANDS_FOR",
            "--from", "Acronym:acronym:slam:simultaneous_localization_and_mapping",
            "--to", "Concept:concept-slam",
            "--props", json.dumps({"origin": "raw", "sources": ["raw-0001"]}),
            "--kb", str(kb_dir), "--json",
        ],
    )
    assert res.exit_code == 0, res.output

    # Query and verify edges
    rows = json.loads(
        runner.invoke(
            app,
            [
                "graph", "query",
                ("MATCH (c:Concept)-[:USES_ACRONYM]->(a:Acronym)-[:STANDS_FOR]->(c2:Concept) "
                 "RETURN c.id AS cid, a.short_form AS sf, a.expansion AS exp, c2.id AS c2id"),
                "--kb", str(kb_dir), "--json",
            ],
        ).output
    )["rows"]
    assert len(rows) == 1
    assert rows[0] == {
        "cid": "concept-slam",
        "sf": "SLAM",
        "exp": "Simultaneous Localization and Mapping",
        "c2id": "concept-slam",
    }


def test_acronym_lint_checks(kb_dir: Path) -> None:
    # 1. Missing short_form & expansion
    runner.invoke(
        app,
        [
            "graph", "upsert-node", "Acronym",
            "--props", json.dumps({
                "id": "acronym:invalid",
                "name": "Invalid Acronym",
                "summary": "Bad acronym without short form",
                "origin": "raw",
                "sources": ["raw-0001"],
            }),
            "--kb", str(kb_dir), "--json",
        ],
    )
    res = runner.invoke(app, ["graph", "lint", "--kb", str(kb_dir), "--json"])
    assert res.exit_code == 0
    data = json.loads(res.output)
    categories = [i["category"] for i in data["issues"]]
    assert "acronym_quality" in categories
    assert any("short_form" in i["message"] for i in data["issues"])
    assert any("expansion" in i["message"] for i in data["issues"])


def test_acronym_deduplication(kb_dir: Path) -> None:
    # Two acronyms with different short forms (EKF vs UKF) should NOT be merged
    runner.invoke(
        app,
        [
            "graph", "upsert-node", "Acronym",
            "--props", json.dumps({
                "id": "acronym:ekf",
                "name": "EKF",
                "short_form": "EKF",
                "expansion": "Extended Kalman Filter",
                "summary": "EKF summary",
                "origin": "raw",
                "sources": ["raw-0001"],
            }),
            "--kb", str(kb_dir), "--json",
        ],
    )
    runner.invoke(
        app,
        [
            "graph", "upsert-node", "Acronym",
            "--props", json.dumps({
                "id": "acronym:ukf",
                "name": "UKF",
                "short_form": "UKF",
                "expansion": "Unscented Kalman Filter",
                "summary": "UKF summary",
                "origin": "raw",
                "sources": ["raw-0002"],
            }),
            "--kb", str(kb_dir), "--json",
        ],
    )
    res = runner.invoke(app, ["graph", "dedupe", "--label", "Acronym", "--kb", str(kb_dir), "--json"])
    assert res.exit_code == 0
    data = json.loads(res.output)
    assert data["merged_count"] == 0


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


def test_graph_lint_command(kb_dir: Path) -> None:
    # 1. Upsert a connected node
    _upsert_concept(kb_dir, "c1")
    runner.invoke(
        app,
        ["graph", "upsert-node", "Document", "--props",
         json.dumps({"id": "doc1", "name": "Doc 1", "origin": "raw", "sources": ["doc1"]}),
         "--kb", str(kb_dir), "--json"],
    )
    runner.invoke(
        app,
        ["graph", "upsert-edge", "MENTIONS", "--from", "Document:doc1", "--to", "Concept:c1",
         "--props", json.dumps({"origin": "raw", "sources": ["doc1"]}),
         "--kb", str(kb_dir), "--json"],
    )

    # Lint should pass
    res = runner.invoke(app, ["graph", "lint", "--kb", str(kb_dir), "--json"])
    assert res.exit_code == 0, res.output
    payload = json.loads(res.output)
    assert payload["ok"] is True
    assert payload["issue_count"] == 0

    # 2. Add a floating node
    runner.invoke(
        app,
        ["graph", "upsert-node", "Concept", "--props",
         json.dumps({"id": "c_floating", "name": "Floating Concept", "origin": "raw", "sources": ["doc1"]}),
         "--kb", str(kb_dir), "--json"],
    )
    res = runner.invoke(app, ["graph", "lint", "--kb", str(kb_dir), "--json"])
    assert res.exit_code == 0
    payload = json.loads(res.output)
    assert payload["ok"] is True  # warning severity
    assert payload["issue_count"] == 1
    assert payload["issues"][0]["category"] == "floating_node"


def test_graph_dedupe_command_dry_run_and_apply(kb_dir: Path) -> None:
    # 1. Upsert document
    runner.invoke(
        app,
        ["graph", "upsert-node", "Document", "--props",
         json.dumps({"id": "doc1", "name": "Paper 1", "origin": "raw", "sources": ["doc1"]}),
         "--kb", str(kb_dir), "--json"],
    )

    # 2. Upsert two duplicate Tool nodes
    runner.invoke(
        app,
        ["graph", "upsert-node", "Concept", "--props",
         json.dumps({"id": "tool_gtsam", "name": "GTSAM Factor Graph Library", "origin": "raw", "sources": ["doc1"]}),
         "--kb", str(kb_dir), "--json"],
    )
    runner.invoke(
        app,
        ["graph", "upsert-edge", "MENTIONS", "--from", "Document:doc1", "--to", "Concept:tool_gtsam",
         "--props", json.dumps({"origin": "raw", "sources": ["doc1"]}),
         "--kb", str(kb_dir), "--json"],
    )

    runner.invoke(
        app,
        ["graph", "upsert-node", "Concept", "--props",
         json.dumps({"id": "gtsam_lib", "name": "GTSAM factor graph library", "origin": "raw", "sources": ["doc2"]}),
         "--kb", str(kb_dir), "--json"],
    )

    # 3. Test dry-run dedupe
    res_dry = runner.invoke(app, ["graph", "dedupe", "--label", "Concept", "--kb", str(kb_dir), "--json"])
    assert res_dry.exit_code == 0, res_dry.output
    dry_data = json.loads(res_dry.output)
    assert dry_data["applied"] is False
    assert dry_data["merged_count"] == 1
    assert dry_data["merges"][0]["canonical_id"] == "tool_gtsam"
    assert dry_data["merges"][0]["merged_id"] == "gtsam_lib"

    # 4. Test apply dedupe
    res_apply = runner.invoke(app, ["graph", "dedupe", "--label", "Concept", "--apply", "--kb", str(kb_dir), "--json"])
    assert res_apply.exit_code == 0, res_apply.output
    apply_data = json.loads(res_apply.output)
    assert apply_data["applied"] is True

    # 5. Verify in graph
    res_q = runner.invoke(
        app,
        ["graph", "query", "MATCH (c:Concept) RETURN c.id AS id, c.sources AS sources", "--kb", str(kb_dir), "--json"],
    )
    rows = json.loads(res_q.output)["rows"]
    assert len(rows) == 1
    assert rows[0]["id"] == "tool_gtsam"
    assert set(rows[0]["sources"]) == {"doc1", "doc2"}
