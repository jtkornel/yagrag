---
name: document-synthesis
description: Create new summary or overview documents based on existing knowledge in the graph. TRIGGER when the user asks for a summary, a comparison, or a report across multiple sources.
---

## When to use

Use this skill when you need to combine information from multiple raw documents into a single, coherent narrative. For example, if a user asks for a "Comparison of wheel odometry models across all ingested papers," you should synthesize a new document rather than just listing search results.

Trigger this skill when:
*   The user asks for a summary of a specific topic.
*   The user asks for a comparison between two or more entities.
*   The user asks for a "state of the knowledge base" report.
*   You need to create a high-level overview that bridges multiple domain areas.

## Steps

1.  **Gather Facts**: Use `kb graph query` and `kb search` to collect all nodes, edges, and claims relevant to the synthesis topic. Do not rely on your internal training data.
2.  **Draft Markdown**: Write a structured Markdown document. 
    *   Use clear headings to organize the synthesis.
    *   Use tables for quantitative comparisons (e.g., comparing drift across `Method` nodes).
    *   Include citations (e.g., `[raw-0001]`) for every factual assertion.
    *   Include a "References" section at the bottom listing the source document IDs.
3.  **Add to Store**: Save the text to a temporary file and run `kb doc add <file> --kind synthesized`. 
    *   **Crucial**: Repeat the `--source <id>` flag for every raw document that contributed to the synthesis.
4.  **Capture ID**: Save the new document ID returned by the CLI (e.g., `synth-0001`).
5.  **Create Graph Node**: Every synthesized document needs a `Document` node in the graph. Run `kb graph upsert-node Document --props '...'`. 
    *   Set `origin: "synthesized"`.
    *   Include all contributing raw IDs in the `sources` array.
6.  **Link Provenance**: For each contributing raw document ID, run `kb graph upsert-edge DERIVED_FROM --from Document:synth-0001 --to Document:<source_id> --props '{"origin": "synthesized", "sources": ["synth-0001"]}'`. This explicitly builds the derivation tree.

## Rules

*   **No Hallucinations**: You are a grounded agent. Do not include any technical claims that are not backed by a fact in the graph or text in a raw document. If the KB has no data on a requested topic, state that clearly.
*   **Synthesized Tree**: Always use `--kind synthesized` in `kb doc add`. Never place agent-generated content in the raw document tree.
*   **Provenance Chain**: Every fact in a synthesized document must be traceable back to its raw source ID.
*   **DERIVED_FROM Edges**: You must explicitly link the synthesized document to its raw ancestors in the graph to maintain the integrity of the knowledge graph.
*   **Cite Sources**: Use the raw document IDs (e.g., `raw-0001`) as citations within the text.
*   **Immutable Raw Docs**: Never attempt to "edit" a raw document to add a summary. Always create a new synthesized document.

## Example

Synthesizing a comparison of two sensors.

```bash
# 1. Query for sensors and their modalities
kb graph query "MATCH (s:Sensor) RETURN s.id, s.name, s.modality, s.sources"

# 2. You write 'sensor_comparison.md' based on raw-0001 and raw-0002.

# 3. Add the synthesized document
kb doc add sensor_comparison.md --kind synthesized --source raw-0001 --source raw-0002 --title "Comparison: Ouster vs Velodyne"

# Result: Successfully added document synth-0001

# 4. Create the graph node
kb graph upsert-node Document --props '{
  "id": "synth-0001",
  "name": "Comparison: Ouster vs Velodyne",
  "origin": "synthesized",
  "sources": ["raw-0001", "raw-0002"],
  "format": "md"
}'

# 5. Link to sources
kb graph upsert-edge DERIVED_FROM --from Document:synth-0001 --to Document:raw-0001 --props '{"origin": "synthesized", "sources": ["synth-0001"]}'
kb graph upsert-edge DERIVED_FROM --from Document:synth-0001 --to Document:raw-0002 --props '{"origin": "synthesized", "sources": ["synth-0001"]}'
```
