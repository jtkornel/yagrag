"""Deterministic CLI and library for a local-first GraphRAG knowledge base.

The `kb` package performs document-store and graph-database operations.
It is intentionally LLM-free; all reasoning lives in the agent layer
(`.agents/skills/`) which invokes this CLI.
"""

from .config import CURRENT_FORMAT_VERSION, IncompatibleKBVersionError, KBConfig
from .graph import (
    GraphDB,
    GraphEngineNotInstalled,
    GraphWriteError,
    ProvenanceError,
    TraverseNotInstalled,
    execute_batch,
    open_graph,
    upsert_claim,
    upsert_edge,
    upsert_node,
)

__version__ = "0.1.0"
__all__ = [
    "CURRENT_FORMAT_VERSION",
    "GraphDB",
    "GraphEngineNotInstalled",
    "GraphWriteError",
    "IncompatibleKBVersionError",
    "KBConfig",
    "ProvenanceError",
    "TraverseNotInstalled",
    "execute_batch",
    "open_graph",
    "upsert_claim",
    "upsert_edge",
    "upsert_node",
]
