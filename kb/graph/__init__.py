"""Embedded graph database connection wrapper (Traverse) and Cypher execution helpers."""

from .connection import (
    GraphDB,
    GraphEngineNotInstalled,
    TraverseNotInstalled,
    open_graph,
)
from .upsert import (
    GraphWriteError,
    ProvenanceError,
    execute_batch,
    upsert_claim,
    upsert_edge,
    upsert_node,
)

__all__ = [
    "GraphDB",
    "GraphEngineNotInstalled",
    "GraphWriteError",
    "ProvenanceError",
    "TraverseNotInstalled",
    "execute_batch",
    "open_graph",
    "upsert_claim",
    "upsert_edge",
    "upsert_node",
]
