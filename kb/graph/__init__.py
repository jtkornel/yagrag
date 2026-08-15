"""Embedded graph database connection wrapper (Grafeo) and Cypher execution helpers."""

from .connection import (
    GrafeoNotInstalled,
    GraphDB,
    GraphEngineNotInstalled,
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
    "GrafeoNotInstalled",
    "open_graph",
    "upsert_node",
    "upsert_edge",
    "upsert_claim",
    "execute_batch",
    "GraphWriteError",
    "ProvenanceError",
]
