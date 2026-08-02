"""Thin wrapper around a Kuzu embedded graph database.

The wrapper is intentionally small: it opens a Kuzu database at a directory,
exposes `execute()` returning row dicts, and provides helpers to introspect
tables. Keeping this layer thin makes it easy to swap in a different Kuzu
version or another embedded graph engine later.
"""

from __future__ import annotations

from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator

try:
    import kuzu  # type: ignore
except ImportError as exc:  # pragma: no cover - handled at call site
    kuzu = None  # type: ignore
    _KUZU_IMPORT_ERROR: Exception | None = exc
else:
    _KUZU_IMPORT_ERROR = None


class KuzuNotInstalled(RuntimeError):
    """Raised when kuzu is required but not importable."""


def _require_kuzu() -> None:
    if kuzu is None:
        raise KuzuNotInstalled(
            "kuzu is not installed; install with `pip install kuzu` "
            "or `pip install .[graph]`"
        ) from _KUZU_IMPORT_ERROR


class GraphDB:
    """A handle to a Kuzu database directory.

    Instances hold a `Database` and a `Connection`. The database directory
    is created if it does not exist yet. Close by calling `close()` or via
    context-manager use.
    """

    def __init__(self, db_path: Path):
        _require_kuzu()
        db_path = db_path.expanduser().resolve()
        db_path.parent.mkdir(parents=True, exist_ok=True)
        self.db_path = db_path
        self._db = kuzu.Database(str(db_path))
        self._conn = kuzu.Connection(self._db)

    # --- lifecycle -----------------------------------------------------------

    def close(self) -> None:
        # Kuzu Python bindings close on GC; explicit deletion helps release
        # file locks on Windows/CI environments.
        self._conn = None  # type: ignore
        self._db = None  # type: ignore

    def __enter__(self) -> "GraphDB":
        return self

    def __exit__(self, *_exc: object) -> None:
        self.close()

    # --- query ---------------------------------------------------------------

    def execute(
        self, cypher: str, parameters: dict[str, Any] | None = None
    ) -> list[dict[str, Any]]:
        """Execute a Cypher/DDL query and return rows as a list of dicts."""
        result = self._conn.execute(cypher, parameters or {})
        cols = result.get_column_names()
        rows: list[dict[str, Any]] = []
        while result.has_next():
            row = result.get_next()
            rows.append(dict(zip(cols, row)))
        return rows

    def execute_raw(self, statements: str) -> None:
        """Execute one or more `;`-separated statements, discarding results.

        Useful for DDL and extension management (INSTALL/LOAD), where Kuzu
        may return one result object per statement.
        """
        self._conn.execute(statements)

    # --- introspection -------------------------------------------------------

    def list_tables(self) -> list[dict[str, Any]]:
        """Return all tables in the DB with name + type (NODE|REL)."""
        return self.execute("CALL SHOW_TABLES() RETURN *")

    def node_table_names(self) -> list[str]:
        return [r["name"] for r in self.list_tables() if r.get("type") == "NODE"]

    def rel_table_names(self) -> list[str]:
        return [r["name"] for r in self.list_tables() if r.get("type") == "REL"]

    def table_info(self, name: str) -> list[dict[str, Any]]:
        """Return per-property info for a table."""
        # TABLE_INFO returns: property id, name, type, default expression, primary key
        return self.execute(f'CALL TABLE_INFO("{name}") RETURN *')


@contextmanager
def open_graph(db_path: Path) -> Iterator[GraphDB]:
    """Open a `GraphDB` as a context manager."""
    g = GraphDB(db_path)
    try:
        yield g
    finally:
        g.close()
