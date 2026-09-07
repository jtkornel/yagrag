"""Loader and query helpers for the schema companion.

The companion (`schema/schema_companion.json`) records semantics that GQL
migrations cannot express: per-type labels/descriptions/usage notes,
domain/range constraints, OWL-style edge qualifiers (transitive, symmetric,
inverse_of), canonical storage direction, and MaRDI cross-references.

Storage convention ("lean", following MathModDB): for inverse pairs only the
canonical direction is stored; symmetric edges are stored once; transitive
edges store only direct links. The helpers here expand those qualifiers at
query time.

See docs/mardi/PLAN-mardi-schema-alignment.md (§4.3).
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


class CompanionError(RuntimeError):
    """Raised for malformed or inconsistent companion files."""


@dataclass(frozen=True)
class EdgeSemantics:
    """Semantics for one edge type."""

    name: str
    label: str = ""
    description: str = ""
    usage: str = ""
    example: str = ""
    domain: tuple[str, ...] = ()
    range: tuple[str, ...] = ()
    transitive: bool = False
    symmetric: bool = False
    inverse_of: str | None = None
    stored: bool = True  # False for query-time-only inverses (e.g. MODELS)
    mardi_source: str | None = None


@dataclass(frozen=True)
class NodeSemantics:
    """Semantics for one node type."""

    name: str
    label: str = ""
    description: str = ""
    usage: str = ""
    deprecated: bool = False
    extra_properties: dict[str, str] = field(default_factory=dict)
    mardi_source: str | None = None


@dataclass(frozen=True)
class SchemaCompanion:
    """Parsed companion: node and edge semantics keyed by type name."""

    nodes: dict[str, NodeSemantics]
    edges: dict[str, EdgeSemantics]

    # --- lookups -------------------------------------------------------------

    def edge(self, name: str) -> EdgeSemantics | None:
        return self.edges.get(name)

    def node(self, name: str) -> NodeSemantics | None:
        return self.nodes.get(name)

    def canonical_edge(self, name: str) -> EdgeSemantics | None:
        """Resolve `name` to the stored (canonical) edge of an inverse pair.

        If `name` denotes a non-stored inverse (e.g. MODELS), returns the
        canonical entry (MODELLED_BY) instead. Unknown names return None.
        """
        e = self.edges.get(name)
        if e is None:
            return None
        if e.stored:
            return e
        if e.inverse_of:
            return self.edges.get(e.inverse_of)
        return None

    def inverse_name(self, name: str) -> str | None:
        """Return the inverse edge name of `name`, if declared."""
        e = self.edges.get(name)
        if e is None:
            return None
        if e.inverse_of:
            return e.inverse_of
        # `name` may itself be the target of someone else's inverse_of
        for other in self.edges.values():
            if other.inverse_of == name:
                return other.name
        return None

    def is_symmetric(self, name: str) -> bool:
        e = self.edges.get(name)
        return bool(e and e.symmetric)

    def is_transitive(self, name: str) -> bool:
        e = self.edges.get(name)
        return bool(e and e.transitive)

    def stored_edge_names(self) -> list[str]:
        return sorted(n for n, e in self.edges.items() if e.stored)

    def active_node_names(self) -> list[str]:
        return sorted(n for n, s in self.nodes.items() if not s.deprecated)

    # --- validation ----------------------------------------------------------

    def validate_against_schema(self, node_names: list[str], edge_names: list[str]) -> list[str]:
        """Check consistency between the companion and the structural schema.

        Returns a list of human-readable issues (empty when consistent).
        """
        issues: list[str] = []
        node_set, edge_set = set(node_names), set(edge_names)

        for name, e in self.edges.items():
            if e.stored and name not in edge_set:
                issues.append(f"edge {name}: companion entry has no matching GQL edge type")
            if e.inverse_of and e.inverse_of not in self.edges:
                issues.append(f"edge {name}: inverse_of {e.inverse_of!r} not declared in companion")
            for t in (*e.domain, *e.range):
                if t not in node_set:
                    issues.append(f"edge {name}: endpoint type {t!r} not in schema")
        for name in self.nodes:
            if name not in node_set:
                issues.append(f"node {name}: companion entry has no matching GQL node type")

        undocumented = sorted(n for n in edge_set if n not in self.edges)
        if undocumented:
            issues.append(f"GQL edge types missing companion entries: {', '.join(undocumented)}")
        undocumented_nodes = sorted(n for n in node_set if n not in self.nodes and not n.startswith("_"))
        if undocumented_nodes:
            issues.append(f"GQL node types missing companion entries: {', '.join(undocumented_nodes)}")
        return issues


def load_companion(path: Path | str) -> SchemaCompanion:
    """Load and validate the companion JSON file."""
    p = Path(path)
    try:
        data: dict[str, Any] = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise CompanionError(f"cannot load schema companion {p}: {exc}") from exc

    def _str_tuple(v: Any) -> tuple[str, ...]:
        if v is None:
            return ()
        if isinstance(v, str):
            return (v,)
        if isinstance(v, list) and all(isinstance(x, str) for x in v):
            return tuple(v)
        raise CompanionError(f"invalid domain/range value: {v!r}")

    nodes: dict[str, NodeSemantics] = {}
    for name, raw in (data.get("nodes") or {}).items():
        nodes[name] = NodeSemantics(
            name=name,
            label=str(raw.get("label", "")),
            description=str(raw.get("description", "")),
            usage=str(raw.get("usage", "")),
            deprecated=bool(raw.get("deprecated", False)),
            extra_properties=dict(raw.get("extra_properties") or {}),
            mardi_source=raw.get("mardi_source"),
        )

    edges: dict[str, EdgeSemantics] = {}
    for name, raw in (data.get("edges") or {}).items():
        edges[name] = EdgeSemantics(
            name=name,
            label=str(raw.get("label", "")),
            description=str(raw.get("description", "")),
            usage=str(raw.get("usage", "")),
            example=str(raw.get("example", "")),
            domain=_str_tuple(raw.get("domain")),
            range=_str_tuple(raw.get("range")),
            transitive=bool(raw.get("transitive", False)),
            symmetric=bool(raw.get("symmetric", False)),
            inverse_of=raw.get("inverse_of"),
            stored=bool(raw.get("stored", True)),
            mardi_source=raw.get("mardi_source"),
        )

    # Symmetric edges must be their own inverse.
    for e in edges.values():
        if e.symmetric and e.inverse_of not in (None, e.name):
            raise CompanionError(
                f"edge {e.name}: symmetric edges cannot declare a distinct inverse_of"
            )

    return SchemaCompanion(nodes=nodes, edges=edges)
