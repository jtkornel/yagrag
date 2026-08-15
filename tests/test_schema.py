"""Tests for the Grafeo graph layer and versioned GQL schema management.

Covers: DDL rendering, migration loading, apply/validate round-trip via the
CLI, a reified Claim write/read, and a follow-up migration adding a new
domain-specific node + relation type.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from typer.testing import CliRunner

from kb.cli.main import app
from kb.graph.connection import GraphDB
from kb.schema.migrations import (
    MigrationError,
    apply_migrations,
    build_target_schema,
    load_migrations,
    next_migration_id,
    validate,
)
from kb.schema.model import (
    Migration,
    NodeType,
    Property,
    RelationType,
    RelPair,
    Schema,
    render_create_edge_type_grafeo,
    render_create_node_type_grafeo,
    render_gql_graph_type,
)

runner = CliRunner()

pytest.importorskip("grafeo")


# --- fixtures -----------------------------------------------------------------


@pytest.fixture()
def kb_dir(tmp_path: Path) -> Path:
    """A scaffolded KB directory in a temp dir."""
    result = runner.invoke(app, ["init", str(tmp_path / "kb")])
    assert result.exit_code == 0
    return tmp_path / "kb"


INIT_GQL = """
-- Document layer + minimal domain layer + reified Claim.
CREATE NODE TYPE Document (id STRING, name STRING, summary STRING, origin STRING, sources LIST, confidence FLOAT64, created_at TIMESTAMP, updated_at TIMESTAMP, kind STRING, path STRING);
CREATE NODE TYPE Concept (id STRING, name STRING, summary STRING, origin STRING, sources LIST, confidence FLOAT64, created_at TIMESTAMP, updated_at TIMESTAMP);
CREATE NODE TYPE Claim (id STRING, name STRING, summary STRING, origin STRING, sources LIST, confidence FLOAT64, created_at TIMESTAMP, updated_at TIMESTAMP, predicate STRING, object_literal STRING, qualifiers STRING);

CREATE EDGE TYPE MENTIONS (origin STRING, sources LIST, confidence FLOAT64, created_at TIMESTAMP, updated_at TIMESTAMP);
CREATE EDGE TYPE ABOUT (origin STRING, sources LIST, confidence FLOAT64, created_at TIMESTAMP, updated_at TIMESTAMP);
CREATE EDGE TYPE HAS_OBJECT (origin STRING, sources LIST, confidence FLOAT64, created_at TIMESTAMP, updated_at TIMESTAMP);
"""


def _write_gql_migration(kb: Path, mid: str, gql_text: str) -> Path:
    path = kb / "schema" / "migrations" / f"{mid}.gql"
    path.write_text(gql_text, encoding="utf-8")
    return path


# --- DDL rendering ------------------------------------------------------------


def test_render_node_table_injects_common_properties() -> None:
    ddl = render_create_node_type_grafeo(NodeType(name="Concept"))
    assert ddl.startswith("CREATE NODE TYPE Concept (")
    for col in ("id STRING", "origin STRING", "sources LIST", "confidence FLOAT64"):
        assert col in ddl


def test_render_rel_table_properties() -> None:
    rt = RelationType(name="MENTIONS")
    ddl = render_create_edge_type_grafeo(rt)
    assert ddl.startswith("CREATE EDGE TYPE MENTIONS (")
    assert "origin STRING" in ddl
    assert "sources LIST" in ddl


def test_node_type_requires_exactly_one_primary_key() -> None:
    nt = NodeType(name="Bad", include_common=False, properties=[Property(name="x", type="STRING")])
    with pytest.raises(ValueError, match="primary key"):
        nt.effective_properties()


def test_invalid_property_type_rejected() -> None:
    with pytest.raises(ValueError, match="unsupported"):
        Property(name="x", type="BLOB_UNSUPPORTED")


# --- migration loading ----------------------------------------------------------


def test_load_migrations_ordering_and_ids(kb_dir: Path) -> None:
    _write_gql_migration(kb_dir, "0002_second", "-- second\n")
    _write_gql_migration(kb_dir, "0001_first", "-- first\n")
    mfs = load_migrations(kb_dir / "schema" / "migrations")
    assert [m.id for m in mfs] == ["0001_first", "0002_second"]


def test_bad_migration_id_rejected(kb_dir: Path) -> None:
    path = kb_dir / "schema" / "migrations" / "bad-name.gql"
    path.write_text("-- invalid\n", encoding="utf-8")
    with pytest.raises(MigrationError, match="invalid migration id"):
        load_migrations(kb_dir / "schema" / "migrations")


def test_next_migration_id(kb_dir: Path) -> None:
    mdir = kb_dir / "schema" / "migrations"
    assert next_migration_id(mdir, "init") == "0001_init"
    _write_gql_migration(kb_dir, "0001_init", "-- init\n")
    assert next_migration_id(mdir, "add_solver") == "0002_add_solver"


def test_build_target_schema_folds_migrations(kb_dir: Path) -> None:
    _write_gql_migration(kb_dir, "0001_init", INIT_GQL)
    schema = build_target_schema(kb_dir / "schema" / "migrations")
    assert set(schema.node_type_names()) == {"Document", "Concept", "Claim"}
    assert set(schema.relation_type_names()) == {"MENTIONS", "ABOUT", "HAS_OBJECT"}


# --- apply / validate round-trip via CLI ---------------------------------------


def test_schema_apply_validate_roundtrip_cli(kb_dir: Path) -> None:
    _write_gql_migration(kb_dir, "0001_init", INIT_GQL)

    result = runner.invoke(app, ["schema", "apply", "--kb", str(kb_dir), "--json"])
    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert payload["newly_applied"] == ["0001_init"]

    # apply again → idempotent, nothing new
    result = runner.invoke(app, ["schema", "apply", "--kb", str(kb_dir), "--json"])
    assert result.exit_code == 0
    assert json.loads(result.output)["newly_applied"] == []

    result = runner.invoke(app, ["schema", "validate", "--kb", str(kb_dir), "--json"])
    assert result.exit_code == 0, result.output
    report = json.loads(result.output)
    assert report["ok"] is True


def test_schema_validate_fails_on_pending_migration(kb_dir: Path) -> None:
    _write_gql_migration(kb_dir, "0001_init", INIT_GQL)
    result = runner.invoke(app, ["schema", "validate", "--kb", str(kb_dir), "--json"])
    assert result.exit_code == 1
    report = json.loads(result.output)
    assert report["ok"] is False
    assert "0001_init" in report["pending_migrations"]


def test_schema_show_cli(kb_dir: Path) -> None:
    _write_gql_migration(kb_dir, "0001_init", INIT_GQL)
    result = runner.invoke(app, ["schema", "show", "--kb", str(kb_dir), "--json"])
    assert result.exit_code == 0
    schema = Schema.model_validate_json(result.output)
    assert "Claim" in schema.node_type_names()


def test_schema_show_type_filter_cli(kb_dir: Path) -> None:
    _write_gql_migration(kb_dir, "0001_init", INIT_GQL)
    result = runner.invoke(app, ["schema", "show", "-t", "Concept", "--kb", str(kb_dir), "--json"])
    assert result.exit_code == 0
    data = json.loads(result.output)
    assert len(data["node_types"]) == 1
    assert data["node_types"][0]["name"] == "Concept"
    assert len(data["relation_types"]) == 0


def test_schema_migrate_scaffolds_file(kb_dir: Path) -> None:
    result = runner.invoke(
        app, ["schema", "migrate", "add_noise_model", "--kb", str(kb_dir), "--json"]
    )
    assert result.exit_code == 0
    payload = json.loads(result.output)
    assert payload["id"] == "0001_add_noise_model"
    assert Path(payload["created"]).is_file()
    assert payload["created"].endswith(".gql")


# --- Claim write/read + domain migration ---------------------------------------


def test_claim_write_read_and_domain_migration(kb_dir: Path) -> None:
    _write_gql_migration(kb_dir, "0001_init", INIT_GQL)
    mdir = kb_dir / "schema" / "migrations"
    db_path = kb_dir / "graph.grafeo"

    with GraphDB(db_path) as g:
        apply_migrations(g, mdir)

        # Write a reified Claim with provenance, linked to a Concept.
        g.execute(
            "CREATE (:Concept {id: 'c1', name: 'FactorGraph', origin: 'raw', "
            "sources: ['doc1'], confidence: 0.9})"
        )
        g.execute(
            "CREATE (:Claim {id: 'cl1', predicate: 'specializes', "
            "object_literal: 'graphical model', origin: 'raw', "
            "sources: ['doc1'], confidence: 0.8})"
        )
        g.execute(
            "MATCH (cl:Claim {id: 'cl1'}), (c:Concept {id: 'c1'}) "
            "CREATE (cl)-[:ABOUT {origin: 'raw', sources: ['doc1'], confidence: 0.8}]->(c)"
        )
        rows = g.execute(
            "MATCH (cl:Claim)-[:ABOUT]->(c:Concept) "
            "RETURN cl.predicate AS predicate, cl.sources AS sources, c.name AS name"
        )
        assert rows == [
            {"predicate": "specializes", "sources": ["doc1"], "name": "FactorGraph"}
        ]

    # A second migration adds a domain-specific node + relation type.
    _write_gql_migration(
        kb_dir,
        "0002_add_factor_variable",
        """
        CREATE NODE TYPE Factor (id STRING, name STRING, summary STRING, origin STRING, sources LIST, confidence FLOAT64, created_at TIMESTAMP, updated_at TIMESTAMP);
        CREATE NODE TYPE Variable (id STRING, name STRING, summary STRING, origin STRING, sources LIST, confidence FLOAT64, created_at TIMESTAMP, updated_at TIMESTAMP);
        CREATE EDGE TYPE CONNECTS (origin STRING, sources LIST, confidence FLOAT64, created_at TIMESTAMP, updated_at TIMESTAMP);
        """,
    )
    with GraphDB(db_path) as g:
        newly = apply_migrations(g, mdir)
        assert newly == ["0002_add_factor_variable"]
        assert {"Factor", "Variable"} <= set(g.node_table_names())
        assert "CONNECTS" in g.rel_table_names()
        report = validate(g, mdir)
        assert report.ok, report.to_dict()


def test_raw_cypher_migration(kb_dir: Path) -> None:
    mdir = kb_dir / "schema" / "migrations"
    (mdir / "0001_raw.cypher").write_text(
        "CREATE NODE TYPE Sensor(id STRING, name STRING);\n",
        encoding="utf-8",
    )
    with GraphDB(kb_dir / "graph.grafeo") as g:
        newly = apply_migrations(g, mdir)
        assert newly == ["0001_raw"]
        assert "Sensor" in g.node_table_names()


def test_migration_fold_rejects_duplicate_type() -> None:
    schema = Schema()
    m = Migration.model_validate(
        {
            "id": "0001_dup",
            "operations": [
                {"op": "create_node_table", "table": {"name": "Concept"}},
                {"op": "create_node_table", "table": {"name": "Concept"}},
            ],
        }
    )
    with pytest.raises(ValueError, match="duplicate node type"):
        m.apply_to_schema(schema)


def test_render_gql_graph_type() -> None:
    schema = Schema(
        node_types=[NodeType(name="Document"), NodeType(name="Author")],
        relation_types=[
            RelationType(
                name="AUTHORED_BY",
                pairs=[RelPair.model_validate({"from": "Document", "to": "Author"})],
            )
        ],
    )
    gql = render_gql_graph_type(schema, "TestGraphType")
    assert "CREATE GRAPH TYPE TestGraphType AS {" in gql
    assert "NODE TYPE Document {" in gql
    assert "EDGE TYPE AUTHORED_BY CONNECTING (" in gql
    assert "Document TO Author" in gql


def test_cli_schema_show_gql(kb_dir: Path) -> None:
    _write_gql_migration(kb_dir, "0001_init", INIT_GQL)
    result = runner.invoke(app, ["schema", "show", "--kb", str(kb_dir), "--gql"])
    assert result.exit_code == 0
    assert "CREATE GRAPH TYPE KnowledgeBaseGraphType AS {" in result.output
    assert "NODE TYPE Document" in result.output
    assert "EDGE TYPE MENTIONS" in result.output
