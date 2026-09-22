"""Filesystem document store: raw vs synthesized documents + manifest.

Raw documents are immutable once ingested: the source file is copied into
`documents/raw/` under a content-addressed filename and never modified.
Synthesized documents (agent-generated) live in `documents/synthesized/` and
must declare the source document ids they are derived from.

A JSON manifest (`documents/manifest.json`) indexes all documents by id and
content hash, enabling duplicate detection and fast listing without walking
the tree.
"""

from __future__ import annotations

import contextlib
import hashlib
import io
import json
import shutil
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal

import docling
from docling.datamodel.base_models import InputFormat
from docling.datamodel.pipeline_options import PdfPipelineOptions
from docling.document_converter import DocumentConverter, PdfFormatOption

from ..config import KBConfig
from .cache import CacheManager, FigureMetadata

DocKind = Literal["raw", "synthesized"]

# Formats natively supported for ingestion. HTML is deliberately absent:
# web pages are saved as clean Markdown/text first (see the agent howto skill).
SUPPORTED_EXTENSIONS = {".md", ".markdown", ".txt", ".text", ".pdf"}


class StoreError(RuntimeError):
    """Raised for document-store violations (bad kind, duplicates, etc.)."""


class DuplicateDocumentError(StoreError):
    """Raised when ingesting a file whose content hash is already stored."""


class MissingDOIError(StoreError):
    """Raised when ingesting a raw document without a DOI when require_doi is enabled."""


class UnsupportedFormatError(StoreError):
    """Raised for file formats the store does not natively support."""


def content_hash(path: Path) -> str:
    """SHA-256 of file content, hex-encoded."""
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _utcnow() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


@dataclass(frozen=True)
class DocumentRecord:
    """One manifest entry."""

    id: str
    kind: DocKind
    title: str
    path: str  # relative to KB root
    hash: str
    format: str  # md | txt | pdf
    added_at: str
    sources: tuple[str, ...] = ()  # source doc ids (synthesized only)
    tags: tuple[str, ...] = ()
    notes: str = ""
    url: str = ""
    doi: str = ""

    def to_dict(self) -> dict[str, Any]:
        d: dict[str, Any] = {
            "id": self.id,
            "kind": self.kind,
            "title": self.title,
            "path": self.path,
            "hash": self.hash,
            "format": self.format,
            "added_at": self.added_at,
            "sources": list(self.sources),
            "tags": list(self.tags),
            "notes": self.notes,
            "url": self.url,
        }
        if self.doi:
            d["doi"] = self.doi
        return d

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> DocumentRecord:
        return cls(
            id=d["id"],
            kind=d["kind"],
            title=d["title"],
            path=d["path"],
            hash=d["hash"],
            format=d["format"],
            added_at=d["added_at"],
            sources=tuple(d.get("sources", [])),
            tags=tuple(d.get("tags", [])),
            notes=d.get("notes", ""),
            url=d.get("url", ""),
            doi=d.get("doi", ""),
        )


def _format_for(path: Path) -> str:
    ext = path.suffix.lower()
    if ext not in SUPPORTED_EXTENSIONS:
        raise UnsupportedFormatError(
            f"unsupported document format {ext!r} for {path.name}; "
            f"supported: {', '.join(sorted(SUPPORTED_EXTENSIONS))}. "
            "For web pages, save as clean Markdown/text first."
        )
    if ext == ".pdf":
        return "pdf"
    if ext in (".txt", ".text"):
        return "txt"
    return "md"


class DocumentStore:
    """Manifest-backed document store rooted at a KB directory."""

    def __init__(self, kb_root: Path, config: KBConfig | None = None):
        self.kb_root = kb_root.expanduser().resolve()
        self.config = config or KBConfig.load(self.kb_root)
        self.manifest_path = self.kb_root / self.config.paths.manifest
        self.cache = CacheManager(
            self.kb_root,
            cache_rel_path=getattr(self.config.paths, "cache", "documents/cache"),
        )

    # --- manifest ------------------------------------------------------------

    def _load_manifest(self) -> dict[str, Any]:
        if not self.manifest_path.is_file():
            raise StoreError(f"manifest not found at {self.manifest_path}; run `kb init`")
        with self.manifest_path.open("r", encoding="utf-8") as f:
            return json.load(f)

    def _save_manifest(self, manifest: dict[str, Any]) -> None:
        self.manifest_path.write_text(
            json.dumps(manifest, indent=2) + "\n", encoding="utf-8"
        )

    def records(self) -> list[DocumentRecord]:
        manifest = self._load_manifest()
        return [DocumentRecord.from_dict(d) for d in manifest["documents"]]

    def get(self, doc_id: str) -> DocumentRecord:
        for rec in self.records():
            if rec.id == doc_id:
                return rec
        raise StoreError(f"no document with id {doc_id!r}")

    def find_by_hash(self, digest: str) -> DocumentRecord | None:
        for rec in self.records():
            if rec.hash == digest:
                return rec
        return None

    # --- id / path allocation --------------------------------------------------

    def _next_id(self, kind: DocKind) -> str:
        prefix = "raw" if kind == "raw" else "syn"
        nums = [
            int(rec.id.split("-", 1)[1])
            for rec in self.records()
            if rec.id.startswith(f"{prefix}-") and rec.id.split("-", 1)[1].isdigit()
        ]
        return f"{prefix}-{(max(nums) + 1) if nums else 1:04d}"

    def _kind_dir(self, kind: DocKind) -> Path:
        if kind == "raw":
            return self.kb_root / self.config.paths.raw
        if kind == "synthesized":
            return self.kb_root / self.config.paths.synthesized
        raise StoreError(f"invalid document kind: {kind!r}")

    # --- add -----------------------------------------------------------------

    def add(
        self,
        source: Path,
        kind: DocKind,
        title: str | None = None,
        sources: list[str] | None = None,
        tags: list[str] | None = None,
        notes: str = "",
        url: str = "",
        doi: str = "",
        allow_no_doi: bool = False,
    ) -> DocumentRecord:
        """Ingest `source` as a document of `kind`. Returns the new record.

        Raw documents must not declare `sources` (they *are* sources);
        synthesized documents must declare at least one source doc id
        (provenance is mandatory).
        """
        if kind not in ("raw", "synthesized"):
            raise StoreError(f"invalid document kind: {kind!r}")
        source = source.expanduser().resolve()
        if not source.is_file():
            raise StoreError(f"no such file: {source}")
        fmt = _format_for(source)

        sources = sources or []
        if kind == "raw" and sources:
            raise StoreError("raw documents must not declare sources (they are sources)")
        if kind == "synthesized":
            if not sources:
                raise StoreError(
                    "synthesized documents must declare at least one source document id"
                )
            known = {rec.id for rec in self.records()}
            missing = [s for s in sources if s not in known]
            if missing:
                raise StoreError(f"unknown source document id(s): {', '.join(missing)}")

        # DOI enforcement: raw documents require a DOI by default
        doi = doi.strip()
        if kind == "raw" and self.config.documents.require_doi and not allow_no_doi and not doi:
            raise MissingDOIError(
                "raw documents require a DOI for traceability and remote retrieval; "
                "pass --doi <doi>, or use --no-doi / --allow-no-doi to bypass"
            )

        digest = content_hash(source)
        existing = self.find_by_hash(digest)
        if existing is not None:
            raise DuplicateDocumentError(
                f"identical content already stored as {existing.id} ({existing.path})"
            )

        doc_id = self._next_id(kind)
        dest_dir = self._kind_dir(kind)
        dest_dir.mkdir(parents=True, exist_ok=True)
        dest = dest_dir / f"{doc_id}_{source.name}"
        if dest.exists():
            raise StoreError(f"destination already exists: {dest}")
        shutil.copy2(source, dest)
        if kind == "raw":
            # Raw documents are immutable: drop write permission bits.
            dest.chmod(dest.stat().st_mode & 0o555)

        record = DocumentRecord(
            id=doc_id,
            kind=kind,
            title=title or source.stem,
            path=str(dest.relative_to(self.kb_root)),
            hash=digest,
            format=fmt,
            added_at=_utcnow(),
            sources=tuple(sources),
            tags=tuple(tags or []),
            notes=notes,
            url=url,
            doi=doi,
        )
        # Sidecar metadata next to the stored file (self-describing tree).
        meta_path = dest.with_name(dest.name + ".meta.json")
        meta_path.write_text(
            json.dumps(record.to_dict(), indent=2) + "\n", encoding="utf-8"
        )

        manifest = self._load_manifest()
        manifest["documents"].append(record.to_dict())
        self._save_manifest(manifest)
        return record

    def update_record(self, doc_id: str, **fields: Any) -> DocumentRecord:
        """Update metadata fields of an existing document in the manifest."""
        manifest = self._load_manifest()
        updated = None
        for i, doc in enumerate(manifest["documents"]):
            if doc["id"] == doc_id:
                for k, v in fields.items():
                    doc[k] = v
                manifest["documents"][i] = doc
                updated = DocumentRecord.from_dict(doc)
                break
        if updated is None:
            raise StoreError(f"no document with id {doc_id!r}")
        self._save_manifest(manifest)
        # Update sidecar metadata if file exists
        dest = self.kb_root / updated.path
        meta_path = dest.with_name(dest.name + ".meta.json")
        if meta_path.parent.exists():
            meta_path.write_text(
                json.dumps(updated.to_dict(), indent=2) + "\n", encoding="utf-8"
            )
        return updated

    # --- remove ----------------------------------------------------------------

    def remove(self, doc_id: str) -> DocumentRecord:
        """Remove a document from the store and manifest.

        Refuses to remove a raw document that other documents derive from.
        """
        rec = self.get(doc_id)
        dependents = [r.id for r in self.records() if doc_id in r.sources]
        if dependents:
            raise StoreError(
                f"cannot remove {doc_id}: derived documents depend on it "
                f"({', '.join(dependents)})"
            )
        stored = self.kb_root / rec.path
        if stored.exists():
            stored.chmod(0o644)
            stored.unlink()
        meta = stored.with_name(stored.name + ".meta.json")
        if meta.exists():
            meta.unlink()
        # Invalidate any cached extraction artifacts
        self.cache.invalidate(doc_id)
        manifest = self._load_manifest()
        manifest["documents"] = [
            d for d in manifest["documents"] if d["id"] != doc_id
        ]
        self._save_manifest(manifest)
        return rec

    # --- text extraction & parsing ----------------------------------------------

    def parse_document(self, doc_id: str, force: bool = False) -> str:
        """Parse a document and cache its markdown, AST, and figures.

        Uses Docling for layout and diagram aware extraction.
        Returns the extracted markdown content.
        """
        rec = self.get(doc_id)
        path = self.kb_root / rec.path

        # If cache is valid and not forced, return cached content
        if not force and self.cache.is_valid(doc_id, rec.hash):
            cached_text = self.cache.read_content(doc_id)
            if cached_text is not None:
                return cached_text

        docling_ver = getattr(docling, "__version__", "2.0")

        if rec.format in ("md", "txt"):
            converter = DocumentConverter()
            result = converter.convert(str(path))
            doc = result.document

            content_md = doc.export_to_markdown()

            try:
                doc_ast = doc.export_to_dict()
            except (RuntimeError, ValueError, TypeError):
                doc_ast = None

            self.cache.write_cache(
                doc_id=doc_id,
                source_hash=rec.hash,
                content=content_md,
                extractor="docling",
                extractor_version=docling_ver,
                document_ast=doc_ast,
                figures=None,
            )
            return content_md

        if rec.format == "pdf":
            pipeline_options = PdfPipelineOptions()
            pipeline_options.do_ocr = False
            pipeline_options.generate_picture_images = bool(
                self.config.documents.extract_figures
            )
            pipeline_options.images_scale = float(self.config.documents.figure_dpi) / 72.0

            converter = DocumentConverter(
                format_options={
                    InputFormat.PDF: PdfFormatOption(pipeline_options=pipeline_options)
                }
            )

            result = converter.convert(str(path))
            doc = result.document

            # Extract markdown
            content_md = doc.export_to_markdown()

            # Extract figures
            extracted_figures: list[tuple[FigureMetadata, bytes]] = []
            if self.config.documents.extract_figures and hasattr(doc, "pictures"):
                fig_dir_rel = f"documents/cache/{doc_id}/figures"
                for idx, pic in enumerate(doc.pictures):
                    try:
                        pil_img = pic.get_image(doc)
                        if pil_img is None:
                            continue
                        buf = io.BytesIO()
                        pil_img.save(buf, format="PNG")
                        img_bytes = buf.getvalue()

                        page_no = 1
                        bbox_coords: list[float] = []
                        if hasattr(pic, "prov") and pic.prov:
                            p0 = pic.prov[0]
                            page_no = getattr(p0, "page_no", 1)
                            bb = getattr(p0, "bbox", None)
                            if bb is not None:
                                bbox_coords = [
                                    getattr(bb, "l", 0.0),
                                    getattr(bb, "t", 0.0),
                                    getattr(bb, "r", 0.0),
                                    getattr(bb, "b", 0.0),
                                ]

                        caption_text = ""
                        if hasattr(pic, "caption_text"):
                            with contextlib.suppress(Exception):
                                caption_text = pic.caption_text(doc) or ""

                        fig_id = f"fig_{idx:03d}_p{page_no}"
                        rel_img_path = f"{fig_dir_rel}/{fig_id}.png"
                        fig_meta = FigureMetadata(
                            id=fig_id,
                            page=page_no,
                            bbox=bbox_coords,
                            caption=caption_text,
                            image_path=rel_img_path,
                        )
                        extracted_figures.append((fig_meta, img_bytes))
                    except (OSError, RuntimeError, ValueError):
                        continue

            try:
                doc_ast = doc.export_to_dict()
            except (RuntimeError, ValueError, TypeError):
                doc_ast = None

            self.cache.write_cache(
                doc_id=doc_id,
                source_hash=rec.hash,
                content=content_md,
                extractor="docling",
                extractor_version=docling_ver,
                document_ast=doc_ast,
                figures=extracted_figures,
            )
            return content_md

        raise UnsupportedFormatError(f"cannot extract text from format {rec.format!r}")

    def extract_text(self, doc_id: str) -> str:
        """Return the plain-text/markdown content of a stored document.

        Checks the cache first. If cache is invalid or missing, parses the document.
        """
        rec = self.get(doc_id)
        if self.cache.is_valid(doc_id, rec.hash):
            cached = self.cache.read_content(doc_id)
            if cached is not None:
                return cached
        return self.parse_document(doc_id)
