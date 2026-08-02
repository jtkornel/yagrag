---
name: deep-knowledge-extraction
description: Extract structured domain entities, relations, and claims from a document's text. TRIGGER after a new raw document has been ingested and its ID is known.
---

## When to use

Use this skill immediately after `ingest-document`. This is where you transform unstructured text into the "Wikidata" style graph. You should look for specific technical details—equations, variables, and performance claims—rather than just summarizing what the document is about.

Trigger this skill when:
*   A new document ID has just been generated.
*   The user asks to "extract knowledge" or "analyze the graph content" of a document.
*   You need to populate the domain layer with specific entities (e.g., "Find all the sensors mentioned in this paper").

## Steps

1.  **Read Text**: Run `kb doc text <id>` to retrieve the full content of the document.
2.  **Identify Entities**: Scan the text for nodes matching the seed schema. Look for:
    *   **Mathematical**: `Equation` (capture LaTeX and, where possible, a SymPy canonical form), `Variable` (identify domain and dimension), `Quantity` (capture symbol and unit).
    *   **Models**: `MotionModel` (kinematics), `SensorModel` (observation), `NoiseModel` (parameters), `FactorGraph`, `Factor`.
    *   **Architecture**: `StateEstimator` (e.g., EKF, iSAM2), `Solver` (e.g., Levenberg-Marquardt), `Robot`, `Sensor`.
    *   **Academic**: `Method`, `Algorithm` (capture a Python reference implementation if available), `Dataset` (e.g., KITTI, Euroc), `Metric` (e.g., ATE, RPE), `Assumption`.
3.  **Upsert Nodes**: For each entity, run `kb graph upsert-node <Label> --props '...'`. 
    *   **Crucial**: Every node MUST include `origin: "raw"` and `sources: ["<doc_id>"]`.
    *   **Code Representation**: For nodes supporting it (e.g., `Equation`, `Algorithm`), write the checkable snippet to the `code/` directory (using document/source namespacing for paper-specific snippets, e.g., `code/equations/<doc_id>/<slug>.sympy` and `code/algorithms/<doc_id>/<slug>.py`) and run `kb code check` after upserting. Refer to the `code-representation` skill for the formal specification. Do NOT include paper equation numbers in snippet comments; store paper equation references in the node's `summary` or properties.
    *   Use the `summary` field to capture a brief definition of the entity as used in the document.
4.  **Extract Claims**: Identify specific assertions with a truth value or quantitative result (e.g., "Method X achieves 2.1% drift").
    *   Run `kb graph upsert-claim <claim_id> --subject <Label:id> --predicate <str> --props '{"origin": "raw", "sources": ["<doc_id>"], "confidence": 0.9}'`.
    *   Use `--object-literal` for quantitative results or `--object Label:id` for relationships between entities.
5.  **Establish Relations**: Link the `Document` node to its contents:
    *   `DEFINES`: For new concepts or models introduced by the document.
    *   `MENTIONS`: For existing concepts or related work cited.
    *   `SUPPORTS`: To link the `Document` to the `Claim` nodes it asserts.
    *   Use `kb graph upsert-edge`.
6.  **Cross-Link Domain**: Connect domain entities directly (e.g., `FactorGraph` --`HAS_VARIABLE`--> `Variable`). This builds the "physics" of the graph.

## Rules

*   **No Prose Summaries**: A document summary is a failure. You must extract the underlying structured facts.
*   **Mandatory Provenance**: Every `upsert-node`, `upsert-edge`, and `upsert-claim` MUST include `origin` and `sources` in its properties.
*   **Reified Claims**: Claims are nodes themselves. Don't just make them properties of another node; use the `Claim` node type.
*   **LaTeX for Equations**: Always capture equations in their raw LaTeX format to allow for future mathematical reasoning.
*   **Machine-Checkable Code**: Whenever you can, provide a SymPy form for equations or a Python 3.11 implementation for algorithms. However, missing or uncheckable code NEVER blocks ingestion; an `Equation` with only `latex` is still a valid and useful node. Refer to `code-representation` for how to store and check these snippets.
*   **Unit Awareness**: When extracting `Quantity` nodes, always include the `unit` and `symbol` properties if present.
*   **Source Integrity**: The `sources` array for a node should grow as more documents mention it; never overwrite it.

## Example

Extracting knowledge from a SLAM paper (`raw-0001`).

```bash
# Read the text first
kb doc text raw-0001

# Upsert an Equation found in the text
kb graph upsert-node Equation --props '{
  "id": "eq_imu_preint",
  "name": "IMU Preintegration",
  "latex": "\\Delta R_{ij} = \\prod_{k=i}^{j-1} Exp((\\omega_k - b_g) \\Delta t)",
  "origin": "raw",
  "sources": ["raw-0001"]
}'

# Link document to the equation
kb graph upsert-edge DEFINES --from Document:raw-0001 --to Equation:eq_imu_preint --props '{"origin": "raw", "sources": ["raw-0001"]}'

# Upsert a claim about performance
kb graph upsert-claim claim_drift_01 --subject Method:preint_v2 --predicate "achieves_drift" --object-literal "0.5% per km" --props '{
  "origin": "raw",
  "sources": ["raw-0001"],
  "confidence": 0.95
}'

# Support the claim
kb graph upsert-edge SUPPORTS --from Document:raw-0001 --to Claim:claim_drift_01 --props '{"origin": "raw", "sources": ["raw-0001"]}'
```
