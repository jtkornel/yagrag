"""Configuration model for a knowledge base directory.

A knowledge base is a self-contained directory holding documents, a graph
database, a schema, and a `kb.toml` config file. The config is intentionally
minimal at bootstrap time; new fields are added as later steps land.
"""

from __future__ import annotations

import tomllib
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict, Field


CONFIG_FILENAME = "kb.toml"


class PathsConfig(BaseModel):
    """Filesystem paths used inside a KB directory, relative to the KB root."""

    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    documents: str = "documents"
    raw: str = "documents/raw"
    synthesized: str = "documents/synthesized"
    manifest: str = "documents/manifest.json"
    schema_dir: str = Field(default="schema", alias="schema")
    graph_db: str = "graph.kuzu"
    code: str = "code"


class EmbedderConfig(BaseModel):
    """Embedding backend selection. Local by default; API backends pluggable."""

    model_config = ConfigDict(extra="forbid")

    backend: str = "local"
    model: str = "BAAI/bge-small-en-v1.5"
    dim: int = 384


class KBConfig(BaseModel):
    """Top-level KB configuration, loaded from `<kb_root>/kb.toml`."""

    model_config = ConfigDict(extra="forbid")

    version: int = 1
    name: str = "kb"
    paths: PathsConfig = Field(default_factory=PathsConfig)
    embedder: EmbedderConfig = Field(default_factory=EmbedderConfig)

    @classmethod
    def default(cls, name: str = "kb") -> "KBConfig":
        return cls(name=name)

    @classmethod
    def load(cls, kb_root: Path) -> "KBConfig":
        """Load `<kb_root>/kb.toml` into a `KBConfig`."""
        config_path = kb_root / CONFIG_FILENAME
        if not config_path.is_file():
            raise FileNotFoundError(
                f"Not a knowledge base directory: missing {CONFIG_FILENAME} at {kb_root}"
            )
        with config_path.open("rb") as f:
            data: dict[str, Any] = tomllib.load(f)
        return cls.model_validate(data)

    def to_toml(self) -> str:
        """Serialize this config as a TOML string.

        A hand-rolled writer is used to keep the dependency footprint small and
        the output stable/reviewable. The schema is small and well-known.
        """
        lines: list[str] = []
        lines.append(f"version = {self.version}")
        lines.append(f'name = "{self.name}"')
        lines.append("")
        lines.append("[paths]")
        lines.append(f'documents = "{self.paths.documents}"')
        lines.append(f'raw = "{self.paths.raw}"')
        lines.append(f'synthesized = "{self.paths.synthesized}"')
        lines.append(f'manifest = "{self.paths.manifest}"')
        lines.append(f'schema = "{self.paths.schema_dir}"')
        lines.append(f'graph_db = "{self.paths.graph_db}"')
        lines.append(f'code = "{self.paths.code}"')
        lines.append("")
        lines.append("[embedder]")
        lines.append(f'backend = "{self.embedder.backend}"')
        lines.append(f'model = "{self.embedder.model}"')
        lines.append(f"dim = {self.embedder.dim}")
        lines.append("")
        return "\n".join(lines)
