"""Loading and applying schema migrations.

Migration files live in `<kb_root>/schema/migrations/` and are of two kinds:

- `NNNN_name.json`  — structured file parsed into a `Migration` model.
- `NNNN_name.cypher` — a single Cypher/DDL statement (or `;`-separated
  statements) applied verbatim. Its migration id is derived from the file
  stem.

Migrations are applied in lexicographic filename order. A dedicated
`_kb_migrations` node table records which migration ids have been applied,
so `apply` is idempotent: re-running only executes pending files.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from ..graph.connection import GraphDB
from .model import (
    CreateNodeOp,
    CreateRelOp,
    CypherOp,
    Migration,
    Schema,
    render_create_node_table,
    render_create_rel_table,
)


_MIGRATION_ID_RE = re.compile(r"^\d{4,}_[A-Za-z0-9_\-]+$")
MIGRATIONS_TABLE = "_kb_migrations"


class MigrationError(RuntimeError):
    """Raised for malformed migration files or failed application."""


@dataclass(frozen=True)
class MigrationFile:
    """A migration on disk: id, source path, and parsed content."""

    id: str
    path: Path
    migration: Migration | None  # None for raw `.cypher` files
    raw_cypher: str | None  # populated for `.cypher` files


def _validate_id(mid: str, source: Path) -> None:
    if not _MIGRATION_ID_RE.match(mid):
        raise MigrationError(
            f"invalid migration id {mid!r} (from {source.name}); "
            "expected NNNN_name (e.g. 0001_init)"
        )


def load_migration_file(path: Path) -> MigrationFile:
    """Parse a single migration file from disk."""
    if path.suffix == ".json":
        with path.open("r", encoding="utf-8") as f:
            data: dict[str, Any] = json.load(f)
        # If no id is declared, default to the file stem.
        data.setdefault("id", path.stem)
        try:
            m = Migration.model_validate(data)
        except Exception as exc:  # pydantic ValidationError
            raise MigrationError(f"invalid migration {path.name}: {exc}") from exc
        _validate_id(m.id, path)
        return MigrationFile(id=m.id, path=path, migration=m, raw_cypher=None)
    if path.suffix == ".cypher":
        mid = path.stem
        _validate_id(mid, path)
        text = path.read_text(encoding="utf-8")
        return MigrationFile(id=mid, path=path, migration=None, raw_cypher=text)
    raise MigrationError(
        f"unsupported migration file extension: {path.name} (expected .json or .cypher)"
    )


def load_migrations(migrations_dir: Path) -> list[MigrationFile]:
    """Load all migration files from `migrations_dir` in lexicographic order."""
    if not migrations_dir.is_dir():
        return []
    files: list[MigrationFile] = []
    seen_ids: set[str] = set()
    for path in sorted(migrations_dir.iterdir()):
        if path.name.startswith(".") or not path.is_file():
            continue
        if path.suffix not in (".json", ".cypher"):
            continue
        mf = load_migration_file(path)
        if mf.id in seen_ids:
            raise MigrationError(f"duplicate migration id: {mf.id}")
        seen_ids.add(mf.id)
        files.append(mf)
    return files


# --- Migration runner --------------------------------------------------------


def _ensure_migrations_table(g: GraphDB) -> None:
    if MIGRATIONS_TABLE not in g.node_table_names():
        g.execute(
            f"CREATE NODE TABLE {MIGRATIONS_TABLE}"
            "(id STRING, applied_at TIMESTAMP, PRIMARY KEY(id))"
        )


def applied_migration_ids(g: GraphDB) -> list[str]:
    """Return migration ids already recorded in the DB (empty if table absent)."""
    if MIGRATIONS_TABLE not in g.node_table_names():
        return []
    rows = g.execute(
        f"MATCH (m:{MIGRATIONS_TABLE}) RETURN m.id AS id ORDER BY m.id"
    )
    return [r["id"] for r in rows]


def _split_cypher_statements(text: str) -> list[str]:
    """Split a raw `.cypher` file into individual statements on `;`."""
    parts = [p.strip() for p in text.split(";")]
    return [p for p in parts if p]


def _apply_migration(g: GraphDB, mf: MigrationFile) -> None:
    if mf.migration is not None:
        for op in mf.migration.operations:
            if isinstance(op, CreateNodeOp):
                g.execute(render_create_node_table(op.table))
            elif isinstance(op, CreateRelOp):
                g.execute(render_create_rel_table(op.table))
            elif isinstance(op, CypherOp):
                g.execute(op.sql)
    else:
        assert mf.raw_cypher is not None
        for stmt in _split_cypher_statements(mf.raw_cypher):
            g.execute(stmt)


def apply_migrations(g: GraphDB, migrations_dir: Path) -> list[str]:
    """Apply all pending migrations. Returns the list of ids newly applied."""
    _ensure_migrations_table(g)
    on_disk = load_migrations(migrations_dir)
    already = set(applied_migration_ids(g))
    newly_applied: list[str] = []
    for mf in on_disk:
        if mf.id in already:
            continue
        _apply_migration(g, mf)
        g.execute(
            f"CREATE (:{MIGRATIONS_TABLE} "
            "{id: $id, applied_at: current_timestamp()})",
            {"id": mf.id},
        )
        newly_applied.append(mf.id)
    return newly_applied


# --- Validation --------------------------------------------------------------


def build_target_schema(migrations_dir: Path) -> Schema:
    """Fold all structural migrations into a single target `Schema`."""
    schema = Schema()
    for mf in load_migrations(migrations_dir):
        if mf.migration is not None:
            mf.migration.apply_to_schema(schema)
    return schema


@dataclass(frozen=True)
class ValidationReport:
    ok: bool
    missing_node_tables: list[str]
    missing_rel_tables: list[str]
    pending_migrations: list[str]
    extra_node_tables: list[str]
    extra_rel_tables: list[str]

    def to_dict(self) -> dict[str, Any]:
        return {
            "ok": self.ok,
            "missing_node_tables": self.missing_node_tables,
            "missing_rel_tables": self.missing_rel_tables,
            "pending_migrations": self.pending_migrations,
            "extra_node_tables": self.extra_node_tables,
            "extra_rel_tables": self.extra_rel_tables,
        }


def validate(g: GraphDB, migrations_dir: Path) -> ValidationReport:
    """Compare the DB against the target schema declared by migrations."""
    target = build_target_schema(migrations_dir)
    db_nodes = set(g.node_table_names()) - {MIGRATIONS_TABLE}
    db_rels = set(g.rel_table_names())
    want_nodes = set(target.node_type_names())
    want_rels = set(target.relation_type_names())

    applied = set(applied_migration_ids(g))
    on_disk_ids = [mf.id for mf in load_migrations(migrations_dir)]
    pending = [i for i in on_disk_ids if i not in applied]

    missing_nodes = sorted(want_nodes - db_nodes)
    missing_rels = sorted(want_rels - db_rels)
    extra_nodes = sorted(db_nodes - want_nodes)
    extra_rels = sorted(db_rels - want_rels)

    ok = not (missing_nodes or missing_rels or pending)
    return ValidationReport(
        ok=ok,
        missing_node_tables=missing_nodes,
        missing_rel_tables=missing_rels,
        pending_migrations=pending,
        extra_node_tables=extra_nodes,
        extra_rel_tables=extra_rels,
    )


# --- New migration scaffold --------------------------------------------------


def next_migration_id(migrations_dir: Path, name: str) -> str:
    """Compute the next `NNNN_name` id given the current directory contents."""
    if not re.match(r"^[A-Za-z0-9_\-]+$", name):
        raise MigrationError(f"invalid migration name: {name!r}")
    existing = load_migrations(migrations_dir) if migrations_dir.is_dir() else []
    max_num = 0
    for mf in existing:
        num = int(mf.id.split("_", 1)[0])
        max_num = max(max_num, num)
    return f"{max_num + 1:04d}_{name}"


def create_migration_file(migrations_dir: Path, name: str) -> Path:
    """Create a new empty JSON migration file and return its path."""
    migrations_dir.mkdir(parents=True, exist_ok=True)
    mid = next_migration_id(migrations_dir, name)
    path = migrations_dir / f"{mid}.json"
    scaffold = {
        "id": mid,
        "description": "",
        "operations": [],
    }
    path.write_text(json.dumps(scaffold, indent=2) + "\n", encoding="utf-8")
    return path
