"""Tests for remote repository lifecycle commands (clone, pull, push, remote) and version compatibility."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from typer.testing import CliRunner

from kb import CURRENT_FORMAT_VERSION, IncompatibleKBVersionError
from kb.cli.main import app
from kb.config import CONFIG_FILENAME, KBConfig
from kb.init import init_kb
from kb.remote import run_git

runner = CliRunner()


def test_config_version_compatibility(tmp_path: Path) -> None:
    """Test that KBConfig loads matching versions and flags incompatible newer versions."""
    init_kb(tmp_path, name="compat_test")
    cfg = KBConfig.load(tmp_path)
    assert cfg.format_version == CURRENT_FORMAT_VERSION
    assert cfg.min_software_version == "0.1.0"

    # Write a future format version to kb.toml
    config_file = tmp_path / CONFIG_FILENAME
    config_file.write_text(
        f'version = 1\nformat_version = 999\nmin_software_version = "0.1.0"\nname = "future_kb"\n',
        encoding="utf-8",
    )
    with pytest.raises(IncompatibleKBVersionError, match="format version 999"):
        KBConfig.load(tmp_path)

    # Write a future software version requirement
    config_file.write_text(
        f'version = 1\nformat_version = 1\nmin_software_version = "99.0.0"\nname = "future_sw"\n',
        encoding="utf-8",
    )
    with pytest.raises(IncompatibleKBVersionError, match="requires yagrag/kb >= 99.0.0"):
        KBConfig.load(tmp_path)


def test_dump_and_restore_roundtrip(tmp_path: Path) -> None:
    """Verify that graph dump and restore accurately round-trip schema and records."""
    src_kb = tmp_path / "src_kb"
    init_kb(src_kb, name="src")
    # Copy migrations
    schema_src = Path(__file__).parent.parent / "schema"
    import shutil
    shutil.copytree(schema_src, src_kb / "schema", dirs_exist_ok=True)

    # Apply schema to source
    res_migrate = runner.invoke(app, ["schema", "apply", "--kb", str(src_kb)])
    assert res_migrate.exit_code == 0

    # Insert a test node and relationship
    res_node1 = runner.invoke(
        app,
        [
            "graph",
            "upsert-node",
            "Document",
            "--kb",
            str(src_kb),
            "--props",
            '{"id": "doc1", "title": "Test Doc", "origin": "human", "sources": ["doc1"]}',
        ],
    )
    assert res_node1.exit_code == 0

    res_node2 = runner.invoke(
        app,
        [
            "graph",
            "upsert-node",
            "Concept",
            "--kb",
            str(src_kb),
            "--props",
            '{"id": "c1", "name": "Kinematics", "origin": "human", "sources": ["doc1"]}',
        ],
    )
    assert res_node2.exit_code == 0

    # Create dump
    dump_file = tmp_path / "test_dump.json.gz"
    res_dump = runner.invoke(app, ["graph", "dump", "--kb", str(src_kb), "-o", str(dump_file)])
    assert res_dump.exit_code == 0
    assert dump_file.is_file()

    # Restore in fresh destination KB
    dest_kb = tmp_path / "dest_kb"
    init_kb(dest_kb, name="dest")
    shutil.copytree(schema_src, dest_kb / "schema", dirs_exist_ok=True)

    res_restore = runner.invoke(app, ["graph", "restore", "--kb", str(dest_kb), "-i", str(dump_file)])
    assert res_restore.exit_code == 0

    # Verify restored content in dest
    res_query = runner.invoke(
        app,
        ["graph", "query", "--kb", str(dest_kb), "--json", "MATCH (d:Document {id: 'doc1'}) RETURN d.title AS title, d.id AS id"],
    )
    assert res_query.exit_code == 0
    data = json.loads(res_query.stdout)
    # Traverse may return dict row or dict with n
    row = data[0]
    title = row.get("title") or (row.get("d") or {}).get("title")
    assert title == "Test Doc"


def test_clone_local_git_repo(tmp_path: Path) -> None:
    """Verify `kb clone` can clone a git repo containing a knowledge base."""
    # 1. Create a git repo with a knowledge base
    origin_kb = tmp_path / "origin_kb"
    init_kb(origin_kb, name="robotics_kb")

    # Initialize git repo in origin_kb
    run_git(["init"], cwd=origin_kb)
    run_git(["config", "user.name", "Test User"], cwd=origin_kb)
    run_git(["config", "user.email", "test@example.com"], cwd=origin_kb)
    run_git(["config", "commit.gpgsign", "false"], cwd=origin_kb)
    run_git(["add", "."], cwd=origin_kb)
    run_git(["commit", "--no-gpg-sign", "-m", "Initial KB commit"], cwd=origin_kb)

    # 2. Clone it using `kb clone`
    cloned_dest = tmp_path / "cloned_kb"
    res_clone = runner.invoke(app, ["clone", str(origin_kb), str(cloned_dest)])
    assert res_clone.exit_code == 0
    assert (cloned_dest / CONFIG_FILENAME).is_file()

    # Check remotes command
    res_remotes = runner.invoke(app, ["remote", "list", "--kb", str(cloned_dest), "--json"])
    assert res_remotes.exit_code == 0
    remotes_data = json.loads(res_remotes.stdout)
    assert remotes_data["ok"] is True
    assert "origin" in remotes_data["remotes"]


def test_doc_fetch_local(tmp_path: Path) -> None:
    """Verify `kb doc fetch` downloads and verifies document content from URLs."""
    kb_dir = tmp_path / "fetch_kb"
    init_kb(kb_dir, name="fetch_kb")

    # Create a local file to serve as a test remote source URL
    source_file = tmp_path / "source_doc.txt"
    source_content = b"Knowledge Base Remote Fetch Test Content\n"
    source_file.write_bytes(source_content)
    import hashlib
    h = hashlib.sha256(source_content).hexdigest()

    # Manually add manifest entry with file:// URL
    manifest_file = kb_dir / "documents" / "manifest.json"
    manifest_data = {
        "version": 1,
        "documents": [
            {
                "id": "raw-0001",
                "kind": "raw",
                "title": "Local Fetch Doc",
                "path": "documents/raw/raw-0001_source_doc.txt",
                "hash": h,
                "format": "txt",
                "added_at": "2026-09-13T00:00:00+00:00",
                "sources": [],
                "tags": [],
                "notes": "",
                "url": source_file.as_uri(),
            }
        ],
    }
    manifest_file.write_text(json.dumps(manifest_data, indent=2))

    # Test doc list before fetch shows status 'missing'
    res_list = runner.invoke(app, ["doc", "list", "--kb", str(kb_dir), "--missing", "--json"])
    assert res_list.exit_code == 0
    missing_docs = json.loads(res_list.stdout)
    assert len(missing_docs) == 1
    assert missing_docs[0]["id"] == "raw-0001"

    # Run kb doc fetch
    res_fetch = runner.invoke(app, ["doc", "fetch", "--kb", str(kb_dir), "--json"])
    assert res_fetch.exit_code == 0
    fetch_result = json.loads(res_fetch.stdout)
    assert fetch_result["ok"] is True
    assert len(fetch_result["fetched"]) == 1
    assert fetch_result["fetched"][0]["hash_matches"] is True

    # Target file should now exist on disk with correct content
    target_dest = kb_dir / "documents" / "raw" / "raw-0001_source_doc.txt"
    assert target_dest.is_file()
    assert target_dest.read_bytes() == source_content
