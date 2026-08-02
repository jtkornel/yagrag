---
name: graph-update
description: Reconcile and update the knowledge graph with new facts while maintaining consistency. TRIGGER whenever you need to add information to the graph that might overlap with existing nodes or edges.
---

## When to use

Use this skill during `deep-knowledge-extraction` or when a user provides updates to existing facts. It ensures the graph doesn't become cluttered with duplicate nodes for the same real-world concept (e.g., two nodes for the "GTSAM" library).

Trigger this skill when:
*   You are about to upsert a node and need to check for existing records.
*   You find two nodes that refer to the same physical entity.
*   You encounter new evidence for an existing claim or relationship.
*   You need to handle conflicting information from different documents.

## Steps

1.  **Search First**: Before creating a new node, run `kb graph query` or `kb search` to see if the concept already exists. Use stable, slug-like IDs (e.g., `tool_gtsam`) rather than random UUIDs.
2.  **Reconcile Entities**:
    *   **If it exists**: Do not create a new node. Instead, update the existing node to include the new `doc_id` in its `sources` array. You should also merge or append to the `summary` property if the new document provides better context.
    *   **If it's new**: Use `kb graph upsert-node` to create the initial record.
3.  **Handle Claims**: If a new claim contradicts an existing one (e.g., two papers claim different accuracy for the same method):
    *   Store **both** claims as separate nodes with their own unique IDs.
    *   Ensure each claim has its own `origin`, `sources`, and `confidence` properties.
    *   This preserves the history of the disagreement in the field.
4.  **Update Edges**: Use `kb graph upsert-edge`. If an edge between two nodes already exists, updating it with a new source ID reinforces the relationship and increases overall graph confidence.
5.  **Merge Duplicates**: If you find two nodes that represent the exact same thing but have different IDs (e.g., `algo_ekf` and `ekf_slam_method`):
    *   Identify the most descriptive ID as the primary.
    *   Update all edges pointing to/from the duplicate to point to the primary.
    *   Once all edges are moved, the duplicate is safely isolated (mark it as such in its summary).

## Rules

*   **Merge by ID**: The `kb graph upsert-*` commands work as "merge" operations based on the ID. Providing an existing ID will update that record instead of creating a new one.
*   **No Overwriting Evidence**: Never delete a source ID from a node's `sources` list. The list should represent the union of all documents that support the entity's existence.
*   **Conflict Visibility**: Do not "resolve" conflicting claims by picking a winner. The graph must reflect the raw state of human knowledge, including its contradictions.
*   **Stable IDs**: Prefer meaningful, lowercase, underscored IDs (e.g., `algo_ekf_slam`) over internal system hashes. This makes the Cypher queries more readable.
*   **Confidence Updates**: If multiple high-quality sources agree on a fact, consider slightly increasing the `confidence` score (e.g., from 0.9 to 0.95).

## Example

Reinforcing a node with a new source.

```bash
# 1. Search for existing EKF-SLAM node
kb graph query "MATCH (n:Algorithm) WHERE n.id = 'algo_ekf_slam' RETURN n"

# 2. Node exists, it has sources: ["raw-0001"]
# You want to add raw-0002 as a new source for this algorithm.

# 3. Upsert with the updated source list
kb graph upsert-node Algorithm --props '{
  "id": "algo_ekf_slam",
  "name": "EKF SLAM",
  "summary": "Classic Extended Kalman Filter based SLAM algorithm.",
  "origin": "raw",
  "sources": ["raw-0001", "raw-0002"],
  "confidence": 0.95
}'

# 4. The 'sources' array now correctly reflects that two documents mention this entity.
```
