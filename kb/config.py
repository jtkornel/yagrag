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


CURRENT_FORMAT_VERSION = 1
MIN_SOFTWARE_VERSION = "0.1.0"


class IncompatibleKBVersionError(RuntimeError):
    """Raised when a knowledge base requires a newer version of the software."""


class PathsConfig(BaseModel):
    """Filesystem paths used inside a KB directory, relative to the KB root."""

    model_config = ConfigDict(extra="ignore", populate_by_name=True)

    documents: str = "documents"
    raw: str = "documents/raw"
    synthesized: str = "documents/synthesized"
    manifest: str = "documents/manifest.json"
    schema_dir: str = Field(default="schema", alias="schema")
    graph_db: str = "graph.tvdb"
    code: str = "code"


class EmbedderConfig(BaseModel):
    """Embedding backend selection. Local by default; API backends pluggable."""

    model_config = ConfigDict(extra="ignore")

    backend: str = "local"
    model: str = "BAAI/bge-small-en-v1.5"
    dim: int = 384


def _parse_semver(v: str) -> tuple[int, ...]:
    parts = []
    for piece in v.strip().split("."):
        clean = "".join(ch for ch in piece if ch.isdigit())
        if clean:
            parts.append(int(clean))
        else:
            break
    return tuple(parts) if parts else (0,)


class KBConfig(BaseModel):
    """Top-level KB configuration, loaded from `<kb_root>/kb.toml`."""

    model_config = ConfigDict(extra="ignore")

    version: int = 1
    format_version: int = CURRENT_FORMAT_VERSION
    min_software_version: str = MIN_SOFTWARE_VERSION
    name: str = "kb"
    description: str = ""
    paths: PathsConfig = Field(default_factory=PathsConfig)
    embedder: EmbedderConfig = Field(default_factory=EmbedderConfig)

    @classmethod
    def default(cls, name: str = "kb", description: str = "") -> KBConfig:
        return cls(name=name, description=description)

    @classmethod
    def load(cls, kb_root: Path, check_compat: bool = True) -> KBConfig:
        """Load `<kb_root>/kb.toml` into a `KBConfig`.

        If `check_compat` is True (default), checks whether the KB requires
        a newer version of the software and raises IncompatibleKBVersionError if so.
        """
        config_path = kb_root / CONFIG_FILENAME
        if not config_path.is_file():
            raise FileNotFoundError(
                f"Not a knowledge base directory: missing {CONFIG_FILENAME} at {kb_root}"
            )
        with config_path.open("rb") as f:
            data: dict[str, Any] = tomllib.load(f)
        cfg = cls.model_validate(data)
        if check_compat:
            cfg.check_compatibility()
        return cfg

    def check_compatibility(self) -> None:
        """Verify that current software can handle this knowledge base version."""
        from . import __version__ as current_sw_version

        # Forward compatibility guard: newer database / KB format than this software knows how to handle
        if self.format_version > CURRENT_FORMAT_VERSION:
            raise IncompatibleKBVersionError(
                f"This knowledge base requires format version {self.format_version}, but "
                f"your yagrag/kb software only supports up to format version {CURRENT_FORMAT_VERSION}. "
                "Please update yagrag (e.g. `pip install --upgrade ...` or `pipx upgrade ...`)."
            )

        if _parse_semver(self.min_software_version) > _parse_semver(current_sw_version):
            raise IncompatibleKBVersionError(
                f"This knowledge base requires yagrag/kb >= {self.min_software_version}, "
                f"but you are running {current_sw_version}. "
                "Please update your yagrag installation."
            )

    def to_toml(self) -> str:
        """Serialize this config as a TOML string.

        A hand-rolled writer is used to keep the dependency footprint small and
        the output stable/reviewable. The schema is small and well-known.
        """
        lines: list[str] = []
        lines.append(f"version = {self.version}")
        lines.append(f"format_version = {self.format_version}")
        lines.append(f'min_software_version = "{self.min_software_version}"')
        lines.append(f'name = "{self.name}"')
        if self.description:
            lines.append(f'description = "{self.description}"')
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
