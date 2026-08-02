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

import hashlib
import json
import shutil
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Literal

from ..config import KBConfig


DocKind = Literal["raw", "synthesized"]

# Formats natively supported for ingestion. HTML is deliberately absent:
# web pages are saved as clean Markdown/text first (see the agent howto skill).
SUPPORTED_EXTENSIONS = {".md", ".markdown", ".txt", ".text", ".pdf"}


class StoreError(RuntimeError):
    """Raised for document-store violations (bad kind, duplicates, etc.)."""


class DuplicateDocumentError(StoreError):
    """Raised when ingesting a file whose content hash is already stored."""


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
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


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

    def to_dict(self) -> dict[str, Any]:
        return {
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

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "DocumentRecord":
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
        manifest = self._load_manifest()
        manifest["documents"] = [
            d for d in manifest["documents"] if d["id"] != doc_id
        ]
        self._save_manifest(manifest)
        return rec

    # --- text extraction ---------------------------------------------------------

    def extract_text(self, doc_id: str) -> str:
        """Return the plain-text content of a stored document.

        Markdown/text files are read as-is; PDFs are extracted with pypdf.
        """
        rec = self.get(doc_id)
        path = self.kb_root / rec.path
        if rec.format in ("md", "txt"):
            return path.read_text(encoding="utf-8")
        if rec.format == "pdf":
            try:
                from pypdf import PdfReader  # type: ignore
            except ImportError as exc:
                raise StoreError(
                    "pypdf is not installed; install with `pip install .[pdf]`"
                ) from exc
            reader = PdfReader(str(path))
            return "\n\n".join((page.extract_text() or "") for page in reader.pages)
        raise UnsupportedFormatError(f"cannot extract text from format {rec.format!r}")
