"""Tests for the schema companion and the MaRDI alignment migration (0005)."""

from __future__ import annotations

from pathlib import Path

import pytest

from kb.schema.companion import CompanionError, load_companion
from kb.schema.migrations import build_target_schema

pytest.importorskip("traverse")

REPO_ROOT = Path(__file__).resolve().parent.parent
MIGRATIONS_DIR = REPO_ROOT / "schema" / "migrations"
COMPANION_PATH = REPO_ROOT / "schema" / "schema_companion.json"


@pytest.fixture(scope="module")
def companion():
    return load_companion(COMPANION_PATH)


@pytest.fixture(scope="module")
def target_schema():
    return build_target_schema(MIGRATIONS_DIR)


# --- companion loader ---------------------------------------------------------


def test_companion_loads(companion):
    assert companion.nodes and companion.edges


def test_symmetric_edges_have_no_distinct_inverse():
    import json

    data = json.loads(COMPANION_PATH.read_text(encoding="utf-8"))
    for name, e in data["edges"].items():
        if e.get("symmetric"):
            assert e.get("inverse_of") in (None, name), f"{name}: symmetric with distinct inverse"


def test_invalid_symmetric_inverse_rejected(tmp_path):
    bad = {
        "edges": {
            "A": {"symmetric": True, "inverse_of": "B"},
            "B": {},
        }
    }
    p = tmp_path / "bad.json"
    p.write_text(__import__("json").dumps(bad), encoding="utf-8")
    with pytest.raises(CompanionError):
        load_companion(p)


# --- inverse / qualifier helpers ----------------------------------------------


def test_canonical_edge_resolves_inverse(companion):
    models = companion.edge("MODELS")
    assert models is not None and not models.stored
    canonical = companion.canonical_edge("MODELS")
    assert canonical is not None and canonical.name == "MODELLED_BY" and canonical.stored


def test_inverse_lookup_both_directions(companion):
    assert companion.inverse_name("MODELLED_BY") == "MODELS"
    assert companion.inverse_name("MODELS") == "MODELLED_BY"


def test_qualifier_flags(companion):
    assert companion.is_transitive("SPECIALIZES")
    assert companion.is_transitive("SUBCLASS_OF")
    assert companion.is_transitive("HAS_COMPONENT")
    assert companion.is_symmetric("SIMILAR_TO")
    assert companion.is_symmetric("RELATES_TO")
    assert not companion.is_transitive("USED_BY")
    assert not companion.is_symmetric("MODELLED_BY")


# --- consistency with GQL migrations -------------------------------------------


def test_companion_covers_full_schema(companion, target_schema):
    issues = companion.validate_against_schema(
        target_schema.node_type_names(), target_schema.relation_type_names()
    )
    assert issues == [], "companion/schema drift:\n" + "\n".join(issues)


def test_migration_0005_types_present(target_schema):
    nodes = set(target_schema.node_type_names())
    edges = set(target_schema.relation_type_names())
    for n in ("ComputationalTask", "ApplicationProblem", "ApplicationDomain", "QuantityKind", "Benchmark"):
        assert n in nodes, f"missing node type {n}"
    for e in (
        "MODELLED_BY", "USED_BY", "SOLVES", "INSTANCE_OF", "TESTS",
        "SUBCLASS_OF", "HAS_COMPONENT", "DISCRETIZED_BY", "LINEARIZED_BY",
        "APPROXIMATED_BY", "NONDIMENSIONALIZED_BY", "HAS_WEAK_FORMULATION",
        "SOLUTION_TO", "SIMILAR_TO", "INVENTED_IN", "ANALYZED_IN",
        "STUDIED_IN", "APPLIED_IN", "REVIEWED_IN", "DOCUMENTED_IN", "USED_IN",
        "HAS_KIND",
    ):
        assert e in edges, f"missing edge type {e}"


def test_new_nodes_have_external_id_columns(target_schema):
    props = {nt.name: {p.name for p in nt.properties} for nt in target_schema.node_types}
    assert "wikidata_qid" in props["ApplicationDomain"]
    assert "msc_id" in props["ApplicationDomain"]
    assert "qudt_id" in props["QuantityKind"]
    assert "wikidata_qid" in props["ComputationalTask"]
