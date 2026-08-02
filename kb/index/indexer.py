"""Index build: chunk documents, embed, and create Kuzu vector + FTS indexes.

Indexed content lives in an internal `_kb_chunk` node table (one row per text
chunk, pointing back to its document id). Rebuilding is destructive-safe: the
chunk table and its indexes are dropped and recreated from the current
document store, so the index is always a pure function of the stored docs.

Graph entities are also indexed: every node with `id`/`name` properties in a
user table contributes one chunk (`kind = 'entity'`) built from its name and
summary, so hybrid search can surface graph entities as well as documents.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from ..config import KBConfig
from ..graph.connection import GraphDB
from ..schema.migrations import MIGRATIONS_TABLE
from ..store.documents import DocumentStore
from .embedder import Embedder, get_embedder

CHUNK_TABLE = "_kb_chunk"
VECTOR_INDEX = "kb_chunk_vec"
FTS_INDEX = "kb_chunk_fts"

# Node tables that never contribute entity chunks.
_NON_ENTITY_TABLES = {MIGRATIONS_TABLE, CHUNK_TABLE, "Claim"}


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

    def to_dict(self) -> dict:
        return {
            "documents": self.documents,
            "entities": self.entities,
            "chunks": self.chunks,
            "backend": self.backend,
            "model": self.model,
            "dim": self.dim,
        }


def _load_extensions(g: GraphDB) -> None:
    for ext in ("vector", "fts"):
        try:
            g.execute_raw(f"INSTALL {ext}; LOAD EXTENSION {ext};")
        except RuntimeError as exc:
            raise IndexError_(f"failed to load Kuzu extension {ext!r}: {exc}") from exc


def _drop_existing(g: GraphDB) -> None:
    if CHUNK_TABLE in g.node_table_names():
        for stmt in (
            f"CALL DROP_VECTOR_INDEX('{CHUNK_TABLE}', '{VECTOR_INDEX}')",
            f"CALL DROP_FTS_INDEX('{CHUNK_TABLE}', '{FTS_INDEX}')",
        ):
            try:
                g.execute_raw(stmt)
            except RuntimeError:
                pass  # index may not exist yet
        g.execute_raw(f"DROP TABLE {CHUNK_TABLE}")


def _entity_rows(g: GraphDB) -> list[dict]:
    """Collect (table, id, name, summary) for all entity nodes in user tables."""
    rows: list[dict] = []
    for table in g.node_table_names():
        if table in _NON_ENTITY_TABLES or table.startswith("_"):
            continue
        try:
            result = g.execute(
                f"MATCH (n:{table}) RETURN n.id AS id, n.name AS name, "
                "coalesce(n.summary, '') AS summary"
            )
        except RuntimeError:
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
    chunks: list[dict] = []
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
        g.execute_raw(
            f"CREATE NODE TABLE {CHUNK_TABLE}("
            "id STRING, kind STRING, ref STRING, label STRING, text STRING, "
            f"embedding FLOAT[{embedder.dim}], embedder STRING, "
            "PRIMARY KEY(id))"
        )

        if chunks:
            vectors = embedder.embed([c["text"] for c in chunks])
            for chunk, vec in zip(chunks, vectors):
                g.execute(
                    f"CREATE (:{CHUNK_TABLE} {{id: $id, kind: $kind, ref: $ref, "
                    "label: $label, text: $text, embedding: $embedding, "
                    "embedder: $embedder})",
                    {
                        **chunk,
                        "embedding": vec,
                        "embedder": f"{embedder.backend}/{embedder.model}",
                    },
                )

        g.execute_raw(
            f"CALL CREATE_VECTOR_INDEX('{CHUNK_TABLE}', '{VECTOR_INDEX}', 'embedding')"
        )
        g.execute_raw(
            f"CALL CREATE_FTS_INDEX('{CHUNK_TABLE}', '{FTS_INDEX}', ['text'])"
        )

    return IndexStats(
        documents=len(records),
        entities=len(entities),
        chunks=len(chunks),
        backend=embedder.backend,
        model=embedder.model,
        dim=embedder.dim,
    )
