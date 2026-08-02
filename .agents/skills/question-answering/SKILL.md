---
name: question-answering
description: Answer user questions using evidence strictly retrieved from the knowledge base. TRIGGER when the user asks a question about the domain or specific documents.
---

## When to use

Use this skill whenever the user asks for information. Your goal is to provide a grounded answer that cites specific document IDs. Do not answer from your pre-trained weights if the information is missing from the graph; instead, report the gap in the knowledge base.

Trigger this skill when:
*   The user asks a question ending in a question mark.
*   The user asks for a "fact", "definition", or "result".
*   The user asks about relationships between domain entities (e.g., "Which solver estimates this variable?").
*   You need to verify a claim made in a recent conversation.

## Steps

1.  **Search the Index**: Run `kb search '<query>' --json` to find relevant raw documents and specific graph nodes. This provides the first layer of context.
2.  **Query the Graph**: Based on search results, run `kb graph query '<cypher>'` to find relationships, claims, and technical details. Use Cypher to traverse the domain layer (e.g., `StateEstimator` → `MotionModel` → `Equation`).
3.  **Inspect Claims**: Look specifically for `Claim` nodes and their associated `SUPPORTS` or `CONTRADICTS` edges. Surface the specific document IDs attached to these edges.
4.  **Synthesize Answer**:
    *   State the answer clearly in the second person ("you").
    *   Cite document IDs for every fact mentioned (e.g., "According to [raw-0002]...").
    *   If there are conflicting claims, present both and their respective sources rather than picking one.
5.  **Assess Confidence**: State your confidence in the answer. Base this on:
    *   The `confidence` property of the retrieved nodes/claims.
    *   The number of distinct sources (`doc_ids`) that mention the fact.
    *   The presence (or absence) of conflicting evidence.
6.  **Identify Gaps**: If the knowledge base does not contain the answer, explicitly state: "I cannot find evidence for this in the current knowledge base." Do not guess.

## Rules

*   **No Internal Knowledge**: Only answer based on the knowledge base. Do not supplement with outside information unless it is to clarify a term already defined in the graph.
*   **Mandatory Citations**: Every answer must point to at least one raw document ID as its ultimate source of truth.
*   **Surface Conflicts**: Never hide a contradiction. If one paper says "A" and another says "B", your answer must mention both and their respective citations.
*   **Traceable Reasoning**: If you inferred a fact by connecting two different parts of the graph, state that the origin is "inferred" and explain your logic.
*   **Deterministic CLI**: Remember that the `kb` CLI provides the facts, but you provide the reasoning and the natural language answer.

## Example

Answering a question about solver performance.

```bash
# 1. Search for solvers and their metrics
kb search "GTSAM solver accuracy on KITTI dataset" --json

# 2. Query for specific claims
kb graph query "MATCH (s:Solver {name: 'GTSAM'})-[:SUPPORTS]-(c:Claim) RETURN c.predicate, c.object_literal, c.sources"

# 3. Analyze results:
# Found Claim: achieves_drift = 0.8% [raw-0001]
# Found Claim: achieves_drift = 1.2% [raw-0002]

# 4. Final Answer:
# Junie: "The GTSAM solver shows varying performance on the KITTI dataset. 
# Document [raw-0001] claims a drift of 0.8%, while [raw-0002] reports a 
# higher drift of 1.2%. My confidence is medium due to this discrepancy."
```
