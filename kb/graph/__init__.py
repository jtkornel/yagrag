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
    "GrafeoNotInstalled",
    "GraphDB",
    "GraphEngineNotInstalled",
    "GraphWriteError",
    "ProvenanceError",
    "execute_batch",
    "open_graph",
    "upsert_claim",
    "upsert_edge",
    "upsert_node",
]
