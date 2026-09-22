"""Cache management for extracted documents and diagrams.

Intermediate storage under `<kb_root>/documents/cache/<doc_id>/`:
  - meta.json: Cache manifest (extractor name, version, source SHA-256 hash, counts, timestamp)
  - content.md: Extracted Markdown representation
  - document.json: Lossless structural AST (e.g. DoclingDocument serialized dictionary)
  - figures/: Directory of cropped visual assets
    - fig_<index>_p<page>.png: Cropped diagram/figure PNG
    - fig_<index>.meta.json: Sidecar metadata (caption, page, bounding box)
"""

from __future__ import annotations

import json
import shutil
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any


def _utcnow() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


@dataclass(frozen=True)
class FigureMetadata:
    """Sidecar metadata for an extracted figure or diagram."""

    id: str
    page: int
    bbox: list[float]  # [l, b, r, t] or [l, t, r, b]
    caption: str
    image_path: str  # relative to KB root or cache root


@dataclass(frozen=True)
class CacheMetadata:
    """Cache manifest tracking extraction provenance and hash integrity."""

    doc_id: str
    source_hash: str
    extractor: str
    extractor_version: str
    extracted_at: str
    has_figures: bool = False
    figure_count: int = 0
    tables_count: int = 0
    equations_count: int = 0

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> CacheMetadata:
        return cls(
            doc_id=data["doc_id"],
            source_hash=data["source_hash"],
            extractor=data.get("extractor", "docling"),
            extractor_version=data.get("extractor_version", ""),
            extracted_at=data.get("extracted_at", ""),
            has_figures=data.get("has_figures", False),
            figure_count=data.get("figure_count", 0),
            tables_count=data.get("tables_count", 0),
            equations_count=data.get("equations_count", 0),
        )


class CacheManager:
    """Manages reading, writing, and invalidation of cached extracted documents."""

    def __init__(self, kb_root: Path, cache_rel_path: str = "documents/cache"):
        self.kb_root = kb_root.expanduser().resolve()
        self.cache_root = self.kb_root / cache_rel_path

    def doc_cache_dir(self, doc_id: str) -> Path:
        return self.cache_root / doc_id

    def meta_path(self, doc_id: str) -> Path:
        return self.doc_cache_dir(doc_id) / "meta.json"

    def content_path(self, doc_id: str) -> Path:
        return self.doc_cache_dir(doc_id) / "content.md"

    def document_json_path(self, doc_id: str) -> Path:
        return self.doc_cache_dir(doc_id) / "document.json"

    def figures_dir(self, doc_id: str) -> Path:
        return self.doc_cache_dir(doc_id) / "figures"

    def is_valid(self, doc_id: str, expected_source_hash: str) -> bool:
        """Check if cache exists and source SHA-256 hash matches."""
        meta_file = self.meta_path(doc_id)
        content_file = self.content_path(doc_id)
        if not meta_file.is_file() or not content_file.is_file():
            return False
        try:
            with meta_file.open("r", encoding="utf-8") as f:
                data = json.load(f)
            meta = CacheMetadata.from_dict(data)
            return meta.source_hash == expected_source_hash
        except (OSError, json.JSONDecodeError, KeyError, TypeError):
            return False

    def get_metadata(self, doc_id: str) -> CacheMetadata | None:
        """Load cache metadata for a document if present."""
        meta_file = self.meta_path(doc_id)
        if not meta_file.is_file():
            return None
        try:
            with meta_file.open("r", encoding="utf-8") as f:
                return CacheMetadata.from_dict(json.load(f))
        except (OSError, json.JSONDecodeError, KeyError, TypeError):
            return None

    def read_content(self, doc_id: str) -> str | None:
        """Read cached content.md if available."""
        p = self.content_path(doc_id)
        if p.is_file():
            return p.read_text(encoding="utf-8")
        return None

    def read_document_json(self, doc_id: str) -> dict[str, Any] | None:
        """Read cached document.json AST if available."""
        p = self.document_json_path(doc_id)
        if p.is_file():
            try:
                with p.open("r", encoding="utf-8") as f:
                    return json.load(f)
            except (OSError, json.JSONDecodeError):
                return None
        return None

    def list_figures(self, doc_id: str) -> list[FigureMetadata]:
        """List all extracted figures with metadata for a document."""
        fig_dir = self.figures_dir(doc_id)
        if not fig_dir.is_dir():
            return []

        results: list[FigureMetadata] = []
        for meta_path in sorted(fig_dir.glob("*.meta.json")):
            try:
                with meta_path.open("r", encoding="utf-8") as f:
                    d = json.load(f)
                results.append(
                    FigureMetadata(
                        id=d.get("id", meta_path.stem.replace(".meta", "")),
                        page=d.get("page", 1),
                        bbox=d.get("bbox", []),
                        caption=d.get("caption", ""),
                        image_path=d.get("image_path", ""),
                    )
                )
            except (OSError, json.JSONDecodeError, KeyError):
                continue
        return results

    def write_cache(
        self,
        doc_id: str,
        source_hash: str,
        content: str,
        extractor: str = "docling",
        extractor_version: str = "",
        document_ast: dict[str, Any] | None = None,
        figures: list[tuple[FigureMetadata, bytes]] | None = None,
        tables_count: int = 0,
        equations_count: int = 0,
    ) -> CacheMetadata:
        """Populate cache directory for doc_id atomically / cleanly."""
        target_dir = self.doc_cache_dir(doc_id)
        if target_dir.exists():
            shutil.rmtree(target_dir)
        target_dir.mkdir(parents=True, exist_ok=True)

        # Write content.md
        self.content_path(doc_id).write_text(content, encoding="utf-8")

        # Write document.json if provided
        if document_ast is not None:
            self.document_json_path(doc_id).write_text(
                json.dumps(document_ast, indent=2), encoding="utf-8"
            )

        # Save figures if any
        fig_count = 0
        if figures:
            fig_dir = self.figures_dir(doc_id)
            fig_dir.mkdir(parents=True, exist_ok=True)
            for fig_meta, img_bytes in figures:
                # Save PNG
                img_path = self.kb_root / fig_meta.image_path
                img_path.parent.mkdir(parents=True, exist_ok=True)
                img_path.write_bytes(img_bytes)

                # Save sidecar metadata JSON
                fig_meta_file = fig_dir / f"{fig_meta.id}.meta.json"
                fig_meta_file.write_text(
                    json.dumps(asdict(fig_meta), indent=2) + "\n",
                    encoding="utf-8",
                )
                fig_count += 1

        meta = CacheMetadata(
            doc_id=doc_id,
            source_hash=source_hash,
            extractor=extractor,
            extractor_version=extractor_version,
            extracted_at=_utcnow(),
            has_figures=fig_count > 0,
            figure_count=fig_count,
            tables_count=tables_count,
            equations_count=equations_count,
        )

        self.meta_path(doc_id).write_text(
            json.dumps(meta.to_dict(), indent=2) + "\n", encoding="utf-8"
        )
        return meta

    def invalidate(self, doc_id: str) -> bool:
        """Remove cache directory for doc_id."""
        target_dir = self.doc_cache_dir(doc_id)
        if target_dir.exists():
            shutil.rmtree(target_dir)
            return True
        return False

    def clean_all(self) -> int:
        """Purge cache for all documents. Returns count of purged document cache dirs."""
        if not self.cache_root.exists():
            return 0
        count = 0
        for entry in self.cache_root.iterdir():
            if entry.is_dir():
                shutil.rmtree(entry)
                count += 1
        return count
