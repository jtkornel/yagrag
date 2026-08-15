"""Index build: chunk documents, embed, and create vector + FTS indexes.

Indexed content lives in an internal `_kb_chunk` node label (one row per text
chunk, pointing back to its document id). Rebuilding is destructive-safe: the
chunk nodes and indexes are dropped and recreated from the current
document store, so the index is always a pure function of the stored docs.

Graph entities are also indexed: every node with `id`/`name` properties in a
user table contributes one chunk (`kind = 'entity'`) built from its name and
summary, so hybrid search can surface graph entities as well as documents.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from ..config import KBConfig
from ..graph.connection import GraphDB
from ..schema.migrations import MIGRATIONS_TABLE
from ..store.documents import DocumentStore
from .embedder import Embedder, get_embedder

CHUNK_TABLE = "_kb_chunk"
VECTOR_INDEX = "kb_chunk_vec"
FTS_INDEX = "kb_chunk_fts"

# Node tables that never contribute entity chunks.
_NON_ENTITY_TABLES = {MIGRATIONS_TABLE, CHUNK_TABLE, "Claim", "Document"}


class IndexError_(RuntimeError):
    """Raised when index build or search fails."""


def chunk_text(text: str, max_chars: int = 1200) -> list[str]:
    """Split text into paragraph-aligned chunks of at most `max_chars`."""
    paragraphs = [p.strip() for p in text.split("\n\n") if p.strip()]
    chunks: list[str] = []
    current = ""
    for para in paragraphs:
        if current and len(current) + len(para) + 2 > max_chars:
            chunks.append(current)
            current = para
        else:
            current = f"{current}\n\n{para}" if current else para
        while len(current) > max_chars:
            chunks.append(current[:max_chars])
            current = current[max_chars:]
    if current:
        chunks.append(current)
    return chunks


@dataclass(frozen=True)
class IndexStats:
    documents: int
    entities: int
    chunks: int
    backend: str
    model: str
    dim: int

    def to_dict(self) -> dict[str, Any]:
        return {
            "documents": self.documents,
            "entities": self.entities,
            "chunks": self.chunks,
            "backend": self.backend,
            "model": self.model,
            "dim": self.dim,
        }


def _load_extensions(g: GraphDB) -> None:
    """Grafeo provides native vector and full-text search without dynamic extension loading."""
    pass


def _drop_existing(g: GraphDB) -> None:
    try:
        g.execute(f"MATCH (c:{CHUNK_TABLE}) DETACH DELETE c")
    except Exception:  # noqa: BLE001
        pass
    if hasattr(g._db, "drop_vector_index"):
        try:
            g._db.drop_vector_index(CHUNK_TABLE, "embedding")
        except Exception:  # noqa: BLE001
            pass
    if hasattr(g._db, "drop_text_index"):
        try:
            g._db.drop_text_index(CHUNK_TABLE, "text")
        except Exception:  # noqa: BLE001
            pass


def _entity_rows(g: GraphDB) -> list[dict[str, Any]]:
    """Collect (table, id, name, summary) for all entity nodes in user tables."""
    rows: list[dict[str, Any]] = []
    for table_raw in g.node_table_names():
        table = str(table_raw)
        if not table or table in _NON_ENTITY_TABLES or table.startswith("_"):
            continue
        try:
            result = g.execute(
                f"MATCH (n:{table}) RETURN n.id AS id, n.name AS name, "
                "coalesce(n.summary, '') AS summary"
            )
        except (RuntimeError, Exception):  # noqa: BLE001
            continue  # table without id/name properties — skip
        for r in result:
            if r.get("id") and r.get("name"):
                rows.append({"table": table, **r})
    return rows


def build_index(kb_root: Path, config: KBConfig | None = None) -> IndexStats:
    """(Re)build the chunk table, embeddings, and vector/FTS indexes."""
    kb_root = kb_root.expanduser().resolve()
    config = config or KBConfig.load(kb_root)
    embedder: Embedder = get_embedder(config.embedder)
    store = DocumentStore(kb_root, config)

    # Gather chunks from documents ...
    chunks: list[dict[str, Any]] = []
    records = store.records()
    for rec in records:
        try:
            text = store.extract_text(rec.id)
        except RuntimeError as exc:
            raise IndexError_(f"cannot extract text from {rec.id}: {exc}") from exc
        for i, chunk in enumerate(chunk_text(text)):
            chunks.append(
                {
                    "id": f"{rec.id}#{i}",
                    "kind": "document",
                    "ref": rec.id,
                    "label": "",
                    "text": chunk,
                }
            )

    with GraphDB(kb_root / config.paths.graph_db) as g:
        _load_extensions(g)

        # ... and from graph entities (name + summary).
        entities = _entity_rows(g)
        for ent in entities:
            text = f"{ent['name']}. {ent['summary']}".strip()
            chunks.append(
                {
                    "id": f"{ent['table']}:{ent['id']}",
                    "kind": "entity",
                    "ref": ent["id"],
                    "label": ent["table"],
                    "text": text,
                }
            )

        _drop_existing(g)

        if chunks:
            vectors = embedder.embed([c["text"] for c in chunks])
            try:
                g._db.create_vector_index(
                    CHUNK_TABLE, "embedding", dimensions=embedder.dim, metric="cosine"
                )
            except Exception:  # noqa: BLE001
                pass
            chunk_items = []
            for chunk, vec in zip(chunks, vectors, strict=False):
                chunk_items.append({
                    **chunk,
                    "embedding": vec,
                    "embedder": f"{embedder.backend}/{embedder.model}",
                })
            try:
                g._db.batch_create_nodes_with_props(CHUNK_TABLE, chunk_items)
            except Exception:
                for item in chunk_items:
                    g._db.create_node([CHUNK_TABLE], item)
            try:
                g._db.create_text_index(CHUNK_TABLE, "text")
            except Exception:  # noqa: BLE001
                pass

    return IndexStats(
        documents=len(records),
        entities=len(entities),
        chunks=len(chunks),
        backend=embedder.backend,
        model=embedder.model,
        dim=embedder.dim,
    )
