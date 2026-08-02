"""Deterministic CLI and library for a local-first GraphRAG knowledge base.

The `kb` package performs document-store and graph-database operations.
It is intentionally LLM-free; all reasoning lives in the agent layer
(`.junie/skills/`) which invokes this CLI.
"""

__version__ = "0.1.0"
