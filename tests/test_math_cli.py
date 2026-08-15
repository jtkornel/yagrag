"""Tests for math rendering utilities and `kb math` CLI commands."""

from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest
from typer.testing import CliRunner

from kb.cli.main import app
from kb.math.rendering import latex_to_unicode, render_sympy_2d, symbol_name_to_unicode

pytest.importorskip("grafeo")

runner = CliRunner()
REPO_ROOT = Path(__file__).resolve().parents[1]
SCHEMA_MIGRATIONS = REPO_ROOT / "schema" / "migrations"


def test_latex_to_unicode_greek_and_subscripts() -> None:
    assert "ω" in latex_to_unicode(r"\omega_z")
    assert "α" in latex_to_unicode(r"\alpha_l")
    assert "λ" in latex_to_unicode(r"\lambda")
    assert "τₛ" in latex_to_unicode(r"\tau_s")
    assert "χ" in latex_to_unicode(r"\chi")


def test_latex_to_unicode_fractions_and_roots() -> None:
    res = latex_to_unicode(r"\frac{a + b}{c - d}")
    assert "(a + b) / (c - d)" in res

    sqrt_res = latex_to_unicode(r"\sqrt{x^2 + y^2}")
    assert "√(x² + y²)" in sqrt_res


def test_latex_to_unicode_matrix_and_diacritics() -> None:
    mat = latex_to_unicode(r"\begin{bmatrix} a & b \\ c & d \end{bmatrix}")
    assert "[" in mat and "]" in mat and ";" in mat

    dot = latex_to_unicode(r"\dot{x} + \ddot{y} + \hat{z}")
    assert "ẋ" in dot
    assert "ÿ" in dot
    assert "ẑ" in dot


def test_symbol_name_to_unicode() -> None:
    assert symbol_name_to_unicode("omega_z") == "ω_z"
    assert symbol_name_to_unicode("alpha_l") == "αₗ"
    assert symbol_name_to_unicode(r"\omega_z") == "ω_z"
    assert symbol_name_to_unicode("tau_s") == "τₛ"


def test_render_sympy_2d(tmp_path: Path) -> None:
    # 1. Nonexistent path returns None
    assert render_sympy_2d(tmp_path / "missing.sympy") is None

    # 2. Valid sympy snippet
    sympy_file = tmp_path / "test_eq.sympy"
    sympy_file.write_text("Eq(omega_z, (v_r - v_l) / b)\n", encoding="utf-8")
    rendered = render_sympy_2d(sympy_file)
    assert rendered is not None
    assert "ω_z" in rendered or "omega_z" in rendered or "=" in rendered

    # 3. Invalid syntax returns None
    broken_file = tmp_path / "broken.sympy"
    broken_file.write_text("Eq(omega_z, +++ syntax error\n", encoding="utf-8")
    assert render_sympy_2d(broken_file) is None


@pytest.fixture
def seeded_math_kb(tmp_path: Path) -> Path:
    """Fixture that initializes a KB with the domain schema and batch_raw0010 data."""
    kb_dir = tmp_path / "test_kb"
    res = runner.invoke(app, ["init", str(kb_dir)])
    assert res.exit_code == 0

    # Copy migrations and apply
    mig_dir = kb_dir / "schema" / "migrations"
    mig_dir.mkdir(parents=True, exist_ok=True)
    for mig_file in sorted(SCHEMA_MIGRATIONS.glob("*.gql")):
        shutil.copy(mig_file, mig_dir / mig_file.name)

    res_mig = runner.invoke(app, ["schema", "apply", "--kb", str(kb_dir)])
    assert res_mig.exit_code == 0

    # Upsert Document
    runner.invoke(
        app,
        [
            "doc",
            "add",
            "--id",
            "raw-0010",
            "--name",
            "Visual-based Kinematics for Skid-Steer",
            "--kind",
            "raw",
            "--format",
            "md",
            "--kb",
            str(kb_dir),
        ],
    )

    # Upsert Equation 1 with sympy snippet (forward kinematics)
    eq1_sympy = kb_dir / "code" / "equations" / "raw-0010" / "skid_steer_forward_kinematics.sympy"
    eq1_sympy.parent.mkdir(parents=True, exist_ok=True)
    eq1_sympy.write_text("Eq(omega_z, (alpha_r * o_r - alpha_l * o_l) / (Y_l - Y_r))\n", encoding="utf-8")

    res_eq1 = runner.invoke(
        app,
        [
            "graph",
            "upsert-node",
            "Equation",
            "--props",
            json.dumps({
                "id": "eq_skid_steer_forward_kinematics",
                "origin": "raw",
                "sources": ["raw-0010"],
                "name": "ICR Forward Kinematics",
                "summary": "Skid steer kinematic relationship",
                "latex": r"\omega_z = \frac{\alpha_r o_r - \alpha_l o_l}{Y_l - Y_r}",
                "code_path": "code/equations/raw-0010/skid_steer_forward_kinematics.sympy",
                "code_language": "sympy",
            }),
            "--kb",
            str(kb_dir),
        ],
    )
    assert res_eq1.exit_code == 0, res_eq1.output

    # Upsert Equation 2 (upstream definition for track width / Y coordinates)
    eq2_sympy = kb_dir / "code" / "equations" / "raw-0010" / "track_width_init.sympy"
    eq2_sympy.write_text("Eq(Y_l, b / 2)\nEq(Y_r, -b / 2)\n", encoding="utf-8")

    res_eq2 = runner.invoke(
        app,
        [
            "graph",
            "upsert-node",
            "Equation",
            "--props",
            json.dumps({
                "id": "eq_track_width_init",
                "origin": "raw",
                "sources": ["raw-0010"],
                "name": "Track Width ICR Initialization",
                "summary": "Initialize ICR Y coordinates from nominal track width",
                "latex": r"Y_l = \frac{b}{2}, \quad Y_r = -\frac{b}{2}",
                "code_path": "code/equations/raw-0010/track_width_init.sympy",
                "code_language": "sympy",
            }),
            "--kb",
            str(kb_dir),
        ],
    )
    assert res_eq2.exit_code == 0, res_eq2.output

    # Upsert Quantities
    for qid, sym, name, unit in [
        ("qty_raw0010_omega_z", "omega_z", "Yaw Angular Velocity", "rad/s"),
        ("qty_raw0010_alpha_r", "alpha_r", "Right Wheel Correction", "-"),
        ("qty_raw0010_alpha_l", "alpha_l", "Left Wheel Correction", "-"),
        ("qty_raw0010_o_r", "o_r", "Right Wheel Speed", "m/s"),
        ("qty_raw0010_o_l", "o_l", "Left Wheel Speed", "m/s"),
        ("qty_raw0010_Y_r", "Y_r", "Right ICR Y", "m"),
        ("qty_raw0010_Y_l", "Y_l", "Left ICR Y", "m"),
        ("qty_raw0010_b", "b", "Nominal Track Width", "m"),
    ]:
        res_q = runner.invoke(
            app,
            [
                "graph",
                "upsert-node",
                "Quantity",
                "--props",
                json.dumps({
                    "id": qid,
                    "origin": "raw",
                    "sources": ["raw-0010"],
                    "symbol": sym,
                    "name": name,
                    "unit": unit,
                }),
                "--kb",
                str(kb_dir),
            ],
        )
        assert res_q.exit_code == 0, res_q.output

    # Upsert Model
    res_m = runner.invoke(
        app,
        [
            "graph",
            "upsert-node",
            "MotionModel",
            "--props",
            json.dumps({
                "id": "model_skid_steer_icr",
                "origin": "raw",
                "sources": ["raw-0010"],
                "name": "ICR Skid Steer Model",
                "summary": "5-parameter ICR model",
            }),
            "--kb",
            str(kb_dir),
        ],
    )
    assert res_m.exit_code == 0, res_m.output

    # Connect edges
    # Output Quantity DEFINED_BY Equation 1
    runner.invoke(
        app,
        [
            "graph",
            "upsert-edge",
            "DEFINED_BY",
            "--from",
            "Quantity:qty_raw0010_omega_z",
            "--to",
            "Equation:eq_skid_steer_forward_kinematics",
            "--props",
            json.dumps({"origin": "raw", "sources": ["raw-0010"]}),
            "--kb",
            str(kb_dir),
        ],
    )

    # Output Quantities DEFINED_BY Equation 2
    for y_qid in ("qty_raw0010_Y_l", "qty_raw0010_Y_r"):
        runner.invoke(
            app,
            [
                "graph",
                "upsert-edge",
                "DEFINED_BY",
                "--from",
                f"Quantity:{y_qid}",
                "--to",
                "Equation:eq_track_width_init",
                "--props",
                json.dumps({"origin": "raw", "sources": ["raw-0010"]}),
                "--kb",
                str(kb_dir),
            ],
        )

    # Equation 2 USES_SYMBOL qty_raw0010_b
    runner.invoke(
        app,
        [
            "graph",
            "upsert-edge",
            "USES_SYMBOL",
            "--from",
            "Equation:eq_track_width_init",
            "--to",
            "Quantity:qty_raw0010_b",
            "--props",
            json.dumps({"origin": "raw", "sources": ["raw-0010"]}),
            "--kb",
            str(kb_dir),
        ],
    )

    # Model DEFINED_BY Equation 1
    runner.invoke(
        app,
        [
            "graph",
            "upsert-edge",
            "DEFINED_BY",
            "--from",
            "MotionModel:model_skid_steer_icr",
            "--to",
            "Equation:eq_skid_steer_forward_kinematics",
            "--props",
            json.dumps({"origin": "raw", "sources": ["raw-0010"]}),
            "--kb",
            str(kb_dir),
        ],
    )

    # Equation 1 USES_SYMBOL inputs
    for inp in [
        "qty_raw0010_alpha_r",
        "qty_raw0010_alpha_l",
        "qty_raw0010_o_r",
        "qty_raw0010_o_l",
        "qty_raw0010_Y_r",
        "qty_raw0010_Y_l",
    ]:
        runner.invoke(
            app,
            [
                "graph",
                "upsert-edge",
                "USES_SYMBOL",
                "--from",
                "Equation:eq_skid_steer_forward_kinematics",
                "--to",
                f"Quantity:{inp}",
                "--props",
                json.dumps({"origin": "raw", "sources": ["raw-0010"]}),
                "--kb",
                str(kb_dir),
            ],
        )

    return kb_dir


def test_cli_math_derive_quantity_symbol_and_id(seeded_math_kb: Path) -> None:
    # 1. Derive by symbol name
    res = runner.invoke(app, ["math", "derive", "omega_z", "--kb", str(seeded_math_kb)])
    assert res.exit_code == 0, res.output
    assert "ω_z" in res.stdout
    assert "ICR Forward Kinematics" in res.stdout
    assert "Input Symbols" in res.stdout

    # 2. Derive by node ID with --json (depth=1)
    res_json = runner.invoke(
        app,
        ["math", "derive", "qty_raw0010_omega_z", "--kb", str(seeded_math_kb), "--json"],
    )
    assert res_json.exit_code == 0, res_json.output
    data = json.loads(res_json.stdout)
    assert data["found"] is True
    assert len(data["results"]) == 1
    assert data["results"][0]["id"] == "qty_raw0010_omega_z"
    assert len(data["results"][0]["derivations"]) == 1
    assert data["results"][0]["derivations"][0]["id"] == "eq_skid_steer_forward_kinematics"
    assert len(data["results"][0]["derivations"][0]["inputs"]) == 6


def test_cli_math_derive_multi_depth(seeded_math_kb: Path) -> None:
    # Derive with depth=2 to trace upstream definitions of Y_l and Y_r
    res_json = runner.invoke(
        app,
        ["math", "derive", "qty_raw0010_omega_z", "--depth", "2", "--kb", str(seeded_math_kb), "--json"],
    )
    assert res_json.exit_code == 0, res_json.output
    data = json.loads(res_json.stdout)
    inputs = data["results"][0]["derivations"][0]["inputs"]
    y_l_inp = next(i for i in inputs if i["id"] == "qty_raw0010_Y_l")
    assert len(y_l_inp["upstream_derivations"]) == 1
    assert y_l_inp["upstream_derivations"][0]["id"] == "eq_track_width_init"

    # Human readable output with depth=2
    res = runner.invoke(
        app,
        ["math", "derive", "qty_raw0010_omega_z", "--depth", "2", "--kb", str(seeded_math_kb)],
    )
    assert res.exit_code == 0, res.output
    assert "Upstream for" in res.stdout
    assert "eq_track_width_init" in res.stdout


def test_cli_math_derive_equation(seeded_math_kb: Path) -> None:
    # JSON mode
    res = runner.invoke(
        app,
        ["math", "derive", "eq_skid_steer_forward_kinematics", "--kb", str(seeded_math_kb), "--json"],
    )
    assert res.exit_code == 0, res.output
    data = json.loads(res.stdout)
    assert data["found"] is True
    assert data["results"][0]["id"] == "eq_skid_steer_forward_kinematics"
    assert len(data["results"][0]["outputs"]) == 1
    assert len(data["results"][0]["inputs"]) == 6

    # Human-readable mode (default: SymPy 2D rendered, no raw LaTeX)
    res_hr = runner.invoke(
        app,
        ["math", "derive", "eq_skid_steer_forward_kinematics", "--kb", str(seeded_math_kb)],
    )
    assert res_hr.exit_code == 0, res_hr.output
    assert "ICR Forward Kinematics" in res_hr.stdout
    assert "SymPy 2D" in res_hr.stdout
    assert "Defined Output(s):" in res_hr.stdout
    assert r"\frac" not in res_hr.stdout

    # Human-readable mode with --latex
    res_latex = runner.invoke(
        app,
        ["math", "derive", "eq_skid_steer_forward_kinematics", "--kb", str(seeded_math_kb), "--latex"],
    )
    assert res_latex.exit_code == 0, res_latex.output
    assert "LaTeX:" in res_latex.stdout
    assert r"\frac" in res_latex.stdout


def test_cli_math_derive_model(seeded_math_kb: Path) -> None:
    # JSON mode
    res = runner.invoke(
        app,
        ["math", "derive", "model_skid_steer_icr", "--kb", str(seeded_math_kb), "--json"],
    )
    assert res.exit_code == 0, res.output
    data = json.loads(res.stdout)
    assert data["found"] is True
    assert data["results"][0]["id"] == "model_skid_steer_icr"
    assert len(data["results"][0]["equations"]) == 1

    # Human-readable mode
    res_hr = runner.invoke(
        app,
        ["math", "derive", "model_skid_steer_icr", "--kb", str(seeded_math_kb)],
    )
    assert res_hr.exit_code == 0, res_hr.output
    assert "Model Derivation" in res_hr.stdout
    assert "Equation:" in res_hr.stdout


def test_cli_math_derive_not_found(seeded_math_kb: Path) -> None:
    res = runner.invoke(
        app,
        ["math", "derive", "nonexistent_symbol", "--kb", str(seeded_math_kb), "--json"],
    )
    assert res.exit_code != 0
    data = json.loads(res.stdout)
    assert "error" in data


def test_cli_math_glossary_all_and_filtered(seeded_math_kb: Path) -> None:
    # 1. Full glossary (default: clean ID without raw LaTeX strings)
    res = runner.invoke(app, ["math", "glossary", "--kb", str(seeded_math_kb)])
    assert res.exit_code == 0, res.output
    assert "ω_z" in res.stdout
    assert "rad/s" in res.stdout
    assert "Angular" in res.stdout

    # Full glossary with --latex
    res_latex = runner.invoke(app, ["math", "glossary", "--kb", str(seeded_math_kb), "--latex"])
    assert res_latex.exit_code == 0, res_latex.output
    assert "LaTeX / ID" in res_latex.stdout

    # 2. JSON glossary filtered by doc
    res_doc = runner.invoke(
        app,
        ["math", "glossary", "--doc", "raw-0010", "--kb", str(seeded_math_kb), "--json"],
    )
    assert res_doc.exit_code == 0, res_doc.output
    data = json.loads(res_doc.stdout)
    assert data["count"] == 8
    assert any(s["id"] == "qty_raw0010_omega_z" for s in data["symbols"])

    # 3. Filter by model
    res_mod = runner.invoke(
        app,
        ["math", "glossary", "--model", "model_skid_steer_icr", "--kb", str(seeded_math_kb), "--json"],
    )
    assert res_mod.exit_code == 0, res_mod.output
    data_mod = json.loads(res_mod.stdout)
    assert data_mod["count"] >= 1
    assert any(s["id"] == "qty_raw0010_omega_z" for s in data_mod["symbols"])

    # 4. Filter by non-existent doc
    res_empty = runner.invoke(
        app,
        ["math", "glossary", "--doc", "raw-9999", "--kb", str(seeded_math_kb), "--json"],
    )
    assert res_empty.exit_code == 0, res_empty.output
    data_empty = json.loads(res_empty.stdout)
    assert data_empty["count"] == 0
