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

import contextlib
from pathlib import Path
from typing import Any, cast

from ..config import KBConfig
from ..graph.connection import GraphDB
from ..store.documents import DocumentStore, StoreError
from .embedder import get_embedder
from .indexer import CHUNK_TABLE, _load_extensions


def _chunk_hit(row: dict[str, Any], score_key: str) -> dict[str, Any]:
    score_val = row.get(score_key, 1.0)
    try:
        score = float(score_val)
    except (ValueError, TypeError):
        score = 1.0
    return {
        "chunk_id": row["id"],
        "kind": row["kind"],
        "ref": row["ref"],
        "label": row["label"],
        "text": row["text"],
        "score": score,
    }


def _extract_node_props(node: Any) -> dict[str, Any]:
    if hasattr(node, "properties") and callable(node.properties):
        return cast(dict[str, Any], node.properties())
    if hasattr(node, "properties") and isinstance(node.properties, dict):
        return cast(dict[str, Any], node.properties)
    if isinstance(node, dict):
        return node
    return dict(node)


def _vector_hits(g: GraphDB, vector: list[float], limit: int) -> list[dict[str, Any]]:
    if g._db is not None:
        with contextlib.suppress(Exception):
            results = g._db.vector_search(CHUNK_TABLE, "embedding", vector, k=limit)
            hits = []
            for node_id, dist in results:
                node = g._db.get_node(node_id)
                props = _extract_node_props(node)
                hits.append({
                    "chunk_id": props.get("id"),
                    "kind": props.get("kind"),
                    "ref": props.get("ref"),
                    "label": props.get("label"),
                    "text": props.get("text"),
                    "score": float(dist),
                })
            if hits:
                return hits
        with contextlib.suppress(Exception):
            rows = g.execute(
                f"MATCH (node:{CHUNK_TABLE}) "
                "RETURN node.id AS id, node.kind AS kind, node.ref AS ref, "
                "node.label AS label, node.text AS text "
                "LIMIT $k",
                {"k": limit},
            )
            return [
                {
                    "chunk_id": r.get("id"),
                    "kind": r.get("kind"),
                    "ref": r.get("ref"),
                    "label": r.get("label"),
                    "text": r.get("text"),
                    "score": 1.0,
                }
                for r in rows
            ]
    return []


def _fts_hits(g: GraphDB, query: str, limit: int) -> list[dict[str, Any]]:
    if g._db is not None:
        with contextlib.suppress(Exception):
            results = g._db.text_search(CHUNK_TABLE, "text", query, k=limit)
            hits = []
            for node_id, score in results:
                node = g._db.get_node(node_id)
                props = _extract_node_props(node)
                hits.append({
                    "chunk_id": props.get("id"),
                    "kind": props.get("kind"),
                    "ref": props.get("ref"),
                    "label": props.get("label"),
                    "text": props.get("text"),
                    "score": float(score),
                })
            if hits:
                return hits
        with contextlib.suppress(Exception):
            rows = g.execute(
                f"MATCH (node:{CHUNK_TABLE}) "
                "WHERE node.text CONTAINS $q "
                "RETURN node.id AS id, node.kind AS kind, node.ref AS ref, "
                "node.label AS label, node.text AS text "
                "LIMIT $k",
                {"q": query, "k": limit},
            )
            return [
                {
                    "chunk_id": r.get("id"),
                    "kind": r.get("kind"),
                    "ref": r.get("ref"),
                    "label": r.get("label"),
                    "text": r.get("text"),
                    "score": 1.0,
                }
                for r in rows
            ]
    return []


def _entity_details(g: GraphDB, label: str, entity_id: str) -> dict[str, Any]:
    """Fetch an entity's properties plus any claims and acronyms linked to it."""
    rows = g.execute(
        f"MATCH (n:{label} {{id: $id}}) RETURN n.id AS id, n.name AS name, "
        "coalesce(n.summary, '') AS summary, n.origin AS origin, "
        "n.sources AS sources",
        {"id": entity_id},
    )
    if not rows:
        return {"label": label, "id": entity_id}
    detail: dict[str, Any] = {"label": label, **rows[0]}
    if label == "Acronym":
        info: list[dict[str, Any]] = []
        with contextlib.suppress(Exception):
            info = g.execute(
                "MATCH (a:Acronym {id: $id}) RETURN a.short_form AS short_form, "
                "a.expansion AS expansion, a.domain_context AS domain_context",
                {"id": entity_id},
            )
        if info:
            detail["short_form"] = info[0].get("short_form")
            detail["expansion"] = info[0].get("expansion")
            if info[0].get("domain_context"):
                detail["domain_context"] = info[0].get("domain_context")

    try:
        claims = g.execute(
            f"MATCH (cl:Claim)-[:ABOUT]->(n:{label} {{id: $id}}) "
            "RETURN cl.id AS id, cl.predicate AS predicate, "
            "coalesce(cl.object_literal, '') AS object_literal, "
            "cl.sources AS sources, cl.confidence AS confidence",
            {"id": entity_id},
        )
    except (RuntimeError, Exception):
        claims = []  # no Claim/ABOUT tables in this schema
    detail["claims"] = claims

    acronyms: list[dict[str, Any]] = []
    with contextlib.suppress(Exception):
        acronyms = g.execute(
            f"MATCH (n:{label} {{id: $id}})-[:USES_ACRONYM]->(a:Acronym) "
            "RETURN a.id AS id, a.short_form AS short_form, a.expansion AS expansion",
            {"id": entity_id},
        )
    if acronyms:
        detail["acronyms"] = acronyms

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
        _load_extensions(g)

        embedder = get_embedder(config.embedder)
        vector = embedder.embed([query])[0]
        bundle["semantic"] = _vector_hits(g, vector, limit)
        try:
            bundle["fulltext"] = _fts_hits(g, query, limit)
        except (RuntimeError, Exception):  # noqa: BLE001
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
