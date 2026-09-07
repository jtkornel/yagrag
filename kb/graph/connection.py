"""Thin wrapper around TrueSpar Traverse embedded graph database.

The wrapper provides a unified interface: it opens an embedded graph database
at a .tvdb file, exposes `execute()` returning row dicts (supporting ISO GQL and
Cypher), and provides helpers to introspect labels, edges, and schema.
"""

from __future__ import annotations

import contextlib
import datetime
from collections.abc import Generator
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Self

from ..config import KBConfig

try:
    import traverse  # type: ignore[import-not-found]
    traverse_import_error: Exception | None = None
except ImportError as exc:
    traverse = None  # type: ignore[assignment]
    traverse_import_error = exc


class GraphEngineNotInstalled(RuntimeError):
    """Raised when Traverse graph engine is not installed."""


class TraverseNotInstalled(GraphEngineNotInstalled):
    """Raised when traverse-embedded is required but not importable."""


def _require_graph_engine() -> str:
    """Check for available graph engine and return 'traverse'."""
    if traverse is not None:
        return "traverse"
    raise TraverseNotInstalled(
        "TrueSpar Traverse graph database engine is not installed; install with "
        "`pip install traverse-embedded` or `pip install .[graph]`"
    ) from traverse_import_error


def _resolve_db_path(db_path: Path | str) -> Path:
    """Resolve a path to the actual DB file.

    If given a KB root directory containing `kb.toml`, reads the configured
    `graph_db` path; otherwise treats `db_path` directly as the DB path.
    """
    p = Path(db_path).expanduser().resolve()
    if (p / "kb.toml").is_file():
        config = KBConfig.load(p)
        p = p / config.paths.graph_db
    p.parent.mkdir(parents=True, exist_ok=True)
    return p


def _extract_name(item: Any) -> str:
    """Safely extract a string name from a string, dict, or object."""
    if isinstance(item, str):
        return item
    if isinstance(item, dict):
        val = item.get("name") or item.get("label") or item.get("type") or item.get("id") or item.get("relationshipType") or ""
        return str(val) if val else ""
    if hasattr(item, "name"):
        return str(item.name)
    return str(item) if item is not None else ""


def _sanitize_val(v: Any) -> Any:
    if isinstance(v, (datetime.datetime, datetime.date)):
        return v.isoformat()
    if isinstance(v, list):
        return [_sanitize_val(x) for x in v]
    if isinstance(v, dict):
        return {k: _sanitize_val(val) for k, val in v.items()}
    return v


def _sanitize_params(params: dict[str, Any] | None) -> dict[str, Any] | None:
    if not params:
        return params
    return {k: _sanitize_val(v) for k, v in params.items()}


class GraphDB:
    """A handle to an embedded TrueSpar Traverse graph database.

    Instances manage lifecycle, query execution (GQL/Cypher), and schema introspection.
    """

    engine: str = "traverse"
    db_path: Path
    _db: Any

    def __init__(self, db_path: Path | str):
        _require_graph_engine()
        assert traverse is not None
        self.engine = "traverse"
        self.db_path = _resolve_db_path(db_path)
        self._db = traverse.open(str(self.db_path))

    # --- lifecycle -----------------------------------------------------------

    def flush(self) -> None:
        """Flush in-memory database state to the .tvdb file."""
        if self._db is not None and hasattr(self._db, "flush"):
            self._db.flush()

    def close(self) -> None:
        if self._db is not None:
            with contextlib.suppress(Exception):
                self._db.flush()
            if hasattr(self._db, "close"):
                self._db.close()
            self._db = None

    def __enter__(self) -> Self:
        return self

    def __exit__(self, *_exc: object) -> None:
        self.close()

    # --- query ---------------------------------------------------------------

    def execute(
        self,
        query: str,
        parameters: dict[str, Any] | None = None,
        dialect: str | None = None,
    ) -> list[dict[str, Any]]:
        """Execute a GQL or Cypher query and return rows as a list of dicts.

        By default, queries attempt execution using the ISO GQL dialect
        (`dialect="gql"`), and automatically fall back to standard Cypher for
        Cypher-specific statements (e.g. `MERGE`, schema constraints, `SHOW`).
        """
        if self._db is None:
            return []

        clean_params = _sanitize_params(parameters) or {}
        with self._db.begin() as tx:
            # If user explicitly requested a dialect, use it
            if dialect:
                res = tx.execute(query, clean_params, dialect=dialect)
                tx.commit()
                self._db.flush()
                return res.data() if hasattr(res, "data") else []

            # GQL-first: try GQL dialect, fallback to default Cypher if GQL rejects syntax
            try:
                res = tx.execute(query, clean_params, dialect="gql")
            except Exception:
                res = tx.execute(query, clean_params)

            tx.commit()
            self._db.flush()
            return res.data() if hasattr(res, "data") else []

    def execute_raw(self, statements: str) -> None:
        """Execute one or more `;`-separated statements, discarding results."""
        if self._db is not None:
            for stmt in statements.split(";"):
                stmt = stmt.strip()
                if stmt:
                    self.execute(stmt)

    # --- introspection -------------------------------------------------------

    def list_tables(self) -> list[dict[str, Any]]:
        """Return all tables/labels in the DB with name + type (NODE|REL)."""
        tables: list[dict[str, Any]] = []
        for nl in self.node_table_names():
            tables.append({"name": nl, "type": "NODE"})
        for et in self.rel_table_names():
            tables.append({"name": et, "type": "REL"})
        return tables

    def node_table_names(self) -> list[str]:
        if self._db is None:
            return []
        all_lbls: set[str] = set()
        with contextlib.suppress(Exception):
            rows = self.execute("CALL db.labels()")
            for r in rows:
                name = _extract_name(r)
                if name:
                    all_lbls.add(name)
        # Also check schema constraints for declared labels with 0 nodes yet
        with contextlib.suppress(Exception):
            rows = self.execute("SHOW CONSTRAINTS")
            for r in rows:
                if str(r.get("entityType", "")).upper() == "NODE":
                    lbl = str(r.get("labelsOrTypes", "")).strip()
                    if lbl:
                        all_lbls.add(lbl)
        return sorted(all_lbls)

    def rel_table_names(self) -> list[str]:
        if self._db is None:
            return []
        all_edges: set[str] = set()
        with contextlib.suppress(Exception):
            rows = self.execute("CALL db.relationshipTypes()")
            for r in rows:
                name = _extract_name(r)
                if name:
                    all_edges.add(name)
        with contextlib.suppress(Exception):
            rows = self.execute("SHOW CONSTRAINTS")
            for r in rows:
                if str(r.get("entityType", "")).upper() == "RELATIONSHIP":
                    rt = str(r.get("labelsOrTypes", "")).strip()
                    if rt:
                        all_edges.add(rt)
        with contextlib.suppress(Exception):
            rows = self.execute("SHOW INDEXES")
            for r in rows:
                if str(r.get("entityType", "")).upper() == "RELATIONSHIP":
                    rt = str(r.get("labelsOrTypes", "")).strip()
                    if rt:
                        all_edges.add(rt)
        return sorted(all_edges)

    def table_info(self, name: str) -> list[dict[str, Any]]:
        """Return per-property info for a label or relationship type."""
        if self._db is None:
            return []
        target_name = name.strip()

        # 1. Check migrations schema if available
        mig_dir = self.db_path.parent / "schema" / "migrations"
        if not mig_dir.is_dir():
            mig_dir = self.db_path.parent.parent / "schema" / "migrations"
        if mig_dir.is_dir():
            with contextlib.suppress(Exception):
                from ..schema.migrations import build_target_schema
                target = build_target_schema(mig_dir)
                for nt in target.node_types:
                    if nt.name.lower() == target_name.lower():
                        return [{"name": p.name, "type": p.type} for p in nt.properties]
                for rt in target.relation_types:
                    if rt.name.lower() == target_name.lower():
                        return [{"name": p.name, "type": p.type} for p in rt.properties]

        # 2. Try finding a sample node with this label
        with contextlib.suppress(Exception):
            rows = self.execute(
                f"MATCH (n:{target_name}) RETURN properties(n) AS props LIMIT 1"
            )
            if rows:
                props = rows[0].get("props", {})
                return [{"name": k, "type": type(v).__name__.upper()} for k, v in props.items()]

        # 3. Try finding a sample edge with this relation type
        with contextlib.suppress(Exception):
            rows = self.execute(
                f"MATCH ()-[r:{target_name}]->() RETURN properties(r) AS props LIMIT 1"
            )
            if rows:
                props = rows[0].get("props", {})
                return [{"name": k, "type": type(v).__name__.upper()} for k, v in props.items()]

        return []


@contextmanager
def open_graph(db_path: Path | str) -> Generator[GraphDB, None, None]:
    """Open a `GraphDB` as a context manager.

    Accepts either a direct path to the DB file or the KB root
    directory (containing kb.toml).
    """
    g = GraphDB(db_path)
    try:
        yield g
    finally:
        g.close()
