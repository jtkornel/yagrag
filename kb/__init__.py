"""Deterministic CLI and library for a local-first GraphRAG knowledge base.

The `kb` package performs document-store and graph-database operations.
It is intentionally LLM-free; all reasoning lives in the agent layer
(`.agents/skills/`) which invokes this CLI.
"""

from .config import KBConfig
from .graph import (
    GrafeoNotInstalled,
    GraphDB,
    GraphEngineNotInstalled,
    GraphWriteError,
    ProvenanceError,
    execute_batch,
    open_graph,
    upsert_claim,
    upsert_edge,
    upsert_node,
)

__version__ = "0.1.0"
__all__ = [
    "KBConfig",
    "GraphDB",
    "GraphEngineNotInstalled",
    "GrafeoNotInstalled",
    "GraphWriteError",
    "ProvenanceError",
    "execute_batch",
    "open_graph",
    "upsert_claim",
    "upsert_edge",
    "upsert_node",
]
