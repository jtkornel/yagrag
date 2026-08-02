"""Pluggable embedding backends.

The `Embedder` interface is deliberately tiny: turn a list of texts into a
list of float vectors, and report the backend/model/dim so each index records
what produced its vectors.

Backends:
- `local` — fastembed (ONNX, fully offline after first model download).
- `hash`  — deterministic feature-hashing embedder with zero dependencies;
  useful for tests and for air-gapped setups where no model is available.
  Not semantically strong, but stable and fast.
"""

from __future__ import annotations

import hashlib
import math
import re
from typing import Protocol

from ..config import EmbedderConfig


class EmbedderError(RuntimeError):
    """Raised when an embedding backend is unavailable or misconfigured."""


class Embedder(Protocol):
    backend: str
    model: str
    dim: int

    def embed(self, texts: list[str]) -> list[list[float]]: ...


class HashEmbedder:
    """Deterministic feature-hashing embedder (no ML dependencies).

    Tokenizes into lowercase words, hashes each token into `dim` buckets with
    a signed value, and L2-normalizes. Deterministic across runs/platforms.
    """

    backend = "hash"

    def __init__(self, dim: int = 384, model: str = "feature-hash-v1"):
        self.dim = dim
        self.model = model

    def embed(self, texts: list[str]) -> list[list[float]]:
        return [self._embed_one(t) for t in texts]

    def _embed_one(self, text: str) -> list[float]:
        vec = [0.0] * self.dim
        for token in re.findall(r"[a-z0-9]+", text.lower()):
            digest = hashlib.sha256(token.encode("utf-8")).digest()
            bucket = int.from_bytes(digest[:4], "little") % self.dim
            sign = 1.0 if digest[4] % 2 == 0 else -1.0
            vec[bucket] += sign
        norm = math.sqrt(sum(v * v for v in vec))
        if norm > 0:
            vec = [v / norm for v in vec]
        return vec


class LocalEmbedder:
    """fastembed-based local embedder (ONNX runtime, offline after download)."""

    backend = "local"

    def __init__(self, model: str, dim: int):
        try:
            from fastembed import TextEmbedding  # type: ignore
        except ImportError as exc:
            raise EmbedderError(
                "fastembed is not installed; install with `pip install .[embed]` "
                "or set embedder.backend = \"hash\" in kb.toml"
            ) from exc
        self.model = model
        self.dim = dim
        self._model = TextEmbedding(model_name=model)

    def embed(self, texts: list[str]) -> list[list[float]]:
        return [list(map(float, v)) for v in self._model.embed(texts)]


def get_embedder(config: EmbedderConfig) -> Embedder:
    """Instantiate the embedder selected by kb.toml."""
    if config.backend == "hash":
        return HashEmbedder(dim=config.dim)
    if config.backend == "local":
        return LocalEmbedder(model=config.model, dim=config.dim)
    raise EmbedderError(
        f"unknown embedder backend {config.backend!r}; expected 'local' or 'hash'"
    )
