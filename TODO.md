# TODO

Directly actionable items ready for implementation:

## 1. Multi-Stage Lexical and Semantic Hashing in `kb graph dedupe`
* **Goal**: Enhance `kb graph dedupe`'s deterministic candidate matching to detect duplicate entities under morphological variants, word re-orderings, abbreviations, and semantic paraphrasing.
* **Scope**:
  * **Stage 1 (Lexical / Morphological Hashing)**: Normalize names by stripping stopwords, stemming/lemmatizing terms, and sorting tokens (e.g. `"Extended Kalman Filter"` vs `"Kalman Filter (Extended)"` produce identical hash keys).
  * **Stage 2 (Local Vector Semantic Pre-Clustering)**: For nodes within the same label table sharing vocabulary or high cosine similarity via the local fastembed model, group into candidate clusters without relying on slow $O(N^2)$ pairwise comparisons.
  * **Candidate Review & Interactive Output**: Emit structured candidate duplicate pairs/bundles for agent or user review, and support idempotent execution via `kb graph dedupe --apply`.
* **Reference**: Candidate 2 in [`docs/comparison_yagrag_kggen.md`](docs/comparison_yagrag_kggen.md).

## 2. Improved Document & Diagram Extraction Subsystem
* **Goal**: Replace on-the-fly, un-cached `pypdf` extraction with a tiered extractor architecture, intermediate caching, and visual figure/diagram extraction for agent VLM inspection.
* **Scope**:
  * **Intermediate Cache Subsystem (`documents/cache/<doc_id>/`)**:
    * Store extracted layout-preserving Markdown (`content.md`), structured AST (`document.json`), and figures (`figures/`).
    * Cache invalidation via SHA-256 hash in `meta.json`.
    * Exclude `documents/cache/` from Git by default for copyright compliance.
    * Wire `DocumentStore.extract_text` and `kb index build` to read from cache when valid.
  * **Pluggable Extractor Interface**:
    * Abstract base class `DocumentExtractor` with `extract(path, extract_figures=True)`.
    * **Tier 1 (Fallback)**: Lightweight `PyPdfExtractor` retained for minimal environments and fast CI.
    * **Tier 2 (High-Fidelity)**: `DoclingExtractor` packaged as optional dependency (`pip install .[docling]`), providing table extraction, LaTeX formula recovery, and DocLayNet reading order.
  * **Visual Diagram & Figure Cropping**:
    * Deterministic figure/diagram bounding-box extraction and cropping to `.png` with sidecar metadata (caption, page, bbox).
    * CLI commands: `kb doc parse <id>` and `kb doc figures <id>`.
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





