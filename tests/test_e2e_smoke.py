"""End-to-end smoke test driving the workflow the agent skills prescribe.

The test runs `examples/factor-graph-slam/build_example.sh`, which performs the
ingest -> deep knowledge extraction -> index -> search sequence with the LLM
reasoning "frozen" into literal CLI calls. This keeps the worked example and the
test in sync: if the example rots, the test fails.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest

pytest.importorskip("grafeo")

REPO_ROOT = Path(__file__).resolve().parents[1]
EXAMPLE_SCRIPT = REPO_ROOT / "examples" / "factor-graph-slam" / "build_example.sh"

# Invoke the CLI through the running interpreter so the test works without the
# `kb` console script being on PATH.
KB_CMD = f"{sys.executable} -m kb.cli.main"


def _kb(kb_dir: Path, *args: str) -> str:
    result = subprocess.run(
        [sys.executable, "-m", "kb.cli.main", *args, "--kb", str(kb_dir), "--json"],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    return result.stdout


def _query(kb_dir: Path, cypher: str) -> list[dict[str, Any]]:
    return json.loads(_kb(kb_dir, "graph", "query", cypher))["rows"]


@pytest.fixture(scope="module")
def example_kb(tmp_path_factory: pytest.TempPathFactory) -> Path:
    if shutil.which("bash") is None:  # pragma: no cover - environment guard
        pytest.skip("bash is required to run the example script")
    kb_dir = tmp_path_factory.mktemp("e2e") / "kb"
    env = {
        **os.environ,
        "KB": KB_CMD,
        # Deterministic, offline embedder: no model download in tests.
        "KB_EMBEDDER_BACKEND": "hash",
    }
    result = subprocess.run(
        ["bash", str(EXAMPLE_SCRIPT), str(kb_dir)],
        cwd=REPO_ROOT,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    return kb_dir


def test_schema_is_valid_after_seed(example_kb: Path) -> None:
    report = json.loads(_kb(example_kb, "schema", "validate"))
    assert report["ok"] is True
    assert report["pending_migrations"] == []


def test_raw_document_is_ingested_immutably(example_kb: Path) -> None:
    payload = json.loads(_kb(example_kb, "doc", "list"))
    docs = payload["documents"] if isinstance(payload, dict) else payload
    assert len(docs) == 1
    doc = docs[0]
    assert doc["id"] == "raw-0001"
    assert doc["kind"] == "raw"
    stored = example_kb / doc["path"]
    assert stored.is_file()
    assert not os.access(stored, os.W_OK)


def test_factor_graph_internals_are_captured(example_kb: Path) -> None:
    """The point of the exercise: the graph holds the paper's internals."""
    rows = _query(
        example_kb,
        "MATCH (g:FactorGraph)-[:HAS_FACTOR]->(f:Factor)-[:CONNECTS]->(v:Variable) "
        "RETURN g.name AS graph, f.name AS factor, v.name AS variable",
    )
    assert len(rows) >= 4
    factors = {r["factor"] for r in rows}
    assert "IMU preintegration factor" in factors
    assert "Slip-aware wheel-odometry factor" in factors
    variables = {r["variable"] for r in rows}
    assert {"body pose x_k", "IMU bias b_k"} <= variables


def test_estimator_and_models_are_linked(example_kb: Path) -> None:
    rows = _query(
        example_kb,
        "MATCH (e:StateEstimator)-[:USES]->(m:MotionModel) RETURN m.id AS model",
    )
    assert [r["model"] for r in rows] == ["slip-aware-diff-drive"]

    rows = _query(
        example_kb,
        "MATCH (g:FactorGraph)-[:SOLVED_BY]->(s:Solver) RETURN s.id AS solver",
    )
    assert [r["solver"] for r in rows] == ["isam2"]


def test_claims_are_reified_with_provenance(example_kb: Path) -> None:
    rows = _query(
        example_kb,
        "MATCH (d:Document)-[:SUPPORTS]->(c:Claim)-[:ABOUT]->(s) "
        "RETURN d.id AS doc, c.id AS claim, c.predicate AS predicate, "
        "c.confidence AS confidence, c.origin AS origin, c.sources AS sources",
    )
    assert len(rows) == 3
    for row in rows:
        assert row["doc"] == "raw-0001"
        assert row["origin"] == "raw"
        assert row["sources"] == ["raw-0001"]
        assert 0.0 < row["confidence"] <= 1.0
    predicates = {r["predicate"] for r in rows}
    assert "achieves_ate_on" in predicates


@pytest.mark.parametrize(
    "label",
    [
        "Document",
        "Method",
        "FactorGraph",
        "Variable",
        "Factor",
        "StateEstimator",
        "MotionModel",
        "NoiseModel",
        "Sensor",
        "Equation",
        "Assumption",
        "Claim",
    ],
)
def test_every_entity_carries_provenance(example_kb: Path, label: str) -> None:
    """No domain node may exist without `origin` and at least one source."""
    rows = _query(
        example_kb,
        f"MATCH (n:{label}) RETURN n.id AS id, n.origin AS origin, "
        "n.sources AS sources",
    )
    assert rows, f"expected at least one {label} node"
    for row in rows:
        assert row["origin"] in ("raw", "synthesized", "inferred"), row
        assert row["sources"], row


def test_document_links_to_what_it_defines(example_kb: Path) -> None:
    rows = _query(
        example_kb,
        "MATCH (d:Document)-[:DEFINES]->(m) RETURN m.id AS id ORDER BY m.id",
    )
    assert {r["id"] for r in rows} == {
        "eq-lateral-slip",
        "slip-aware-diff-drive",
        "wifg-slam",
        "wifg-slam-graph",
    }


def test_stored_code_is_checked_and_recorded(example_kb: Path) -> None:
    """The example ships checkable maths and code, checked by the build script."""
    nodes = json.loads(_kb(example_kb, "code", "list"))["nodes"]
    by_ref = {f"{n['label']}:{n['id']}": n for n in nodes}

    equation = by_ref["Equation:eq-lateral-slip"]
    assert equation["language"] == "sympy"
    assert equation["status"] == "ok"
    assert equation["checked_at"]
    assert equation["stale"] is False and equation["missing"] is False

    algorithm = by_ref["Algorithm:slip-aware-odometry-increment"]
    assert algorithm["language"] == "python"
    assert algorithm["status"] == "ok"
    assert algorithm["entry"] == "integrate"


def test_equation_keeps_latex_and_gains_a_checkable_form(example_kb: Path) -> None:
    """LaTeX-only equations stay valid; code is an enrichment, not a gate."""
    rows = _query(
        example_kb,
        "MATCH (e:Equation) RETURN e.id AS id, e.latex AS latex, "
        "e.code_language AS language, e.code_status AS status ORDER BY e.id",
    )
    by_id = {r["id"]: r for r in rows}

    checked = by_id["eq-lateral-slip"]
    assert checked["latex"], "the source's notation must be preserved"
    assert checked["language"] == "sympy"
    assert checked["status"] == "ok"

    # Deliberately LaTeX-only: extraction must never stall on missing code.
    latex_only = by_id["eq-map-objective"]
    assert latex_only["latex"]
    assert not latex_only["language"]


def test_snippet_source_is_printable_verbatim(example_kb: Path) -> None:
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "kb.cli.main",
            "code",
            "show",
            "Algorithm:slip-aware-odometry-increment",
            "--kb",
            str(example_kb),
        ],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    assert "def integrate(v: float, omega: float, alpha: float, dt: float)" in result.stdout


def test_hybrid_search_returns_a_context_bundle(example_kb: Path) -> None:
    # The limit is generous on purpose: the KB now holds enough slip-related
    # entities that the source document is no longer in the first few hits.
    bundle = json.loads(
        _kb(example_kb, "search", "slip aware wheel odometry", "--limit", "20")
    )
    assert set(bundle) >= {"query", "semantic", "fulltext"}
    refs = {hit["ref"] for hit in bundle["semantic"]}
    assert "slip-aware-diff-drive" in refs
    # The raw document is reachable from the bundle as well.
    assert any(hit["kind"] == "document" for hit in bundle["semantic"])
