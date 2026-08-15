"""Thin wrapper around Grafeo embedded graph database.

The wrapper provides a unified interface: it opens an embedded graph database
at a directory, exposes `execute()` returning row dicts, and provides helpers
to introspect labels, edges, and schema.
"""

from __future__ import annotations

import warnings
from collections.abc import Generator
from contextlib import contextmanager
from pathlib import Path
from typing import Any

from ..config import KBConfig

try:
    import grafeo  # type: ignore[import-not-found]
    grafeo_import_error: Exception | None = None
except ImportError as exc:
    grafeo = None  # type: ignore[assignment]
    grafeo_import_error = exc


class GraphEngineNotInstalled(RuntimeError):
    """Raised when Grafeo graph engine is not installed."""


class GrafeoNotInstalled(GraphEngineNotInstalled):
    """Raised when grafeo is required but not importable."""


def _require_graph_engine() -> str:
    """Check for available graph engine and return 'grafeo'."""
    if grafeo is not None:
        return "grafeo"
    raise GrafeoNotInstalled(
        "Grafeo graph database engine is not installed; install with `pip install grafeo` "
        "or `pip install .[graph]`"
    ) from grafeo_import_error


def _resolve_db_path(db_path: Path | str) -> Path:
    """Resolve a path to the actual DB directory/file.

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
        val = item.get("name") or item.get("label") or item.get("type") or item.get("id") or ""
        return str(val) if val else ""
    if hasattr(item, "name"):
        return str(item.name)
    return str(item) if item is not None else ""


class GraphDB:
    """A handle to an embedded Grafeo graph database.

    Instances manage lifecycle, query execution, and schema introspection.
    """

    engine: str = "grafeo"
    db_path: Path
    _db: Any

    def __init__(self, db_path: Path | str):
        _require_graph_engine()
        assert grafeo is not None
        self.engine = "grafeo"
        self.db_path = _resolve_db_path(db_path)
        self._db = grafeo.GrafeoDB(str(self.db_path))

    # --- lifecycle -----------------------------------------------------------

    def close(self) -> None:
        if self._db is not None:
            if hasattr(self._db, "close"):
                self._db.close()
            self._db = None

    def __enter__(self) -> GraphDB:
        return self

    def __exit__(self, *_exc: object) -> None:
        self.close()

    # --- query ---------------------------------------------------------------

    def execute(
        self, cypher: str, parameters: dict[str, Any] | None = None
    ) -> list[dict[str, Any]]:
        """Execute a Cypher/GQL query and return rows as a list of dicts."""
        if self._db is None:
            return []
        if parameters:
            result = self._db.execute(cypher, parameters)
        else:
            result = self._db.execute(cypher)
        if hasattr(result, "to_dict_list"):
            return result.to_dict_list()
        rows: list[dict[str, Any]] = []
        with warnings.catch_warnings():
            warnings.filterwarnings(
                "ignore",
                category=DeprecationWarning,
                message=r".*utcfromtimestamp.*",
            )
            for r in result:
                if isinstance(r, dict):
                    rows.append(r)
                elif hasattr(r, "to_dict"):
                    rows.append(r.to_dict())
                else:
                    rows.append(dict(r))
        return rows

    def execute_raw(self, statements: str) -> None:
        """Execute one or more `;`-separated statements, discarding results."""
        if self._db is not None:
            for stmt in statements.split(";"):
                stmt = stmt.strip()
                if stmt:
                    self._db.execute(stmt)

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
        try:
            for r in self.execute("SHOW NODE TYPES"):
                name = _extract_name(r.get("name") or r.get("label") or r)
                if name:
                    all_lbls.add(name)
        except Exception:  # noqa: BLE001
            pass
        return sorted(all_lbls)

    def rel_table_names(self) -> list[str]:
        if self._db is None:
            return []
        all_edges: set[str] = set()
        try:
            for r in self.execute("SHOW EDGE TYPES"):
                name = _extract_name(r.get("name") or r.get("label") or r)
                if name:
                    all_edges.add(name)
        except Exception:  # noqa: BLE001
            pass
        return sorted(all_edges)

    def table_info(self, name: str) -> list[dict[str, Any]]:
        """Return per-property info for a table."""
        if self._db is None:
            return []
        target_name = name.strip().lower()
        try:
            for nt in self.execute("SHOW NODE TYPES"):
                if str(nt.get("name", "")).strip().lower() == target_name:
                    props_str = nt.get("properties", "")
                    cols: list[dict[str, Any]] = []
                    if props_str:
                        for part in props_str.split(","):
                            part = part.strip()
                            if part:
                                pieces = part.split(None, 1)
                                col_name = pieces[0]
                                col_type = pieces[1] if len(pieces) > 1 else "ANY"
                                cols.append({"name": col_name, "type": col_type})
                    return cols
        except Exception:  # noqa: BLE001
            pass

        try:
            for et in self.execute("SHOW EDGE TYPES"):
                if str(et.get("name", "")).strip().lower() == target_name:
                    props_str = et.get("properties", "")
                    cols_edge: list[dict[str, Any]] = []
                    if props_str:
                        for part in props_str.split(","):
                            part = part.strip()
                            if part:
                                pieces = part.split(None, 1)
                                col_name = pieces[0]
                                col_type = pieces[1] if len(pieces) > 1 else "ANY"
                                cols_edge.append({"name": col_name, "type": col_type})
                    return cols_edge
        except Exception:  # noqa: BLE001
            pass

        return []


@contextmanager
def open_graph(db_path: Path | str) -> Generator[GraphDB, None, None]:
    """Open a `GraphDB` as a context manager.

    Accepts either a direct path to the DB directory or the KB root
    directory (containing kb.toml).
    """
    g = GraphDB(db_path)
    try:
        yield g
    finally:
        g.close()
