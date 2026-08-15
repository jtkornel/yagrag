"""Tests for the static-check property set as a *schema requirement* (Step 6).

A node type becomes statically checkable by declaring the uniform property set — not
by being named `Equation` or `Factor`. These tests pin both halves of that
contract: the repository's seed schema satisfies it, and an unrelated domain
schema that satisfies it is treated exactly the same way.

The properties are added directly to `0001_seed_domain.json` (no `ALTER TABLE`
migration exists yet), so a freshly initialised KB must expose them right away.
"""

from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest
from typer.testing import CliRunner

from kb.cli.main import app
from kb.code import statically_checkable_labels, symbol_bearing_labels
from kb.config import KBConfig
from kb.graph.connection import open_graph

runner = CliRunner()

pytest.importorskip("grafeo")

REPO_ROOT = Path(__file__).resolve().parents[1]
SEED_MIGRATION = REPO_ROOT / "schema" / "migrations" / "0001_seed_domain.gql"

CODE_LABELS = [
    "Equation",
    "Algorithm",
    "Method",
    "Factor",
    "MotionModel",
    "SensorModel",
    "NoiseModel",
]

CODE_PROPERTIES = [
    "code_language",
    "code_path",
    "code_entry",
    "code_status",
    "code_checked_at",
    "code_checker",
    "code_hash",
]


@pytest.fixture()
def seeded_kb(tmp_path: Path) -> Path:
    """A KB scaffolded and migrated with the repository's seed schema."""
    kb_root = tmp_path / "kb"
    assert runner.invoke(app, ["init", str(kb_root)]).exit_code == 0
    shutil.copy(SEED_MIGRATION, kb_root / "schema" / "migrations" / SEED_MIGRATION.name)
    assert runner.invoke(app, ["schema", "apply", "--kb", str(kb_root)]).exit_code == 0
    return kb_root


def test_init_creates_code_tree(tmp_path: Path) -> None:
    kb_root = tmp_path / "kb"
    assert runner.invoke(app, ["init", str(kb_root)]).exit_code == 0

    config = KBConfig.load(kb_root)
    assert config.paths.code == "code"
    assert (kb_root / config.paths.code).is_dir()


def test_code_properties_present_on_all_code_labels(seeded_kb: Path) -> None:
    config = KBConfig.load(seeded_kb)
    with open_graph(seeded_kb / config.paths.graph_db) as g:
        for label in CODE_LABELS:
            props = {row["name"] for row in g.table_info(label)}
            missing = [p for p in CODE_PROPERTIES if p not in props]
            assert not missing, f"{label} is missing {missing}"


def test_equation_keeps_latex_alongside_code(seeded_kb: Path) -> None:
    config = KBConfig.load(seeded_kb)
    with open_graph(seeded_kb / config.paths.graph_db) as g:
        props = {row["name"] for row in g.table_info("Equation")}
    assert "latex" in props


def test_schema_validate_passes_on_seeded_kb(seeded_kb: Path) -> None:
    result = runner.invoke(app, ["schema", "validate", "--kb", str(seeded_kb)])
    assert result.exit_code == 0, result.output


def test_statically_checkable_labels_are_discovered_not_hardcoded(seeded_kb: Path) -> None:
    config = KBConfig.load(seeded_kb)
    with open_graph(seeded_kb / config.paths.graph_db) as g:
        assert set(statically_checkable_labels(g)) == set(CODE_LABELS)


def test_symbol_bearing_labels_are_discovered_from_the_symbol_property(
    seeded_kb: Path,
) -> None:
    config = KBConfig.load(seeded_kb)
    with open_graph(seeded_kb / config.paths.graph_db) as g:
        assert set(symbol_bearing_labels(g)) == {"Quantity", "Variable"}


# --- the contract is domain-independent ---------------------------------------


BIRD_MIGRATION = {
    "id": "0001_bird_flight",
    "description": "A domain with nothing to do with factor graphs.",
    "operations": [
        {
            "op": "create_node_table",
            "table": {
                "name": "FlightPattern",
                "properties": [
                    {"name": "code_language", "type": "STRING"},
                    {"name": "code_path", "type": "STRING"},
                    {"name": "code_entry", "type": "STRING"},
                    {"name": "code_status", "type": "STRING"},
                    {"name": "code_checked_at", "type": "STRING"},
                    {"name": "code_checker", "type": "STRING"},
                    {"name": "code_hash", "type": "STRING"},
                ],
            },
        },
        {
            "op": "create_node_table",
            "table": {
                "name": "Wingbeat",
                "properties": [{"name": "symbol", "type": "STRING"}],
            },
        },
        {"op": "create_node_table", "table": {"name": "Species"}},
    ],
}


@pytest.fixture()
def bird_kb(tmp_path: Path) -> Path:
    """A KB whose schema knows nothing about robotics or factor graphs."""
    kb_root = tmp_path / "birds"
    assert runner.invoke(app, ["init", str(kb_root)]).exit_code == 0
    migration = kb_root / "schema" / "migrations" / "0001_bird_flight.json"
    migration.write_text(json.dumps(BIRD_MIGRATION), encoding="utf-8")
    assert runner.invoke(app, ["schema", "apply", "--kb", str(kb_root)]).exit_code == 0
    return kb_root


def test_a_foreign_domain_type_is_checkable_by_its_properties(bird_kb: Path) -> None:
    config = KBConfig.load(bird_kb)
    with open_graph(bird_kb / config.paths.graph_db) as g:
        assert statically_checkable_labels(g) == ("FlightPattern",)
        assert symbol_bearing_labels(g) == ("Wingbeat",)


def test_checking_works_end_to_end_in_a_foreign_domain(bird_kb: Path) -> None:
    snippet = bird_kb / "code" / "patterns" / "glide.py"
    snippet.parent.mkdir(parents=True, exist_ok=True)
    snippet.write_text(
        "def glide_ratio(lift: float, drag: float) -> float:\n"
        "    return lift / drag\n",
        encoding="utf-8",
    )
    props = {
        "id": "dynamic-soaring",
        "name": "Dynamic soaring",
        "code_language": "python",
        "code_path": "code/patterns/glide.py",
        "code_entry": "glide_ratio",
        "origin": "raw",
        "sources": ["raw-0001"],
    }
    upsert = runner.invoke(
        app,
        [
            "graph",
            "upsert-node",
            "FlightPattern",
            "--props",
            json.dumps(props),
            "--kb",
            str(bird_kb),
        ],
    )
    assert upsert.exit_code == 0, upsert.output

    result = runner.invoke(app, ["code", "check", "--kb", str(bird_kb), "--json"])
    assert result.exit_code == 0, result.output
    payload = json.loads(result.stdout)
    assert payload["ok"] == 1
    assert payload["results"][0]["label"] == "FlightPattern"
