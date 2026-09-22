# TODO

Directly actionable items ready for implementation:

## 1. Multi-Stage Lexical and Semantic Hashing in `kb graph dedupe`
* **Goal**: Enhance `kb graph dedupe`'s deterministic candidate matching to detect duplicate entities under morphological variants, word re-orderings, abbreviations, and semantic paraphrasing.
* **Scope**:
  * **Stage 1 (Lexical / Morphological Hashing)**: Normalize names by stripping stopwords, stemming/lemmatizing terms, and sorting tokens (e.g. `"Extended Kalman Filter"` vs `"Kalman Filter (Extended)"` produce identical hash keys).
  * **Stage 2 (Local Vector Semantic Pre-Clustering)**: For nodes within the same label table sharing vocabulary or high cosine similarity via the local fastembed model, group into candidate clusters without relying on slow $O(N^2)$ pairwise comparisons.
  * **Candidate Review & Interactive Output**: Emit structured candidate duplicate pairs/bundles for agent or user review, and support idempotent execution via `kb graph dedupe --apply`.
* **Reference**: Candidate 2 in [`docs/comparison_yagrag_kggen.md`](docs/comparison_yagrag_kggen.md).

## 2. [x] Improved Document & Diagram Extraction Subsystem (Completed)
* **Goal**: Replace on-the-fly, un-cached document extraction with a direct Docling-based extraction engine, intermediate caching, and visual figure/diagram extraction for agent VLM inspection.
* **Scope**:
  * **Direct Docling Integration (No Fallback / No Custom Wrapper Interface)**:
    * Use Docling directly as the single document extraction engine across the project.
    * No fallback extractor (e.g. `pypdf`) and no custom abstract wrapper/pluggable interface; Docling handles layout analysis, table extraction, LaTeX formula recovery, DocLayNet reading order, and figure/diagram cropping.
  * **Intermediate Cache Subsystem (`documents/cache/<doc_id>/`)**:
    * Store extracted layout-preserving Markdown (`content.md`), structured AST (`document.json`), and figures (`figures/`).
    * Cache invalidation via SHA-256 hash in `meta.json`.
    * Exclude `documents/cache/` from Git by default for copyright compliance.
    * Wire `DocumentStore.extract_text` and `kb index build` to read from cache when valid.
  * **Visual Diagram & Figure Cropping**:
    * Deterministic figure/diagram bounding-box extraction and cropping to `.png` with sidecar metadata (caption, page, bbox) via Docling.
    * Transparent caching on `kb doc text <id>` and visual inspection via `kb doc figures <id>`, with `kb doc clean --cache`.
  * **Agent Skill Integration**:
    * Update `deep-knowledge-extraction` skill to list figures and inspect diagrams (factor graphs, kinematic chains, block diagrams) using multimodal VLM capabilities to populate domain graph nodes.
* **Reference**: Proposal in [`docs/proposal_document_and_diagram_extraction.md`](docs/proposal_document_and_diagram_extraction.md).

## 3. [x] Typed Claims and Deep Cross-Document References (Completed)
* **Goal**: Extend citation modeling from coarse, untyped `(Document)-[:CITES]->(Document)` edges down to deep references linking specific domain entities (`Method`, `Equation`, `Algorithm`, `Model`, `Assumption`, `Dataset`) across documents, with a decoupled technical taxonomy and evaluative attitude.
* **Scope**:
  * **Schema Migrations (`schema/migrations/0006_deep_typed_references.gql`)**:
    * Add typed citation relationship edges with contextual qualifiers (`context`, `section`, `target_ref`, `aspect`):
      * `ADOPTS_FORMULATION`, `EXTENDS_METHOD`, `REVISES_ASSUMPTION`, `EVALUATES_PROPERTY`, `BENCHMARKS_AGAINST`, and `BACKGROUND_CONTEXT`.
    * Update `schema/schema_companion.json` to define allowed `(from, to)` node labels and canonical directionality rules for each reference relation type.
  * **Decoupled Evaluative Claims (Unchanged `Claim` Schema)**:
    * Reify nuanced cross-document claims via existing `Claim` nodes without schema alterations, capturing `reference_type` and `attitude` enums inside the standard `qualifiers` JSON map to preserve `Claim`'s generic applicability across all document facts.
    * Enforce `BACKGROUND_CONTEXT` as the default non-evaluative fallback for general or introductory citations to prevent forced misclassification.
  * **Deterministic CLI & Graph Linting**:
    * Support deep edges in `kb graph upsert-edge` and `kb graph batch`.
    * Extend `kb graph lint` to validate non-empty provenance and verify that any optional `reference_type` or `attitude` in claim `qualifiers` matches valid enum values.
    * Add typed lineage and consensus query support (e.g. `kb graph lineage <entity_id>` or `kb graph consensus <entity_id>`).
  * **Agent Skill Integration (`deep-knowledge-extraction`)**:
    * Update `.agents/skills/deep-knowledge-extraction/SKILL.md` to instruct the agent to resolve cross-document references to fine-grained target entities (or entity stubs), classifying them along the two decoupled axes (functional intent and evaluative attitude).
* **Reference**: Proposal in [`docs/integrating_typed_claims_and_deep_references.md`](docs/integrating_typed_claims_and_deep_references.md).

## 4. [x] Structural AST and JSON Pointer Tree Navigation in CLI (`kb doc tree`) (Completed)
* **Goal**: Open up the cached Docling document AST (`document.json`) to agents via clean CLI commands, allowing selective discovery and extraction of tables, LaTeX equations, and section items via standard JSON Pointers (`cref`) without reading entire documents into the LLM context.
* **Scope**:
  * **Universal Docling Parsing Across All Formats**:
    * Process `.md` and `.txt` through Docling at ingestion/parse time alongside `.pdf` to generate unified `DoclingDocument` ASTs (`document.json`) across all documents (raw and synthesized) without format-specific branching.
  * **Section Outline & JSON Pointer Discovery (`kb doc outline <doc_id>`)**:
    * Hierarchical outline command supporting `--items` and `--json` to expose sections, headings, page provenance, and item locations annotated with their native RFC 6901 JSON pointer (`cref`, e.g. `#/tables/0`, `#/texts/18`).
  * **Isolated Table Extraction (`kb doc tables <doc_id>`)**:
    * Table listing and index-based extraction supporting `--format [md|html|json]` to preserve complex multi-row/column structures without markdown-pipe degradation.
  * **Isolated Equation Extraction (`kb doc equations <doc_id>`)**:
    * Mathematical formula listing and extraction by index or page in clean LaTeX notation for direct population of `Equation.latex` graph nodes.
  * **Direct Item Retrieval (`kb doc item <doc_id> <cref>`)**:
    * Direct lookup of specific AST nodes by JSON pointer target discovered in the outline, outputting formatted content or raw AST dictionaries.
  * **Agent Skill Integration**:
    * Update `.agents/skills/deep-knowledge-extraction/SKILL.md` to recommend `kb doc outline` discovery followed by targeted `cref` point-lookups instead of loading full document markdown into context.
* **Reference**: Proposal in [`docs/proposal_docling_ast_cli_tree.md`](docs/proposal_docling_ast_cli_tree.md).





