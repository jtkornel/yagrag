"""Tests for `kb init` — scaffold correctness and idempotency."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from typer.testing import CliRunner

from kb.cli.main import app
from kb.config import CONFIG_FILENAME, KBConfig
from kb.init import init_kb

# --- Library-level tests --------------------------------------------------


def _expected_paths(kb_root: Path) -> list[Path]:
    return [
        kb_root,
        kb_root / CONFIG_FILENAME,
        kb_root / "documents",
        kb_root / "documents" / "raw",
        kb_root / "documents" / "synthesized",
        kb_root / "documents" / "manifest.json",
        kb_root / "schema",
        kb_root / "schema" / "migrations",
        kb_root / "code",
        kb_root / ".gitignore",
    ]


def test_init_creates_full_layout(tmp_path: Path) -> None:
    kb_root = tmp_path / "my-kb"

    result = init_kb(kb_root)

    assert result.kb_root == kb_root.resolve()
    for path in _expected_paths(kb_root):
        assert path.exists(), f"missing: {path}"

    # The manifest is valid JSON with an empty documents list.
    manifest = json.loads((kb_root / "documents" / "manifest.json").read_text())
    assert manifest == {"version": 1, "documents": []}

    # The config round-trips: what init wrote must load back cleanly.
    config = KBConfig.load(kb_root)
    assert config.name == "my-kb"
    assert config.paths.raw == "documents/raw"
    assert config.paths.graph_db == "graph.grafeo"

    # Graph DB is intentionally NOT created at init time; the graph
    # layer materializes it when the schema is first applied (Step 2).
    assert not (kb_root / config.paths.graph_db).exists()


def test_init_is_idempotent(tmp_path: Path) -> None:
    kb_root = tmp_path / "kb"

    first = init_kb(kb_root)
    # Sanity: first run created everything.
    assert first.created, "first init should create entries"
    assert not first.existed

    # User edits the config; a second init must NOT overwrite it.
    custom_config = (kb_root / CONFIG_FILENAME).read_text() + '\n# user comment\n'
    (kb_root / CONFIG_FILENAME).write_text(custom_config)

    second = init_kb(kb_root)
    assert not second.created, "second init should create nothing"
    assert second.existed, "second init should report existing entries"
    assert (kb_root / CONFIG_FILENAME).read_text() == custom_config


def test_init_uses_custom_name(tmp_path: Path) -> None:
    kb_root = tmp_path / "kb"
    init_kb(kb_root, name="sensor-fusion")

    config = KBConfig.load(kb_root)
    assert config.name == "sensor-fusion"


def test_init_rejects_file_where_directory_expected(tmp_path: Path) -> None:
    kb_root = tmp_path / "kb"
    kb_root.mkdir()
    # Place a file where a subdirectory should live.
    (kb_root / "documents").write_text("oops")

    with pytest.raises(NotADirectoryError):
        init_kb(kb_root)


# --- CLI-level tests ------------------------------------------------------


def test_cli_init_human_output(tmp_path: Path) -> None:
    runner = CliRunner()
    kb_root = tmp_path / "kb"

    result = runner.invoke(app, ["init", str(kb_root)])

    assert result.exit_code == 0, result.output
    assert (kb_root / CONFIG_FILENAME).exists()
    assert "created" in result.output.lower()


def test_cli_init_json_output(tmp_path: Path) -> None:
    runner = CliRunner()
    kb_root = tmp_path / "kb"

    result = runner.invoke(app, ["init", str(kb_root), "--json"])

    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert payload["kb_root"] == str(kb_root.resolve())
    assert CONFIG_FILENAME in payload["created"]
    assert "documents/raw" in payload["created"]
    assert payload["existed"] == []


def test_cli_init_second_run_reports_existing(tmp_path: Path) -> None:
    runner = CliRunner()
    kb_root = tmp_path / "kb"

    first = runner.invoke(app, ["init", str(kb_root), "--json"])
    assert first.exit_code == 0

    second = runner.invoke(app, ["init", str(kb_root), "--json"])
    assert second.exit_code == 0
    payload = json.loads(second.output)
    assert payload["created"] == []
    assert CONFIG_FILENAME in payload["existed"]


def test_cli_version() -> None:
    runner = CliRunner()
    result = runner.invoke(app, ["--version"])
    assert result.exit_code == 0
    assert result.output.strip().startswith("kb ")
