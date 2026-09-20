This document holds ideas that have either not been explored in detail or need further refinement/deliberation before commitment to implementation.

---

## 1. Community Summaries (Global Graph Summarization)
* Explore hierarchical community detection (e.g. Leiden / hierarchical clustering) and macro community summaries over entity subgraphs, inspired by Microsoft GraphRAG (https://arxiv.org/pdf/2404.16130).

---

## 2. Refined Two-Stage Constrained Extraction Workflow with Schema Evolution
* **Concept**: Constrain entity and relation extraction in the agent workflow to prevent hallucinated relationship types and floating nodes, while making schema expansion explicit.
* **Why it needs more work**:
  * The existing `deep-knowledge-extraction` skill already partially structures extraction into entity scanning, upserting, claim extraction, and relation linking.
  * Unlike `kg-gen` (which operates on an unconstrained/open schema), `yagrag` enforces a strict property graph schema (`schema/migrations/` and `schema_companion.json`).
  * The workflow must clearly distinguish when an extraction requires **schema extension** (`schema-evolution` skill) versus when entities fit existing node/rel types.
  * Need to clearly define where we currently are vs. where we want to go with constrained generation (e.g., dynamic Pydantic models vs. schema companion guidance).
* **Reference**: Candidate 3 in [`docs/comparison_yagrag_kggen.md`](docs/comparison_yagrag_kggen.md).

---

## 3. FastMCP Server Adapter for Terminal-Free / Autonomous Agent Workflows
* **Concept**: Provide an MCP (Model Context Protocol) server interface (`kb mcp serve` or FastMCP adapter) exposing `kb` capabilities as first-class tools.
* **Why it needs more work**:
  * Currently, the agent relies entirely on executing shell commands in a terminal (`kb ...`). In long-running autonomous or sandboxed agent environments, raw terminal access poses safety and security risks or may not be available.
  * Need to evaluate an MCP server design that allows agents to perform document ingestion, querying, and graph updates safely without requiring full bash/terminal privileges, while preserving CLI determinism.
* **Reference**: Candidate 6 in [`docs/comparison_yagrag_kggen.md`](docs/comparison_yagrag_kggen.md).

---

## 4. Ingestion of Source Code Backed by Public Repositories
* **Concept**: Extend ingestion beyond papers/PDFs/Markdown to ingest source code repositories (e.g. cloning or referencing public Git repositories associated with papers).
* **Details**: Link formal `Algorithm`, `Method`, and `Tool` nodes directly to implementation files, function definitions, or Git commits/tags, enriching `code/` representation and verifiable mathematical pipelines.

---

## 5. Knowledge Base Aggregation & Merge Tool (`kb graph merge`)
* **Concept**: Provide a deterministic command to merge two independent `yagrag` knowledge base directories (e.g., merging domain-specific KBs created by different researchers).
* **Why it needs more work**:
  * Requires conflict resolution strategies for overlapping document IDs, differing schema migration histories, and shared acronyms/entities.
  * Needs investigation into unioning manifests, graph databases (`graph.tvdb`), and snippet directories (`code/`).
* **Reference**: Candidate 5 in [`docs/comparison_yagrag_kggen.md`](docs/comparison_yagrag_kggen.md).

---

## 6. Extraction Quality Benchmark Harness
* **Concept**: Develop an automated evaluation benchmark to measure precision, recall, and factual fidelity of extracted entities, relations, claims, and equations.
* **Why it needs more work**:
  * `kg-gen` uses the MINE benchmark (Measure of Information in Nodes and Edges), but MINE is targeted at open-domain conversational/text triples.
  * For `yagrag`, extraction targets formal structures: equations (with SymPy grounding), parameters, algorithms, and reified claims with provenance. MINE is unlikely to be the right dataset or evaluation methodology.
  * Need to design a domain-appropriate benchmark dataset and scoring framework (e.g. comparing against curated gold-standard technical papers).
* **Reference**: Candidate 7 in [`docs/comparison_yagrag_kggen.md`](docs/comparison_yagrag_kggen.md).
