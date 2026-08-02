"""Tests for the Kuzu graph layer and versioned schema management (Step 2).

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
from kb.schema.model import (
    Migration,
    NodeType,
    Property,
    RelationType,
    RelPair,
    render_create_node_table,
    render_create_rel_table,
    Schema,
)
from kb.schema.migrations import (
    MigrationError,
    apply_migrations,
    build_target_schema,
    load_migrations,
    next_migration_id,
    validate,
)

runner = CliRunner()

pytest.importorskip("kuzu")


# --- fixtures -----------------------------------------------------------------


@pytest.fixture()
def kb_dir(tmp_path: Path) -> Path:
    """A scaffolded KB directory in a temp dir."""
    result = runner.invoke(app, ["init", str(tmp_path / "kb")])
    assert result.exit_code == 0
    return tmp_path / "kb"


def _write_migration(kb: Path, data: dict) -> Path:
    path = kb / "schema" / "migrations" / f"{data['id']}.json"
    path.write_text(json.dumps(data, indent=2), encoding="utf-8")
    return path


INIT_MIGRATION = {
    "id": "0001_init",
    "description": "Document layer + minimal domain layer + reified Claim.",
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
                    {"name": "qualifiers", "type": "STRING"},
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
    ],
}


# --- DDL rendering ------------------------------------------------------------


def test_render_node_table_injects_common_properties() -> None:
    ddl = render_create_node_table(NodeType(name="Concept"))
    assert ddl.startswith("CREATE NODE TABLE Concept(")
    for col in ("id STRING", "origin STRING", "sources STRING[]", "confidence DOUBLE"):
        assert col in ddl
    assert ddl.endswith("PRIMARY KEY(id))")


def test_render_rel_table_multiple_pairs() -> None:
    rt = RelationType(
        name="MENTIONS",
        pairs=[
            RelPair.model_validate({"from": "Document", "to": "Concept"}),
            RelPair.model_validate({"from": "Document", "to": "Claim"}),
        ],
    )
    ddl = render_create_rel_table(rt)
    assert "FROM Document TO Concept" in ddl
    assert "FROM Document TO Claim" in ddl
    assert "origin STRING" in ddl


def test_node_type_requires_exactly_one_primary_key() -> None:
    nt = NodeType(name="Bad", include_common=False, properties=[Property(name="x", type="STRING")])
    with pytest.raises(ValueError, match="primary key"):
        nt.effective_properties()


def test_invalid_property_type_rejected() -> None:
    with pytest.raises(ValueError, match="unsupported"):
        Property(name="x", type="BLOB")


# --- migration loading ----------------------------------------------------------


def test_load_migrations_ordering_and_ids(kb_dir: Path) -> None:
    _write_migration(kb_dir, {"id": "0002_second", "operations": []})
    _write_migration(kb_dir, {"id": "0001_first", "operations": []})
    mfs = load_migrations(kb_dir / "schema" / "migrations")
    assert [m.id for m in mfs] == ["0001_first", "0002_second"]


def test_bad_migration_id_rejected(kb_dir: Path) -> None:
    path = kb_dir / "schema" / "migrations" / "bad-name.json"
    path.write_text(json.dumps({"id": "bad-name", "operations": []}), encoding="utf-8")
    with pytest.raises(MigrationError, match="invalid migration id"):
        load_migrations(kb_dir / "schema" / "migrations")


def test_next_migration_id(kb_dir: Path) -> None:
    mdir = kb_dir / "schema" / "migrations"
    assert next_migration_id(mdir, "init") == "0001_init"
    _write_migration(kb_dir, {"id": "0001_init", "operations": []})
    assert next_migration_id(mdir, "add_solver") == "0002_add_solver"


def test_build_target_schema_folds_migrations(kb_dir: Path) -> None:
    _write_migration(kb_dir, INIT_MIGRATION)
    schema = build_target_schema(kb_dir / "schema" / "migrations")
    assert set(schema.node_type_names()) == {"Document", "Concept", "Claim"}
    assert set(schema.relation_type_names()) == {"MENTIONS", "ABOUT", "HAS_OBJECT"}


# --- apply / validate round-trip via CLI ---------------------------------------


def test_schema_apply_validate_roundtrip_cli(kb_dir: Path) -> None:
    _write_migration(kb_dir, INIT_MIGRATION)

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
    _write_migration(kb_dir, INIT_MIGRATION)
    result = runner.invoke(app, ["schema", "validate", "--kb", str(kb_dir), "--json"])
    assert result.exit_code == 1
    report = json.loads(result.output)
    assert report["ok"] is False
    assert "0001_init" in report["pending_migrations"]


def test_schema_show_cli(kb_dir: Path) -> None:
    _write_migration(kb_dir, INIT_MIGRATION)
    result = runner.invoke(app, ["schema", "show", "--kb", str(kb_dir), "--json"])
    assert result.exit_code == 0
    schema = Schema.model_validate_json(result.output)
    assert "Claim" in schema.node_type_names()


def test_schema_show_type_filter_cli(kb_dir: Path) -> None:
    _write_migration(kb_dir, INIT_MIGRATION)
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


# --- Claim write/read + domain migration ---------------------------------------


def test_claim_write_read_and_domain_migration(kb_dir: Path) -> None:
    _write_migration(kb_dir, INIT_MIGRATION)
    mdir = kb_dir / "schema" / "migrations"
    db_path = kb_dir / "graph.kuzu"

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
    _write_migration(
        kb_dir,
        {
            "id": "0002_add_factor_variable",
            "operations": [
                {"op": "create_node_table", "table": {"name": "Factor"}},
                {"op": "create_node_table", "table": {"name": "Variable"}},
                {
                    "op": "create_rel_table",
                    "table": {
                        "name": "CONNECTS",
                        "pairs": [{"from": "Factor", "to": "Variable"}],
                    },
                },
            ],
        },
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
        "CREATE NODE TABLE Sensor(id STRING, name STRING, PRIMARY KEY(id));\n",
        encoding="utf-8",
    )
    with GraphDB(kb_dir / "graph.kuzu") as g:
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
