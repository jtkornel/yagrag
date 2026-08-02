"""Hybrid retrieval: vector + full-text + graph, returning a context bundle.

The bundle is a stable JSON structure the agent can answer from:

    {
      "query": "...",
      "semantic": [ {chunk hit + score} ],
      "fulltext": [ {chunk hit + score} ],
      "entities": [ {graph entity + claims} ],
      "documents": [ {document reference} ]
    }

An empty/unbuilt index yields an empty-but-valid bundle (no crash).
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from ..config import KBConfig
from ..graph.connection import GraphDB
from ..store.documents import DocumentStore, StoreError
from .embedder import get_embedder
from .indexer import CHUNK_TABLE, FTS_INDEX, VECTOR_INDEX, _load_extensions


def _chunk_hit(row: dict[str, Any], score_key: str) -> dict[str, Any]:
    return {
        "chunk_id": row["id"],
        "kind": row["kind"],
        "ref": row["ref"],
        "label": row["label"],
        "text": row["text"],
        "score": float(row[score_key]),
    }


def _vector_hits(g: GraphDB, vector: list[float], limit: int) -> list[dict[str, Any]]:
    rows = g.execute(
        f"CALL QUERY_VECTOR_INDEX('{CHUNK_TABLE}', '{VECTOR_INDEX}', $v, $k) "
        "RETURN node.id AS id, node.kind AS kind, node.ref AS ref, "
        "node.label AS label, node.text AS text, distance "
        "ORDER BY distance",
        {"v": vector, "k": limit},
    )
    return [_chunk_hit(r, "distance") for r in rows]


def _fts_hits(g: GraphDB, query: str, limit: int) -> list[dict[str, Any]]:
    rows = g.execute(
        f"CALL QUERY_FTS_INDEX('{CHUNK_TABLE}', '{FTS_INDEX}', $q) "
        "RETURN node.id AS id, node.kind AS kind, node.ref AS ref, "
        "node.label AS label, node.text AS text, score "
        "ORDER BY score DESC LIMIT $k",
        {"q": query, "k": limit},
    )
    return [_chunk_hit(r, "score") for r in rows]


def _entity_details(g: GraphDB, label: str, entity_id: str) -> dict[str, Any]:
    """Fetch an entity's properties plus any claims about it."""
    rows = g.execute(
        f"MATCH (n:{label} {{id: $id}}) RETURN n.id AS id, n.name AS name, "
        "coalesce(n.summary, '') AS summary, n.origin AS origin, "
        "n.sources AS sources",
        {"id": entity_id},
    )
    if not rows:
        return {"label": label, "id": entity_id}
    detail: dict[str, Any] = {"label": label, **rows[0]}
    try:
        claims = g.execute(
            f"MATCH (cl:Claim)-[:ABOUT]->(n:{label} {{id: $id}}) "
            "RETURN cl.id AS id, cl.predicate AS predicate, "
            "coalesce(cl.object_literal, '') AS object_literal, "
            "cl.sources AS sources, cl.confidence AS confidence",
            {"id": entity_id},
        )
    except RuntimeError:
        claims = []  # no Claim/ABOUT tables in this schema
    detail["claims"] = claims
    return detail


def search(
    kb_root: Path,
    query: str,
    limit: int = 5,
    config: KBConfig | None = None,
) -> dict[str, Any]:
    """Run hybrid retrieval and assemble the context bundle."""
    kb_root = kb_root.expanduser().resolve()
    config = config or KBConfig.load(kb_root)

    bundle: dict[str, Any] = {
        "query": query,
        "semantic": [],
        "fulltext": [],
        "entities": [],
        "documents": [],
    }

    with GraphDB(kb_root / config.paths.graph_db) as g:
        if CHUNK_TABLE not in g.node_table_names():
            return bundle  # index not built yet — empty but valid
        _load_extensions(g)

        embedder = get_embedder(config.embedder)
        vector = embedder.embed([query])[0]
        bundle["semantic"] = _vector_hits(g, vector, limit)
        try:
            bundle["fulltext"] = _fts_hits(g, query, limit)
        except RuntimeError:
            bundle["fulltext"] = []  # FTS index missing — degrade gracefully

        # Graph entities appearing in any hit, enriched with their claims.
        seen: set[tuple[str, str]] = set()
        for hit in bundle["semantic"] + bundle["fulltext"]:
            if hit["kind"] == "entity" and (hit["label"], hit["ref"]) not in seen:
                seen.add((hit["label"], hit["ref"]))
                bundle["entities"].append(
                    _entity_details(g, hit["label"], hit["ref"])
                )

    # Document references for all document hits.
    store = DocumentStore(kb_root, config)
    doc_ids = {
        hit["ref"]
        for hit in bundle["semantic"] + bundle["fulltext"]
        if hit["kind"] == "document"
    }
    for doc_id in sorted(doc_ids):
        try:
            rec = store.get(doc_id)
        except StoreError:
            continue  # stale index entry
        bundle["documents"].append(
            {
                "id": rec.id,
                "kind": rec.kind,
                "title": rec.title,
                "path": rec.path,
                "sources": list(rec.sources),
            }
        )
    return bundle
