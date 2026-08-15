"""Loading and applying schema migrations.

Migration files live in `<kb_root>/schema/migrations/` and are of the following kinds:

- `NNNN_name.gql` or `NNNN_name.cypher` — standard ISO GQL / openCypher DDL statements
  separated by `;`.
- `NNNN_name.json`  — structured legacy file parsed into a `Migration` model.

Migrations are applied in lexicographic filename order. A dedicated
`_kb_migrations` node type records which migration ids have been applied,
so `apply` is idempotent: re-running only executes pending files.
"""

from __future__ import annotations

import contextlib
import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from ..graph.connection import GraphDB
from .model import (
    AddRelPairOp,
    CreateNodeOp,
    CreateRelOp,
    CypherOp,
    Migration,
    NodeType,
    Property,
    RelationType,
    Schema,
    render_create_edge_type_grafeo,
    render_create_node_type_grafeo,
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
    migration: Migration | None  # None for raw `.gql`/`.cypher` files
    raw_cypher: str | None  # populated for `.gql`/`.cypher` files


def _validate_id(mid: str, source: Path) -> None:
    if not _MIGRATION_ID_RE.match(mid):
        raise MigrationError(
            f"invalid migration id {mid!r} (from {source.name}); "
            "expected NNNN_name (e.g. 0001_init)"
        )


def _clean_statement(stmt: str) -> str:
    lines = []
    for line in stmt.splitlines():
        line_s = line.strip()
        if line_s.startswith(("--", "//")):
            continue
        lines.append(line)
    return "\n".join(lines).strip()


def _split_cypher_statements(text: str) -> list[str]:
    """Split a raw `.gql`/`.cypher` file into individual statements on `;`."""
    parts = text.split(";")
    stmts = []
    for p in parts:
        cleaned = _clean_statement(p)
        if cleaned:
            stmts.append(cleaned)
    return stmts


def load_migration_file(path: Path) -> MigrationFile:
    """Parse a single migration file from disk."""
    if path.suffix in (".gql", ".cypher"):
        mid = path.stem
        _validate_id(mid, path)
        text = path.read_text(encoding="utf-8")
        return MigrationFile(id=mid, path=path, migration=None, raw_cypher=text)
    if path.suffix == ".json":
        with path.open("r", encoding="utf-8") as f:
            data: dict[str, Any] = json.load(f)
        data.setdefault("id", path.stem)
        try:
            m = Migration.model_validate(data)
        except Exception as exc:
            raise MigrationError(f"invalid migration {path.name}: {exc}") from exc
        _validate_id(m.id, path)
        return MigrationFile(id=m.id, path=path, migration=m, raw_cypher=None)
    raise MigrationError(
        f"unsupported migration file extension: {path.name} (expected .gql, .cypher, or .json)"
    )


def load_migrations(migrations_dir: Path) -> list[MigrationFile]:
    """Load all migration files from `migrations_dir` in lexicographic order."""
    if not migrations_dir.is_dir():
        return []
    files: list[MigrationFile] = []
    seen_ids: dict[str, Path] = {}
    for path in sorted(migrations_dir.iterdir()):
        if path.name.startswith(".") or not path.is_file():
            continue
        if path.suffix not in (".gql", ".cypher", ".json"):
            continue
        mf = load_migration_file(path)
        if mf.id in seen_ids:
            # If both .gql and .json exist with the same id, .gql takes precedence
            prev_path = seen_ids[mf.id]
            if prev_path.suffix == ".json" and path.suffix in (".gql", ".cypher"):
                files = [f for f in files if f.id != mf.id]
                seen_ids[mf.id] = path
                files.append(mf)
                continue
            if path.suffix == ".json" and prev_path.suffix in (".gql", ".cypher"):
                continue
            raise MigrationError(f"duplicate migration id: {mf.id}")
        seen_ids[mf.id] = path
        files.append(mf)
    return files


# --- Migration runner --------------------------------------------------------


def _ensure_migrations_table(g: GraphDB) -> None:
    if MIGRATIONS_TABLE not in g.node_table_names():
        with contextlib.suppress(Exception):
            g.execute(
                f"CREATE NODE TYPE {MIGRATIONS_TABLE} (id STRING, applied_at TIMESTAMP)"
            )


def applied_migration_ids(g: GraphDB) -> list[str]:
    """Return migration ids already recorded in the DB (empty if table absent)."""
    try:
        rows = g.execute(
            f"MATCH (m:{MIGRATIONS_TABLE}) RETURN m.id AS id ORDER BY m.id"
        )
        return [r["id"] for r in rows if r.get("id")]
    except Exception:  # noqa: BLE001
        return []


def _apply_migration(g: GraphDB, mf: MigrationFile) -> None:
    if mf.migration is not None:
        for op in mf.migration.operations:
            if isinstance(op, CreateNodeOp):
                with contextlib.suppress(Exception):
                    g.execute(render_create_node_type_grafeo(op.table))
            elif isinstance(op, CreateRelOp):
                with contextlib.suppress(Exception):
                    g.execute(render_create_edge_type_grafeo(op.table))
            elif isinstance(op, AddRelPairOp):
                pass
            elif isinstance(op, CypherOp):
                for stmt in _split_cypher_statements(op.sql):
                    g.execute(stmt)
    else:
        assert mf.raw_cypher is not None
        for stmt in _split_cypher_statements(mf.raw_cypher):
            with contextlib.suppress(Exception):
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


# --- Validation & Target Schema Builder --------------------------------------


def _parse_properties_str(props_str: str) -> list[Property]:
    props: list[Property] = []
    if not props_str:
        return props
    for part in props_str.split(","):
        part = part.strip()
        if not part:
            continue
        pieces = part.split(None, 1)
        col_name = pieces[0]
        col_type = pieces[1] if len(pieces) > 1 else "STRING"
        is_pk = (col_name == "id")
        props.append(Property(name=col_name, type=col_type, primary_key=is_pk))
    return props


def build_target_schema(migrations_dir: Path) -> Schema:
    """Fold all migrations into a single target `Schema` using an ephemeral GrafeoDB session."""
    import grafeo  # type: ignore[import-not-found]

    db = grafeo.GrafeoDB()
    for mf in load_migrations(migrations_dir):
        if mf.raw_cypher is not None:
            for stmt in _split_cypher_statements(mf.raw_cypher):
                with contextlib.suppress(Exception):
                    db.execute(stmt)
        elif mf.migration is not None:
            for op in mf.migration.operations:
                if isinstance(op, CreateNodeOp):
                    with contextlib.suppress(Exception):
                        db.execute(render_create_node_type_grafeo(op.table))
                elif isinstance(op, CreateRelOp):
                    with contextlib.suppress(Exception):
                        db.execute(render_create_edge_type_grafeo(op.table))
                elif isinstance(op, AddRelPairOp):
                    pass
                elif isinstance(op, CypherOp):
                    for stmt in _split_cypher_statements(op.sql):
                        with contextlib.suppress(Exception):
                            db.execute(stmt)

    node_types: list[NodeType] = []
    for r in db.execute("SHOW NODE TYPES"):
        name = str(r.get("name", "")).strip()
        if not name or name == MIGRATIONS_TABLE:
            continue
        props_str = str(r.get("properties", "")).strip()
        props = _parse_properties_str(props_str)
        # Ensure id primary key if missing
        if not any(p.primary_key for p in props):
            props.insert(0, Property(name="id", type="STRING", primary_key=True))
        node_types.append(NodeType(name=name, properties=props, include_common=False))

    relation_types: list[RelationType] = []
    for r in db.execute("SHOW EDGE TYPES"):
        name = str(r.get("name", "")).strip()
        if not name:
            continue
        props_str = str(r.get("properties", "")).strip()
        props = _parse_properties_str(props_str)
        relation_types.append(RelationType(name=name, properties=props, pairs=[], include_common=False))

    return Schema(node_types=node_types, relation_types=relation_types)


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
    db_nodes = {t for t in g.node_table_names() if not t.startswith("_")}
    db_rels = {t for t in g.rel_table_names() if not t.startswith("_")}
    want_nodes = {t for t in target.node_type_names() if not t.startswith("_")}
    want_rels = {t for t in target.relation_type_names() if not t.startswith("_")}

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
    """Create a new empty GQL migration file and return its path."""
    migrations_dir.mkdir(parents=True, exist_ok=True)
    mid = next_migration_id(migrations_dir, name)
    path = migrations_dir / f"{mid}.gql"
    template = (
        f"-- Migration: {mid}\n"
        "-- Description:\n"
        "\n"
        "-- Example node type:\n"
        f"-- CREATE NODE TYPE {name.title().replace('_', '')} (\n"
        "--     id STRING,\n"
        "--     name STRING,\n"
        "--     summary STRING,\n"
        "--     origin STRING,\n"
        "--     sources LIST,\n"
        "--     confidence FLOAT64,\n"
        "--     created_at TIMESTAMP,\n"
        "--     updated_at TIMESTAMP\n"
        "-- );\n"
        "\n"
        "-- Example edge type:\n"
        f"-- CREATE EDGE TYPE {name.upper()} (\n"
        "--     origin STRING,\n"
        "--     sources LIST,\n"
        "--     confidence FLOAT64,\n"
        "--     created_at TIMESTAMP,\n"
        "--     updated_at TIMESTAMP\n"
        "-- );\n"
    )
    path.write_text(template, encoding="utf-8")
    return path
