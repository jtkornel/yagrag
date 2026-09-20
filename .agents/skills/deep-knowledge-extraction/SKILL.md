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
2.  **Identify Entities**: Scan the text for nodes matching the seed schema. Consult `schema/schema_companion.json` for per-type semantics and examples. Look for:
    *   **Mathematical**: `Equation` (capture LaTeX and, where possible, a SymPy canonical form), `Quantity` (capture symbol, unit, and description; use `Quantity` for all physical parameters, measurements, state variables, as well as intermediate equation parameters, normalization factors, constants, and sub-expression symbols like $C$, $N$, $\theta_k$, $v_x$, $\omega_z$, $f_r$, $B_s$), `QuantityKind` (abstract quantity types like length/time, only when the text treats them generically), `Variable` (strictly reserved for discrete state vector slots in `FactorGraph` nodes).
    *   **Models**: `Model` (mathematical model as a whole), `MotionModel` (kinematics), `SensorModel` (observation), `NoiseModel` (parameters), `FactorGraph`, `Factor`.
    *   **Architecture**: `StateEstimator` (e.g., EKF, iSAM2), `Solver` (e.g., Levenberg-Marquardt), `Robot`, `Sensor`.
    *   **Academic & Conceptual**: `Method`, `Algorithm` (capture a Python reference implementation if available), `Dataset` (e.g., KITTI, Euroc), `Benchmark` (standardized task instances used to compare algorithms), `Metric` (e.g., ATE, RPE), `Assumption` (informal/natural-language assumptions), `Concept`. Assumptions are linked via `ASSUMES`: target an `Equation` when the source states the assumption formally, otherwise an `Assumption` node — capture knowledge at the fidelity it appears in the document.
    *   **Application & Task (MaRDI alignment)**: `ApplicationDomain` (e.g. robotics, precision agriculture), `ApplicationProblem` (the practical problem being solved), `ComputationalTask` (the mathematical task derived from a model: state estimation, parameter identification, trajectory optimization, ...). This is the bridge between models and algorithms — extract it whenever a paper says a model is "used for" X.
    *   **Linguistic & Terminological**: `Acronym` (capture `short_form`, `expansion`, `domain_context`, and `summary`; e.g., `short_form: "SLAM"`, `expansion: "Simultaneous Localization and Mapping"`; ID convention: `acronym:<short_slug>:<expansion_slug>`).
3.  **Upsert Nodes**: For each entity, run `kb graph upsert-node <Label> --props '...'`. 
    *   **Crucial**: Every node MUST include `origin: "raw"` and `sources: ["<doc_id>"]`.
    *   **Code Representation**: For nodes supporting it (e.g., `Equation`, `Algorithm`), write the checkable snippet to the `code/` directory (using document/source namespacing for paper-specific snippets, e.g., `code/equations/<doc_id>/<slug>.sympy` and `code/algorithms/<doc_id>/<slug>.py`) and run `kb code check` after upserting. Refer to the `code-representation` skill for the formal specification. Do NOT include paper equation numbers in snippet comments; store paper equation references in the node's `summary` or properties.
    *   Use the `summary` field to capture a brief definition of the entity as used in the document.
    *   **Fast Batch Operations**: For bulk extracting entities, edges, and claims, create a JSON array payload and run `kb graph batch --file <payload.json>` (or pipe via stdin `cat payload.json | kb graph batch --file -`). See `examples/batch_import_example.json` for a reference batch file. Batch operations schema:
        *   Node: `{"op": "node", "label": "<Label>", "props": {"id": "...", "name": "...", "origin": "raw", "sources": ["<doc_id>"], ...}}`
        *   Edge: `{"op": "edge", "rel": "<RelType>", "from": "<FromLabel:from_id>", "to": "<ToLabel:to_id>", "props": {"origin": "raw", "sources": ["<doc_id>"]}}` *(Note: uses `"rel"`, not `"label"`)*
        *   Claim: `{"op": "claim", "id": "<claim_id>", "subject": "<SubjLabel:subj_id>", "predicate": "<predicate>", "object": "<ObjLabel:obj_id>" (or "object_literal": "<value>"), "props": {"name": "<Short Title>", "summary": "<Full Sentence Assertion>", "origin": "raw", "sources": ["<doc_id>"], "confidence": 0.95}}`
        *   Python scripts can also use `with open_graph("<kb_root>") as g: execute_batch(g, ops)`.
4.  **Extract Claims**: Identify specific assertions with a truth value or quantitative result (e.g., "Method X achieves 2.1% drift").
    *   **Strict Property Split**:
        *   `name`: Concise, short title or label for the claim (max 5–10 words, e.g. `"Slip-track EKF drift bound"`). Do NOT place full sentences in `name`.
        *   `summary`: Complete, natural language assertion statement explaining the context, conditions, and quantitative finding.
    *   Run `kb graph upsert-claim <claim_id> --subject <Label:id> --predicate <str> --props '{"name": "<Short Label>", "summary": "<Full Assertion Sentence>", "origin": "raw", "sources": ["<doc_id>"], "confidence": 0.9}'` (or include `"op": "claim"` in `kb graph batch`).
    *   Use `--object-literal` for quantitative results or `--object Label:id` for relationships between entities.
5.  **Establish Relations**: Link the `Document` node to its contents and other documents:
    *   `DEFINES`: For new concepts, models, or acronym definitions introduced by the document (e.g., `(Document)-[:DEFINES]->(Acronym)`).
    *   `MENTIONS`: For existing concepts, related work cited, or established acronyms used in the text (e.g., `(Document)-[:MENTIONS]->(Acronym)`).
    *   `SUPPORTS`: To link the `Document` to the `Claim` nodes it asserts.
    *   `CITES`: To link the `Document` node to other `Document` nodes when the paper directly references another document present in the knowledge base (e.g., `(citing_doc)-[:CITES]->(cited_doc)`).
6.  **Cross-Link Domain & Acronyms (Zero Floating Nodes)**: Connect domain entities directly. Canonical directions and semantics per edge are in `schema/schema_companion.json` — follow them.
    *   `StateEstimator` --`USES`--> `Algorithm` / `Method` / `MotionModel` / `SensorModel` / `FactorGraph` / `Solver`.
    *   `Any Node` --`USES_ACRONYM`--> `Acronym`: Link any method, estimator, model, algorithm, equation, dataset, or concept whose definition/text uses the acronym.
    *   `Acronym` --`STANDS_FOR`--> `Concept` / `StateEstimator` / `Method`: Link the acronym to the formal domain entity it represents.
    *   `Quantity` --`DEFINED_BY`--> `Equation`: Use `DEFINED_BY` strictly when the `Equation` computes or defines this target output quantity (left-hand side / LHS).
    *   `Equation` --`USES_SYMBOL`--> `Quantity`: Link the `Equation` to all input terms, intermediate symbols, normalization constants, and sub-expression parameters appearing inside its expression.
    *   `FactorGraph` --`HAS_VARIABLE`--> `Variable`, `FactorGraph` --`HAS_FACTOR`--> `Factor`.
    *   `Algorithm` / `Method` / `System` --`EVALUATED_ON`--> `Dataset` / `Metric`.
    *   **MaRDI alignment edges** (see companion for full semantics):
        *   `ApplicationDomain` --`CONTAINS`--> `ApplicationProblem`; `ApplicationProblem` --`MODELLED_BY`--> `Model`.
        *   `Model` --`USED_BY`--> `ComputationalTask`; `Algorithm` --`SOLVES`--> `ComputationalTask`. Prefer this bridge over direct Model–Algorithm edges.
        *   `Benchmark` --`INSTANCE_OF`--> `ComputationalTask`; `Tool` --`TESTS`--> `Benchmark`.
        *   Algorithm hierarchies: `Algorithm` --`SUBCLASS_OF`--> `Algorithm` (B is the more general algorithm), `Algorithm` --`HAS_COMPONENT`--> `Algorithm` (B is a subroutine of A). Both transitive — store direct links only.
        *   Model/Equation transformations: `DISCRETIZED_BY`, `LINEARIZED_BY`, `APPROXIMATED_BY`, `NONDIMENSIONALIZED_BY` (Quantity), `HAS_WEAK_FORMULATION` (Equation).
        *   `Model`/`Equation` --`ASSUMES`--> `Equation` (formally stated assumption) or `Assumption` (informal assumption).
        *   Publication roles: `Algorithm` --`INVENTED_IN`/`ANALYZED_IN`/`STUDIED_IN`/`APPLIED_IN`/`REVIEWED_IN`--> `Document`; `Tool`/`Benchmark` --`DOCUMENTED_IN`/`USED_IN`--> `Document`.
        *   `Quantity` --`HAS_KIND`--> `QuantityKind`.
    *   Every extracted node MUST be connected via at least one relationship edge. Floating nodes with 0 edges are prohibited.
7.  **Deep Cross-Document Reference Extraction**:
    Move beyond coarse `(Document)-[:CITES]->(Document)` citation edges whenever the paper makes specific technical references to prior algorithms, equations, assumptions, or baselines.
    *   **Two-Axis Classification (Strictly Decoupled)**:
        1.  **Functional Reference Type (Intent)**:
            *   `ADOPTS_FORMULATION`: Reuses an exact formal definition, governing equation, coordinate frame, or representation without alteration. (Domain: `Equation`, `Quantity`, `Model`, `CoordinateFrame`, `Method`, `Algorithm`, `Document`; Range: `Equation`, `Quantity`, `Model`, `CoordinateFrame`).
            *   `EXTENDS_METHOD`: Builds directly upon an existing algorithm, architecture, pipeline, or proof technique by adding components or expanding state. (Domain: `Method`, `Algorithm`, `System`, `Model`, `Document`; Range: `Method`, `Algorithm`, `System`, `Model`).
            *   `REVISES_ASSUMPTION`: Modifies, relaxes, or substitutes a theoretical or physical assumption or constraint made in earlier work. (Domain: `Method`, `Algorithm`, `Model`, `Equation`, `Assumption`, `Document`; Range: `Assumption`, `Model`, `Constraint`, `Equation`).
            *   `EVALUATES_PROPERTY`: Technical analysis, characterization, or investigation of a property (convergence rate, numerical stability, computational complexity, memory scaling, sensitivity). (Domain: `Method`, `Algorithm`, `Model`, `Document`; Range: `Algorithm`, `Equation`, `Model`, `Method`, `Tool`).
            *   `BENCHMARKS_AGAINST`: Quantitatively or experimentally compares performance against cited work using standardized metrics, tasks, or datasets. (Domain: `Algorithm`, `Method`, `System`, `StateEstimator`, `Document`; Range: `Algorithm`, `Method`, `Dataset`, `Metric`, `Benchmark`, `StateEstimator`).
            *   `BACKGROUND_CONTEXT`: General attribution, introductory survey, historical positioning, or broad thematic context. **Mandatory Default Fallback**. (Domain: `Document`, `Method`, `Algorithm`, `Concept`; Range: `Document`, `Concept`, `Venue`).
        2.  **Attitude (Evaluative Valence)**:
            *   `Positive`: Commends formulation, demonstrates superior robustness/scaling, or praises modularity.
            *   `Negative`: Identifies bottleneck, asymptotic instability, invalid assumption, or edge-case failure.
            *   `Neutral`: Standard objective measurement, routine adoption, or introductory citation.
    *   **Mandatory Default Fallback Rule**:
        Over 50% of citations in technical literature are neutral contextual mentions. If a citation is merely contextual or introductory without explicit adoption, algorithmic extension, assumption alteration, quantitative benchmarking, or property evaluation, you MUST classify it as `BACKGROUND_CONTEXT` with `Neutral` attitude (or a direct `BACKGROUND_CONTEXT` edge). Do NOT force evaluative misclassifications.
    *   **Representation Choice**:
        *   **Direct Typed Edge**: When connecting entities or documents directly with contextual properties:
            ```bash
            kb graph upsert-edge EVALUATES_PROPERTY --from Method:meth_ptv2 --to Method:meth_ptv1 \
              --props '{"aspect": "memory_scaling", "section": "IV-B", "origin": "raw", "sources": ["doc_ptv2"]}'
            ```
        *   **Reified Citation-Augmenting Claim**: When capturing a nuanced, evaluative critique or performance comparison with a full assertion summary:
            ```bash
            kb graph upsert-claim claim_solver_scaling_bottleneck \
              --subject Algorithm:alg_sparse_solver \
              --predicate "evaluates_scaling_limit" \
              --object Algorithm:alg_dense_cholesky \
              --props '{
                "name": "Dense solver cubic scaling limit",
                "summary": "Full dense Cholesky factorization incurs O(N^3) scaling as trajectory grows, making batch re-linearization intractable in real-time.",
                "qualifiers": "{\"reference_type\": \"EVALUATES_PROPERTY\", \"attitude\": \"Negative\", \"target_anchor\": \"Section IV-A, Eq. (8)\"}",
                "origin": "raw",
                "sources": ["doc_0002"],
                "confidence": 0.90
              }'
            ```
8.  **Record Citations via `kb doc cite`**: Scan the document's reference section / bibliography for all cited papers and record them using the single mechanical command `kb doc cite`:
    ```bash
    kb doc cite <citing_doc_id> --title "<Full Cited Paper Title>" --year <Year> --url "<DOI or URL>" --ref "<Full Reference String from Bibliography>"
    ```
    *   **Automated Matching & Stub Handling**: The CLI mechanically checks if the cited paper already exists in the graph (as an ingested raw document or existing stub).
        *   If it already exists: the CLI automatically links the `CITES` edge and accumulates provenance without creating duplicates.
        *   If it is a new external paper: the CLI automatically creates a placeholder `Document` stub (`kind: "stub"`) and the `CITES` edge atomically.
9.  **Run Graph Quality Linting**: After completing extraction, execute `kb graph lint` to verify that 0 floating nodes, missing provenance, or claim formatting errors were introduced.

## Rules

*   **No Prose Summaries**: A document summary is a failure. You must extract the underlying structured facts.
*   **Mandatory Provenance**: Every `upsert-node`, `upsert-edge`, and `upsert-claim` MUST include `origin` and `sources` in its properties.
*   **Two-Axis Reference Decoupling**: Never conflate technical reference type (`ADOPTS_FORMULATION`, `EXTENDS_METHOD`, etc.) with evaluative valence (`Positive`, `Negative`, `Neutral`). Use `qualifiers` on `Claim` to record `reference_type` and `attitude`.
*   **Background Context Fallback**: Avoid hallucinated intent. Always use `BACKGROUND_CONTEXT` for broad, neutral introductory references.
*   **Reified Claims**: Claims are nodes themselves. Don't just make them properties of another node; use the `Claim` node type with short `name` labels and full sentence `summary` assertions.
*   **Reified Claims**: Claims are nodes themselves. Don't just make them properties of another node; use the `Claim` node type with short `name` labels and full sentence `summary` assertions.
*   **Relationship Schema Compatibility**: Always check `kb schema show` and `schema/schema_companion.json` to verify allowed `(from, to)` node labels for each relationship type (e.g. `DEFINED_BY` allows `MotionModel`, `Quantity`, `Algorithm`, etc. to `Equation`, but not generic `Model`; `USES` allows `StateEstimator -> Method` or `System -> Sensor`, but not `Method -> Sensor`).
*   **Edge Direction & Qualifiers**: Store each edge only in its canonical direction (see companion `storage_convention`). Never store inverse edges (e.g. no `MODELS` — only `MODELLED_BY`). For symmetric edges (`SIMILAR_TO`, `RELATES_TO`, `CONTRADICTS`), store once in either direction. For transitive edges (`SPECIALIZES`, `SUBCLASS_OF`, `HAS_COMPONENT`, `PART_OF`, `DERIVED_FROM`), store only direct links, never inferred closures.
*   **Symbol Consistency for SymPy**: When extracting `Quantity` nodes whose symbols appear in `.sympy` expressions checked with `kb code check`, ensure the `symbol`, `name`, or `id` matches the exact SymPy variable identifier (e.g. `"chi"`, `"omega_z"`, `"theta"`), avoiding unknown symbol warnings.
*   **Symbol Sanitization**: For `Variable` and `Quantity` nodes, `symbol` must contain ONLY the clean symbol string for that specific entity (e.g. `"B_s"`, `"f_r"`, `"chi"`), never concatenated or combined multi-variable text.
*   **Intermediate Symbol Extraction / Symbol Interconnectivity**: Extract not only primary physical variables and measurements, but also intermediate equation parameters, normalization factors, constants, and sub-expression symbols (e.g. $C$, $N$, $\theta_k$) as explicit `Quantity` nodes. Wire each parameter to its parent `Equation` via `USES_SYMBOL` edges. This maximizes graph interconnectivity, prevents unlinked mathematical terms, and ensures SymPy static checks (`kb code check`) verify all free symbols without unknown symbol warnings.
*   **Zero Floating Nodes**: Every extracted `Equation`, `Algorithm`, `Variable`, `Quantity`, or `Dataset` must be linked to its parent model, estimator, or paper via domain relationships.
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

# Upsert Quantities (LHS output, intermediate variables, and parameters)
kb graph upsert-node Quantity --props '{
  "id": "qty_delta_R_ij",
  "name": "Preintegrated relative rotation",
  "symbol": "\\Delta R_{ij}",
  "unit": "-",
  "origin": "raw",
  "sources": ["raw-0001"]
}'

kb graph upsert-node Quantity --props '{
  "id": "qty_omega_k",
  "name": "Angular velocity measurement at step k",
  "symbol": "\\omega_k",
  "unit": "rad/s",
  "origin": "raw",
  "sources": ["raw-0001"]
}'

kb graph upsert-node Quantity --props '{
  "id": "qty_b_g",
  "name": "Gyroscope bias",
  "symbol": "b_g",
  "unit": "rad/s",
  "origin": "raw",
  "sources": ["raw-0001"]
}'

kb graph upsert-node Quantity --props '{
  "id": "qty_delta_t",
  "name": "IMU sampling interval",
  "symbol": "\\Delta t",
  "unit": "s",
  "origin": "raw",
  "sources": ["raw-0001"]
}'

# Link document to the equation
kb graph upsert-edge DEFINES --from Document:raw-0001 --to Equation:eq_imu_preint --props '{"origin": "raw", "sources": ["raw-0001"]}'

# Link LHS defined quantity: (Quantity)-[:DEFINED_BY]->(Equation)
kb graph upsert-edge DEFINED_BY --from Quantity:qty_delta_R_ij --to Equation:eq_imu_preint --props '{"origin": "raw", "sources": ["raw-0001"]}'

# Link input terms and intermediate parameters: (Equation)-[:USES_SYMBOL]->(Quantity)
kb graph upsert-edge USES_SYMBOL --from Equation:eq_imu_preint --to Quantity:qty_omega_k --props '{"origin": "raw", "sources": ["raw-0001"]}'
kb graph upsert-edge USES_SYMBOL --from Equation:eq_imu_preint --to Quantity:qty_b_g --props '{"origin": "raw", "sources": ["raw-0001"]}'
kb graph upsert-edge USES_SYMBOL --from Equation:eq_imu_preint --to Quantity:qty_delta_t --props '{"origin": "raw", "sources": ["raw-0001"]}'

# Upsert a claim about performance
kb graph upsert-claim claim_drift_01 --subject Method:preint_v2 --predicate "achieves_drift" --object-literal "0.5% per km" --props '{
  "origin": "raw",
  "sources": ["raw-0001"],
  "confidence": 0.95
}'

# Support the claim
kb graph upsert-edge SUPPORTS --from Document:raw-0001 --to Claim:claim_drift_01 --props '{"origin": "raw", "sources": ["raw-0001"]}'

# Deep Cross-Document Typed Reference (evaluates property of cited algorithm)
kb graph upsert-edge EVALUATES_PROPERTY --from Method:preint_v2 --to Algorithm:alg_euler_int --props '{
  "aspect": "numerical_drift",
  "section": "III-B",
  "origin": "raw",
  "sources": ["raw-0001"]
}'

# Reified Citation-Augmenting Claim (with reference_type, attitude, target_anchor in qualifiers)
kb graph upsert-claim claim_euler_drift --subject Method:preint_v2 --predicate "evaluates_drift_limit" --object Algorithm:alg_euler_int --props '{
  "name": "Euler integration drift accumulation",
  "summary": "Euler integration accumulates unbounded drift under high angular accelerations due to first-order truncation error.",
  "qualifiers": "{\"reference_type\": \"EVALUATES_PROPERTY\", \"attitude\": \"Negative\", \"target_anchor\": \"Section III-B\"}",
  "origin": "raw",
  "sources": ["raw-0001"],
  "confidence": 0.95
}'

# Link document to another document it cites (coarse bibliographical citation)
kb graph upsert-edge CITES --from Document:raw-0001 --to Document:raw-0004 --props '{"origin": "raw", "sources": ["raw-0001"], "confidence": 1.0}'
```
