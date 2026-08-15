"""Embedding backends, full-text indexing, and hybrid retrieval."""

from .embedder import Embedder, EmbedderError, HashEmbedder, LocalEmbedder, get_embedder
from .indexer import IndexStats, build_index, chunk_text
from .retrieval import search

__all__ = [
    "Embedder",
    "EmbedderError",
    "HashEmbedder",
    "IndexStats",
    "LocalEmbedder",
    "build_index",
    "chunk_text",
    "get_embedder",
    "search",
]
