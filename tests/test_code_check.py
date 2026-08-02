"""Tests for the `kb code` group and the static checker (Step 7).

Covers: ok/failed snippets, missing files, stale hashes, symbol consistency,
graceful ruff handling, empty knowledge bases, and the JSON output shapes.
The overriding rule under test is that a snippet failure is *data*, never a
process failure.
"""

from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest
from typer.testing import CliRunner

from kb.cli.main import app
from kb.code import checker as checker_mod
from kb.code import (
    STATUS_FAILED,
    STATUS_OK,
    STATUS_UNCHECKED,
    CodeError,
    list_code_nodes,
)
from kb.config import KBConfig
from kb.graph.connection import open_graph

runner = CliRunner()

pytest.importorskip("kuzu")

REPO_ROOT = Path(__file__).resolve().parents[1]
SEED_MIGRATION = REPO_ROOT / "schema" / "migrations" / "0001_seed_domain.json"


# --- fixtures -----------------------------------------------------------------


@pytest.fixture()
def kb_root(tmp_path: Path) -> Path:
    """A KB with the seed schema applied and a couple of provenance anchors."""
    root = tmp_path / "kb"
    assert runner.invoke(app, ["init", str(root)]).exit_code == 0
    shutil.copy(SEED_MIGRATION, root / "schema" / "migrations" / SEED_MIGRATION.name)
    assert runner.invoke(app, ["schema", "apply", "--kb", str(root)]).exit_code == 0
    return root


def _upsert(kb_root: Path, label: str, props: dict) -> None:
    result = runner.invoke(
        app,
        ["graph", "upsert-node", label, "--props", json.dumps(props), "--kb", str(kb_root)],
    )
    assert result.exit_code == 0, result.output


def _prov(extra: dict | None = None) -> dict:
    props = {"origin": "raw", "sources": ["raw-0001"]}
    props.update(extra or {})
    return props


def _write_code(kb_root: Path, rel: str, content: str) -> str:
    path = kb_root / "code" / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    return f"code/{rel}"


def _check(kb_root: Path, *args: str) -> dict:
    result = runner.invoke(app, ["code", "check", "--kb", str(kb_root), "--json", *args])
    assert result.exit_code == 0, result.output
    return json.loads(result.stdout)


def _by_ref(payload: dict) -> dict[str, dict]:
    return {f"{r['label']}:{r['id']}": r for r in payload["results"]}


# --- empty KB -----------------------------------------------------------------


def test_check_on_empty_kb_exits_zero(kb_root: Path) -> None:
    payload = _check(kb_root)
    assert payload["checked"] == 0
    assert payload["results"] == []


def test_list_on_empty_kb_exits_zero(kb_root: Path) -> None:
    result = runner.invoke(app, ["code", "list", "--kb", str(kb_root), "--json"])
    assert result.exit_code == 0, result.output
    assert json.loads(result.stdout)["nodes"] == []


# --- python snippets ----------------------------------------------------------


GOOD_PYTHON = '''"""Wheel-odometry increment for a skid-drive UGV."""

import numpy as np


def integrate(v: float, omega: float, dt: float) -> np.ndarray:
    return np.array([v * dt, 0.0, omega * dt])
'''

BAD_PYTHON = "def integrate(v, omega, dt)\n    return v * dt\n"


def test_valid_python_snippet_is_ok(kb_root: Path) -> None:
    rel = _write_code(kb_root, "algorithms/odometry.py", GOOD_PYTHON)
    _upsert(
        kb_root,
        "Algorithm",
        _prov(
            {
                "id": "wheel-odometry-integration",
                "name": "Wheel odometry integration",
                "code_language": "python",
                "code_path": rel,
                "code_entry": "integrate",
            }
        ),
    )

    payload = _check(kb_root)

    entry = _by_ref(payload)["Algorithm:wheel-odometry-integration"]
    assert entry["status"] == STATUS_OK
    assert entry["errors"] == []
    assert "ast" in entry["checker"]
    assert payload["ok"] == 1


def test_broken_python_snippet_is_failed_but_write_succeeded(kb_root: Path) -> None:
    rel = _write_code(kb_root, "algorithms/broken.py", BAD_PYTHON)
    _upsert(
        kb_root,
        "Algorithm",
        _prov({"id": "broken-alg", "code_language": "python", "code_path": rel}),
    )

    payload = _check(kb_root)

    entry = _by_ref(payload)["Algorithm:broken-alg"]
    assert entry["status"] == STATUS_FAILED
    assert any("syntax error" in e for e in entry["errors"])
    assert payload["failed"] == 1

    # The node itself is still present: a failed check never blocks a write.
    config = KBConfig.load(kb_root)
    with open_graph(kb_root / config.paths.graph_db) as g:
        nodes = {n.id: n for n in list_code_nodes(g)}
    assert nodes["broken-alg"].status == STATUS_FAILED


def test_status_is_persisted_on_the_node(kb_root: Path) -> None:
    rel = _write_code(kb_root, "algorithms/ok.py", GOOD_PYTHON)
    _upsert(
        kb_root,
        "Algorithm",
        _prov({"id": "persisted", "code_language": "python", "code_path": rel}),
    )
    _check(kb_root)

    config = KBConfig.load(kb_root)
    with open_graph(kb_root / config.paths.graph_db) as g:
        node = list_code_nodes(g, labels=["Algorithm"], node_id="persisted")[0]
    assert node.status == STATUS_OK
    assert node.checked_at
    assert node.stored_hash == checker_mod.content_hash(GOOD_PYTHON)


def test_lint_reports_findings_or_skips_gracefully(kb_root: Path) -> None:
    # An unused import is a ruff finding but valid Python.
    rel = _write_code(kb_root, "algorithms/lint.py", "import os\n\n\ndef f():\n    return 1\n")
    _upsert(
        kb_root,
        "Algorithm",
        _prov({"id": "lint-me", "code_language": "python", "code_path": rel}),
    )

    payload = _check(kb_root, "--lint")

    entry = _by_ref(payload)["Algorithm:lint-me"]
    # Lint findings are warnings, never failures.
    assert entry["status"] == STATUS_OK
    if shutil.which("ruff"):
        assert "ruff" in entry["checker"]
        assert entry["warnings"], "expected ruff to flag the unused import"
    else:
        assert any("ruff is not installed" in w for w in entry["warnings"])


# --- sympy snippets -----------------------------------------------------------


def _seed_symbols(kb_root: Path) -> None:
    _upsert(
        kb_root,
        "Quantity",
        _prov({"id": "range-measurement", "name": "range", "symbol": "z"}),
    )
    _upsert(kb_root, "Variable", _prov({"id": "x_i", "name": "x_i", "domain": "SE(2)"}))


def test_sympy_expression_with_known_symbols_is_ok(kb_root: Path) -> None:
    _seed_symbols(kb_root)
    rel = _write_code(kb_root, "equations/residual.sympy", "# range residual\nEq(z, x_i)\n")
    _upsert(
        kb_root,
        "Equation",
        _prov(
            {
                "id": "range-residual",
                "latex": r"r = z - h(x_i)",
                "code_language": "sympy",
                "code_path": rel,
            }
        ),
    )

    entry = _by_ref(_check(kb_root))["Equation:range-residual"]
    assert entry["status"] == STATUS_OK
    assert entry["warnings"] == []


def test_unknown_sympy_symbol_is_a_warning_not_a_failure(kb_root: Path) -> None:
    _seed_symbols(kb_root)
    rel = _write_code(kb_root, "equations/unknown.sympy", "Eq(z, mystery_term)\n")
    _upsert(
        kb_root,
        "Equation",
        _prov({"id": "unknown-symbol", "code_language": "sympy", "code_path": rel}),
    )

    entry = _by_ref(_check(kb_root))["Equation:unknown-symbol"]
    assert entry["status"] == STATUS_OK
    assert any("mystery_term" in w for w in entry["warnings"])


def test_equation_number_in_comments_issues_warning(kb_root: Path) -> None:
    _seed_symbols(kb_root)
    rel = _write_code(
        kb_root,
        "equations/paper_ref.sympy",
        "# GMM weight update (equation 7)\nEq(z, x_i)\n",
    )
    _upsert(
        kb_root,
        "Equation",
        _prov({"id": "paper-ref-eq", "code_language": "sympy", "code_path": rel}),
    )

    entry = _by_ref(_check(kb_root))["Equation:paper-ref-eq"]
    assert entry["status"] == STATUS_OK
    assert any("paper-specific equation reference" in w for w in entry["warnings"])


def test_malformed_sympy_expression_is_failed(kb_root: Path) -> None:
    _seed_symbols(kb_root)
    rel = _write_code(kb_root, "equations/bad.sympy", "Eq(z, x_i +\n")
    _upsert(
        kb_root,
        "Equation",
        _prov({"id": "bad-equation", "code_language": "sympy", "code_path": rel}),
    )

    entry = _by_ref(_check(kb_root))["Equation:bad-equation"]
    assert entry["status"] == STATUS_FAILED
    assert any("sympy parse error" in e for e in entry["errors"])


def test_sympy_expression_is_never_executed(kb_root: Path) -> None:
    """A hostile-looking expression must not reach the interpreter's builtins."""
    canary = kb_root / "canary.txt"
    rel = _write_code(
        kb_root,
        "equations/hostile.sympy",
        f'Eq(z, open("{canary}", "w"))\n',
    )
    _upsert(
        kb_root,
        "Equation",
        _prov({"id": "hostile", "code_language": "sympy", "code_path": rel}),
    )

    _check(kb_root)

    assert not canary.exists()


# --- failure and staleness edge cases ----------------------------------------


def test_missing_file_is_failed_without_traceback(kb_root: Path) -> None:
    _upsert(
        kb_root,
        "Equation",
        _prov(
            {
                "id": "dangling",
                "code_language": "sympy",
                "code_path": "code/equations/gone.sympy",
            }
        ),
    )

    entry = _by_ref(_check(kb_root))["Equation:dangling"]
    assert entry["status"] == STATUS_FAILED
    assert any("does not exist" in e for e in entry["errors"])


def test_unknown_language_is_unchecked(kb_root: Path) -> None:
    rel = _write_code(kb_root, "equations/mystery.dat", "whatever\n")
    _upsert(
        kb_root,
        "Equation",
        _prov({"id": "mystery-lang", "code_language": "matlab", "code_path": rel}),
    )

    entry = _by_ref(_check(kb_root))["Equation:mystery-lang"]
    assert entry["status"] == STATUS_UNCHECKED


def test_editing_the_file_marks_the_status_stale(kb_root: Path) -> None:
    rel = _write_code(kb_root, "algorithms/edited.py", GOOD_PYTHON)
    _upsert(
        kb_root,
        "Algorithm",
        _prov({"id": "edited", "code_language": "python", "code_path": rel}),
    )
    _check(kb_root)

    listing = runner.invoke(app, ["code", "list", "--kb", str(kb_root), "--json"])
    assert json.loads(listing.stdout)["nodes"][0]["stale"] is False

    (kb_root / rel).write_text(GOOD_PYTHON + "\n# revised\n", encoding="utf-8")

    listing = runner.invoke(app, ["code", "list", "--kb", str(kb_root), "--json"])
    assert listing.exit_code == 0, listing.output
    assert json.loads(listing.stdout)["nodes"][0]["stale"] is True


# --- list / show --------------------------------------------------------------


def test_list_reports_every_code_node_and_filters_by_status(kb_root: Path) -> None:
    _write_code(kb_root, "algorithms/a.py", GOOD_PYTHON)
    _write_code(kb_root, "algorithms/b.py", BAD_PYTHON)
    _upsert(
        kb_root,
        "Algorithm",
        _prov({"id": "alg-ok", "code_language": "python", "code_path": "code/algorithms/a.py"}),
    )
    _upsert(
        kb_root,
        "Algorithm",
        _prov({"id": "alg-bad", "code_language": "python", "code_path": "code/algorithms/b.py"}),
    )
    _check(kb_root)

    result = runner.invoke(app, ["code", "list", "--kb", str(kb_root), "--json"])
    assert result.exit_code == 0
    assert {n["id"] for n in json.loads(result.stdout)["nodes"]} == {"alg-ok", "alg-bad"}

    result = runner.invoke(
        app, ["code", "list", "--kb", str(kb_root), "--json", "--status", "failed"]
    )
    assert result.exit_code == 0
    assert [n["id"] for n in json.loads(result.stdout)["nodes"]] == ["alg-bad"]


def test_list_rejects_an_unknown_status(kb_root: Path) -> None:
    result = runner.invoke(app, ["code", "list", "--kb", str(kb_root), "--status", "bogus"])
    assert result.exit_code == 2


def test_show_prints_the_snippet_verbatim(kb_root: Path) -> None:
    rel = _write_code(kb_root, "algorithms/shown.py", GOOD_PYTHON)
    _upsert(
        kb_root,
        "Algorithm",
        _prov({"id": "shown", "code_language": "python", "code_path": rel}),
    )

    result = runner.invoke(app, ["code", "show", "Algorithm:shown", "--kb", str(kb_root)])
    assert result.exit_code == 0, result.output
    assert "def integrate(v: float, omega: float, dt: float) -> np.ndarray:" in result.stdout


def test_show_rejects_a_bad_reference(kb_root: Path) -> None:
    assert runner.invoke(app, ["code", "show", "nonsense", "--kb", str(kb_root)]).exit_code == 2
    assert (
        runner.invoke(app, ["code", "show", "Algorithm:nope", "--kb", str(kb_root)]).exit_code
        == 2
    )


# --- library-level guards -----------------------------------------------------


def test_list_code_nodes_rejects_a_non_code_label(kb_root: Path) -> None:
    config = KBConfig.load(kb_root)
    with open_graph(kb_root / config.paths.graph_db) as g:
        with pytest.raises(CodeError):
            list_code_nodes(g, labels=["Concept"])


def test_absolute_code_path_is_rejected(kb_root: Path) -> None:
    config = KBConfig.load(kb_root)
    with pytest.raises(CodeError):
        checker_mod.resolve_code_path(kb_root, config, "/etc/passwd")
