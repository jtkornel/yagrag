"""Provenance-enforcing upsert helpers for nodes, edges, and reified claims.

All writes go through these helpers, which reject payloads lacking provenance
(`origin` + non-empty `sources`). `upsert_node`/`upsert_edge` use Cypher MERGE
so re-running with the same id updates rather than duplicates. `upsert_claim`
is a convenience wrapper creating a `Claim` node plus its `ABOUT`/`HAS_OBJECT`
edges in one call.
"""

from __future__ import annotations

from typing import Any

from .connection import GraphDB


VALID_ORIGINS = ("raw", "synthesized", "inferred")


class ProvenanceError(ValueError):
    """Raised when a graph write lacks required provenance fields."""


class GraphWriteError(RuntimeError):
    """Raised when a graph write fails (unknown table, bad reference, ...)."""


def check_provenance(props: dict[str, Any]) -> None:
    """Validate that `props` carries origin + non-empty sources."""
    origin = props.get("origin")
    if origin not in VALID_ORIGINS:
        raise ProvenanceError(
            f"missing or invalid 'origin' (got {origin!r}; "
            f"expected one of {', '.join(VALID_ORIGINS)})"
        )
    sources = props.get("sources")
    if not isinstance(sources, list) or not sources or not all(
        isinstance(s, str) and s for s in sources
    ):
        raise ProvenanceError(
            "missing or invalid 'sources' (expected a non-empty list of document ids)"
        )


def _quote_ident(name: str) -> str:
    if not name.replace("_", "").isalnum():
        raise GraphWriteError(f"invalid identifier: {name!r}")
    return name


def _set_clause(var: str, props: dict[str, Any], skip: tuple[str, ...] = ()) -> tuple[str, dict[str, Any]]:
    """Render `SET var.k = $p_k, ...` and matching parameters."""
    parts: list[str] = []
    params: dict[str, Any] = {}
    for key, value in props.items():
        if key in skip:
            continue
        _quote_ident(key)
        parts.append(f"{var}.{key} = $p_{key}")
        params[f"p_{key}"] = value
    return ", ".join(parts), params


def upsert_node(g: GraphDB, label: str, props: dict[str, Any]) -> dict[str, Any]:
    """MERGE a node of `label` by `id`, setting all other properties.

    Requires `id`, `origin`, and non-empty `sources` in `props`. Timestamps
    are managed automatically (`created_at` on create, `updated_at` always).
    """
    label = _quote_ident(label)
    node_id = props.get("id")
    if not node_id or not isinstance(node_id, str):
        raise GraphWriteError("node upsert requires a string 'id' property")
    check_provenance(props)

    set_clause, params = _set_clause("n", props, skip=("id",))
    params["id"] = node_id
    cypher = (
        f"MERGE (n:{label} {{id: $id}}) "
        "ON CREATE SET n.created_at = current_timestamp() "
        f"SET {set_clause}, n.updated_at = current_timestamp() "
        "RETURN n.id AS id"
    )
    try:
        rows = g.execute(cypher, params)
    except RuntimeError as exc:
        raise GraphWriteError(f"node upsert failed for {label}: {exc}") from exc
    return {"label": label, "id": rows[0]["id"] if rows else node_id}


def upsert_edge(
    g: GraphDB,
    rel: str,
    from_label: str,
    from_id: str,
    to_label: str,
    to_id: str,
    props: dict[str, Any],
) -> dict[str, Any]:
    """MERGE an edge of type `rel` between two existing nodes.

    Requires provenance in `props`. Fails if either endpoint does not exist.
    """
    rel = _quote_ident(rel)
    from_label = _quote_ident(from_label)
    to_label = _quote_ident(to_label)
    check_provenance(props)

    # Verify endpoints exist so MERGE cannot silently no-op.
    for label, node_id, side in (
        (from_label, from_id, "from"),
        (to_label, to_id, "to"),
    ):
        rows = g.execute(
            f"MATCH (n:{label} {{id: $id}}) RETURN n.id AS id", {"id": node_id}
        )
        if not rows:
            raise GraphWriteError(
                f"{side}-node not found: {label} with id {node_id!r}"
            )

    set_clause, params = _set_clause("r", props)
    params["from_id"] = from_id
    params["to_id"] = to_id
    cypher = (
        f"MATCH (a:{from_label} {{id: $from_id}}), (b:{to_label} {{id: $to_id}}) "
        f"MERGE (a)-[r:{rel}]->(b) "
        "ON CREATE SET r.created_at = current_timestamp() "
        f"SET {set_clause}, r.updated_at = current_timestamp() "
        "RETURN a.id AS from_id, b.id AS to_id"
    )
    try:
        rows = g.execute(cypher, params)
    except RuntimeError as exc:
        raise GraphWriteError(f"edge upsert failed for {rel}: {exc}") from exc
    return {"rel": rel, "from": from_id, "to": to_id}


def upsert_claim(
    g: GraphDB,
    claim_id: str,
    subject_label: str,
    subject_id: str,
    predicate: str,
    props: dict[str, Any],
    object_label: str | None = None,
    object_id: str | None = None,
    object_literal: str | None = None,
) -> dict[str, Any]:
    """Create/update a reified `Claim` node plus its `ABOUT`/`HAS_OBJECT` edges.

    The object is either another entity (`object_label` + `object_id`) or a
    literal string (`object_literal`). Provenance in `props` is mandatory and
    is propagated to the created edges.
    """
    if (object_id is None) == (object_literal is None):
        raise GraphWriteError(
            "claim requires exactly one of (object_label + object_id) or object_literal"
        )
    if object_id is not None and object_label is None:
        raise GraphWriteError("object_id requires object_label")
    check_provenance(props)

    node_props = dict(props)
    node_props["id"] = claim_id
    node_props["predicate"] = predicate
    if object_literal is not None:
        node_props["object_literal"] = object_literal
    upsert_node(g, "Claim", node_props)

    edge_props = {
        "origin": props["origin"],
        "sources": props["sources"],
    }
    if "confidence" in props:
        edge_props["confidence"] = props["confidence"]
    upsert_edge(g, "ABOUT", "Claim", claim_id, subject_label, subject_id, edge_props)
    if object_id is not None:
        assert object_label is not None
        upsert_edge(
            g, "HAS_OBJECT", "Claim", claim_id, object_label, object_id, edge_props
        )
    return {"id": claim_id, "subject": subject_id, "predicate": predicate}
