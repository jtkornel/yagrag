# yagrag - Yet Another GraphRAG knowledge base

A **lightweight, local-first GraphRAG system** for building and maintaining
*domain-specific property-graph databases and document collections*.

The system is split in two:

- **A deterministic CLI (`kb`)** — implemented in Python, performs all
  document-store and graph-database operations. **No LLM calls inside the CLI.**
- **An agent layer** — a set of agent skills (in `.agents/skills/`) that tell
  an LLM agent *how* to interview the user, model the domain, ingest and analyse
  documents, evolve the schema, update the graph, synthesize documents, and
  answer questions — always by invoking the deterministic CLI.

The knowledge-graph philosophy is **Wikidata, not Wikipedia**: the graph is
designed to capture structured domain knowledge that lives *inside* documents
(variables, factors, methods, equations, quantities, claims and their
relations), not just bibliographic links between documents. See the plan in
`.junie/plans/graphrag-knowledge-base-bootstrap.md` for the full design.

### Status

Current feature set:

- **Deterministic CLI (`kb`)**: scriptable, offline, provenance-enforced operations with **no LLM calls**.
- **Document store & citation engine**: raw immutable ingestion, PDF/MD text extraction, and automated citation tracking (`kb doc cite`, `kb doc clean`, `kb doc stubs`).
- **Embedded property graph (TrueSpar Traverse) + ISO GQL schema**: native property graph with tracked `.gql` migrations, structured domain types, and reified claims.
- **Cross-cutting terminological layer**: domain-agnostic `Acronym` support with polysemy disambiguation, `USES_ACRONYM` references, and `STANDS_FOR` concept links.
- **Statically checkable mathematics & algorithms**: SymPy-verified expressions (`.sympy`), Python reference implementations (`.py`), and mathematical dependency inspection (`kb math show`, `kb math glossary`).
- **Graph quality audit & maintenance**: non-destructive entity deduplication (`kb graph dedupe`) and structural graph linting (`kb graph lint`).
- **Hybrid retrieval**: vector search + full-text search + graph context bundles via `kb search`.
- **Agent skills**: 10 modular skills in `.agents/skills/` adhering to the open Agent Skills standard.

### Quickstart (for developers)

Install in a dev virtualenv (Python 3.11+):

```bash
./scripts/bootstrap.sh
```

`./scripts/bootstrap.sh` sets up `.venv` with all optional extras. If you suspect the
current venv is broken (e.g. stale interpreter), re-create it with `--recreate`.

Then scaffold a new knowledge base directory:

```bash
kb init ./my-kb
```

This creates a self-contained knowledge base directory:

```
my-kb/
  kb.toml                 # KB config (db path, document paths, embedder)
  documents/
    raw/                  # immutable ingested sources
    synthesized/          # agent-generated documents
    manifest.json         # document index
  schema/
    migrations/           # versioned schema migrations (copy in the seed schema here)
  code/                   # statically checkable snippets referenced by `code_path`
  graph.tvdb              # embedded Traverse database file (created on first `kb schema apply`)
```

### The worked example

Run:

```bash
./examples/factor-graph-slam/build_example.sh /tmp/demo-kb
```

This script builds a complete knowledge base from a short fictional factor-graph SLAM “paper”
by ingesting raw text, extracting structured entities + reified claims, building indexes, and running search.

`tests/test_e2e_smoke.py` runs the same script end-to-end, so the worked example cannot silently rot.
The script produces roughly **~25 domain entities**, **~30 relations**, and **3 reified claims**, plus one
SymPy equation and one Python algorithm that it checks with `kb code check`.

### Deterministic CLI vs agent reasoning

The central architectural boundary is *deterministic CLI, LLM reasoning in the agent*:

- The `kb` CLI contains **no LLM calls of any kind**. It only performs deterministic operations for the
  document store, graph writes/queries, index build, and hybrid retrieval.
- The CLI is fully scriptable and testable offline.
- All reasoning lives in the agent: interviewing the user, deciding what entities exist, extraction,
  schema proposals, synthesis, and answering questions.
- The agent is guided by the markdown skills in `.agents/skills/` and acts only by invoking the CLI.

Benefits: the tool is harness-agnostic, deterministic, unit-testable, and the “intelligence” is swappable.

### Agent skills

The skills live in **`.agents/skills/`**, one folder per skill holding a `SKILL.md` in the open
[Agent Skills](https://agentskills.io) format:

```
.agents/skills/
  domain-modeling/SKILL.md
  ingest-document/SKILL.md
  ...
```

That location is deliberately vendor-neutral rather than tied to one harness (`.junie/skills/`,
`.claude/skills/`, `.cursor/skills/`, …), so any agent supporting the format discovers them with no
per-tool configuration. If your agent only scans its own directory, symlink or copy the folder there.

| Skill | Use it when |
|---|---|
| `domain-modeling` | Interview the user to define node and relation types for a new domain and verify against the existing schema. |
| `ingest-document` | Add a new raw document to the knowledge base and initialize its record. |
| `save-html-as-digestible` | Convert a web page or HTML content into clean Markdown before ingesting it into the KB. |
| `deep-knowledge-extraction` | Extract structured domain entities, relations, and claims from a document's text. |
| `schema-evolution` | Extend the knowledge base schema by adding new node or relation types via migrations. |
| `graph-update` | Reconcile and update the knowledge graph with new facts while maintaining consistency. |
| `document-synthesis` | Create new summary or overview documents based on existing knowledge in the graph. |
| `question-answering` | Answer user questions using evidence strictly retrieved from the knowledge base. |
| `code-representation` | Write and check statically checkable code for equations, algorithms, and models. |
| `knowledge-base-maintenance` | Audit, clean, and maintain knowledge base quality across documents, citations, domain entities, and symbols. |

### The layered schema philosophy

**Wikidata, not Wikipedia**: the goal is a graph of structured facts *inside* documents — not a fuzzy
“what this paper is about” summary.

The schema in `schema/migrations/` defines node and relation types organized in **four conceptual layers**.
The upper layers are generic and reusable across all scientific and technical domains; the domain layer is
customizable to the specific field:

- **Layer 1: Document & Bibliographic Layer** (general, reusable across research fields)
  - Node types: `Document`, `Author`, `Venue`
  - Relation types: `CITES`, `AUTHORED_BY`, `PUBLISHED_IN`, `DERIVED_FROM`, `MENTIONS`, `DEFINES`, `SUPPORTS`, `CONTRADICTS`
  - Features: Automatic citation stubs, DOI/arXiv canonicalization, and citing provenance aggregation.

- **Layer 1.5: Cross-Cutting Terminological & Acronym Layer** (domain-agnostic linguistic layer)
  - Node types: `Acronym` (captures `short_form`, `expansion`, `domain_context`, `summary`)
  - Relation types:
    - `USES_ACRONYM`: Links any domain node (Method, StateEstimator, Algorithm, Dataset, Equation) to an acronym used in its title or descriptive text.
    - `STANDS_FOR`: Connects an acronym directly to the formal domain concept or entity it represents.
    - `DEFINES` / `MENTIONS`: Records whether a paper introduces or merely uses an abbreviation.
  - Identity & Disambiguation: Uses deterministic compound keys (`acronym:<short_slug>:<expansion_slug>`), allowing multiple distinct meanings of the same short form (e.g. SLAM or PCA) to coexist without collision.

- **Layer 2: Reified Claim Layer** (general, reusable across research fields)
  - Node types: `Claim` — a claim is its own node carrying subject–predicate–object semantic triples, qualifiers, confidence, and source provenance.
  - Property discipline:
    - `name`: Short label or title (max 5–10 words, e.g. `"Slip-track EKF drift bound"`).
    - `summary`: Full, self-contained natural language assertion sentence capturing context, conditions, and quantitative findings.
  - Relation types: `ABOUT`/`HAS_OBJECT` link a claim to its subject and object (nodes from any layer); documents attach via `SUPPORTS`/`CONTRADICTS`.
  - Conflicting assertions from different papers coexist with full provenance rather than being flattened.

- **Layer 3: Deep Domain Knowledge** (e.g., sensor fusion, factor graphs, robotics navigation)
  - Node types: `FactorGraph`, `Variable`, `Factor`, `StateEstimator`, `MotionModel`, `SensorModel`, `NoiseModel`, `Sensor`, `Solver`, `Equation`, `Quantity`, `Assumption`, `CoordinateFrame`, `Robot`, `Task`, `Dataset`, `Metric`, `Tool`
  - Relation types: `HAS_VARIABLE`, `HAS_FACTOR`, `CONNECTS`, `ESTIMATES`, `MEASURES`, `ASSUMES`, `SOLVED_BY`, `DEFINED_BY`, `EXPRESSED_BY`, `USES_SYMBOL`, `EVALUATED_ON`, `EXPRESSED_IN`, `HAS_NOISE`

Key point: a mere document summary is a failed extraction — the goal is the structured knowledge held
*inside* documents.

Example `kb graph query` (FactorGraph → HAS_FACTOR → Factor → CONNECTS → Variable):

```bash
kb graph query "MATCH (fg:FactorGraph)-[:HAS_FACTOR]->(f:Factor)-[:CONNECTS]->(v:Variable) RETURN fg.id AS graph, f.id AS factor, v.id AS variable" --kb ./my-kb --json
```

### Provenance rules

Hard rules the system follows:

- **Every node/edge/claim carries provenance**: `origin` (`raw` | `synthesized` | `inferred`) and a **non-empty** `sources` list of document ids. The CLI rejects writes that lack them.
- **Raw documents are immutable**: stored read-only under `documents/raw/` and deduplicated by content hash.
- **Synthesized documents are separate outputs**: stored under `documents/synthesized/` and (in the agent workflow) linked back to their inputs via `DERIVED_FROM` edges.
- **`kb doc remove` is blocked** when other documents derive from the target document.
- **Never overwrite provenance evidence**: if a second source mentions an existing entity, append the new document id(s) to `sources` rather than overwriting.

### Statically checkable equations and algorithms

LaTeX is excellent for presentation but cannot be checked for mathematical or logical consistency. To bridge
this gap, a knowledge base can attach a **statically checkable representation** to any node whose meaning is
formal — an equation, an algorithm, a model, a residual, a numerical recipe. Two standardized languages are
supported:

*   **Mathematics**: a **SymPy-parseable canonical expression** in a `.sympy` file (one relation per line).
*   **Procedures**: a reference implementation in **Python 3.11 + NumPy** in a `.py` file.

All snippets are stored as **real files** under the `code/` directory (e.g., `code/equations/range_residual.sympy`),
and nodes reference them via the `code_path` property. There is no inline code property: real files are what
`ruff` wants and what `git diff` can version.

The checkable form never replaces the display form. A schema that also declares a `latex` property (as the seed
schema does on `Equation`) keeps both: LaTeX for fidelity to the source, SymPy for checking.

#### Two complementary roles in the formal schema

Two distinct roles are involved, and they are deliberately separated:

1.  **Statically checkable node types** — nodes that carry formal procedural code or symbolic mathematical
    formulations in external `.py` or `.sympy` snippet files (e.g. `Equation`, `Algorithm`, `Method`).
2.  **Symbol-bearing node types** — nodes that declare mathematical variables, quantities, and parameters
    (e.g. `Quantity`, `Variable`). They do not hold executable code files; instead, they define the formal
    symbol vocabulary that ground and validate the checkable expressions.

#### Statically checkable node types (Schema requirements)

Nothing in the `kb` CLI knows which node types in *your* domain are formal. A node type becomes **statically checkable**
purely by declaring the required property set in the schema — the checker discovers the statically checkable types by
inspecting the applied schema at runtime. The seed schema happens to make `Equation`, `Algorithm`, `Method`,
`Factor`, `MotionModel`, `SensorModel` and `NoiseModel` statically checkable, but that is a property of *that* schema.
A knowledge base about wave mechanics, orbital dynamics or bird flight patterns gets exactly the same machinery
by declaring the same properties on `DispersionRelation`, `Manoeuvre` or `FlightPattern`.

**What your schema must declare** on a node type, for that type to hold statically checked code:

| Property | Type | Meaning |
|---|---|---|
| `code_language` | `STRING` | `python` or `sympy`. Anything else is left `unchecked`. |
| `code_path` | `STRING` | Snippet file, relative to the KB root. Must stay inside the KB. |
| `code_entry` | `STRING` | Function name (Python) or primary relation name (SymPy). |
| `code_status` | `STRING` | Written by the checker: `ok` / `failed` / `unchecked`. |
| `code_checked_at` | `STRING` | Written by the checker: ISO-8601 UTC timestamp. |
| `code_checker` | `STRING` | Written by the checker: which checkers ran (`ast`, `ruff`, `sympy`). |
| `code_hash` | `STRING` | Written by the checker: content hash used to detect staleness. |

All seven must be present — the last four are the result slots the checker writes back into, so a partial
declaration is not a statically checkable type. Declaring them is free for a type that never carries code: the
properties simply stay null.

**What you get in return for checkable types**:

*   `kb code list` — an inventory of every snippet-carrying node with its status, plus `stale` (file edited
    since the last check) and `missing` (dangling `code_path`) flags.
*   `kb code show <Label>:<id>` — the snippet source, verbatim.
*   `kb code check` — static analysis, with the result persisted back onto the node:
    *   **SymPy**: expressions are parsed in a restricted namespace with `evaluate=False`, then their free
        symbols are cross-checked against the symbol-bearing nodes in the graph.
    *   **Python**: `ast.parse`, plus optional `ruff` linting with `--lint`.
*   Retrieval can then surface or filter facts by whether their formal content actually checks out.

Failures (like syntax errors) are recorded as `code_status: "failed"`, but they **never block a write**. Warnings
(like unknown symbols or linter findings) do not flip the status to `failed`. **No code is ever executed** by the
checker today.

```bash
# Check all snippets and output results as JSON
kb code check --lint --json

# List the status of all statically checkable nodes
kb code list --status failed
```

#### Symbol-bearing node types and cross-verification

Any node table declaring a `symbol STRING` property becomes an active symbol-bearing type discovered dynamically
by the CLI. In the robotics seed schema, these are `Quantity` and `Variable`; in other scientific domains they could for instance be
be `PhysicalConstant`, `StateCoordinate`, or `FieldComponent`.

Rather than holding code snippets, symbol-bearing nodes link into the equations and algorithms, unlocking a
three-tier consistency and linting pipeline across the CLI:

1. **Syntactic Quality & Symbol Hygiene (`kb graph lint`)**:
   - `symbol_quality` audit: Scans all symbol-bearing nodes to detect concatenated, corrupt, or uncleaned symbols
     (e.g., long concatenated OCR strings, unbalanced brackets, or strings $> 30$ characters).
   - Validates that symbols are clean mathematical identifiers (LaTeX, Greek letters, subscripted variables like
     `\alpha_r`, `v_x`, `\omega_z`).

2. **Grounding & Free Symbol Resolution (`kb code check`)**:
   - When checking `.sympy` expressions, `kb code check` resolves all free mathematical symbols against the graph's
     known symbol vocabulary (matching against node `symbol`, `id`, and `name`).
   - Warns on ungrounded or floating free symbols that have not been explicitly defined in the graph, ensuring every
     parameter and constant in an equation is documented.
   - Extracting intermediate parameters, normalization factors, constants, and sub-expression symbols (e.g. $C$, $N$,
     $\theta_k$) as explicit `Quantity` nodes connected via `USES_SYMBOL` maximizes symbol-graph interconnectivity.
     Without any symbol-bearing type, expressions still parse, but the grounding check is reported as skipped.

3. **Two-Way Symbolic & Graph Consistency Audit (`kb graph lint`)**:
   - **LaTeX vs. Graph Edges**: Verifies that every connected quantity linked to an `Equation` via `USES_SYMBOL`,
     `EXPRESSED_BY`, or `DEFINED_BY` actually appears in the equation's display `latex` formula.
   - **LHS Output Role Matching**: Inspects `.sympy` equations to ensure the left-hand-side output variable is
     connected to the equation via incoming `(Quantity)-[:EXPRESSED_BY]->(Equation)` (or `DEFINED_BY`).
   - **RHS Input Role Matching**: Verifies that all right-hand-side inputs and parameters in `.sympy` are linked
     via outgoing `(Equation)-[:USES_SYMBOL]->(Quantity)`.
   - **Role Inversions & Completeness**: Automatically flags inverted roles (e.g., an output linked via `USES_SYMBOL`
     instead of `EXPRESSED_BY`) and flags symbols present in code but missing from the graph or vice versa.

4. **Mathematical Derivations & Glossaries (`kb math`)**:
   - Powers `kb math glossary` and `kb math show` to produce unified, readable mathematical glossaries linking
     formal symbols directly to their units, textual definitions, source papers, and defining equations.

### Schema evolution

Schema evolves via versioned, numbered migrations:

- `kb schema migrate` scaffolds a numbered empty `.gql` migration in `schema/migrations/`.
- `kb schema apply` applies all pending migrations **idempotently**.
- `kb schema validate` checks the database against the target schema.
- `kb schema status` lists applied vs pending migrations.

Migrations are append-only and never edited once applied.

Supported migration operations:
- Native `.gql` migrations: Direct ISO GQL / openCypher DDL statements.
- `create_node_table`: Declare a new node table and properties (legacy JSON).
- `create_rel_table`: Declare a new relation table and allowed pairs (legacy JSON).
- `add_rel_pair`: Extend an existing relation table with new allowed `(from, to)` pairs (legacy JSON).
- `cypher`: Execute raw DDL/DML Cypher statements verbatim.

### CLI reference

| Command group | Subcommands |
|---|---|
| `kb init` | `<path>` (and `--name`, `--json`) |
| `kb schema` | `show`, `validate`, `apply`, `migrate`, `status` |
| `kb doc` | `add`, `list`, `show`, `text`, `remove`, `cite`, `stubs`, `match-stubs`, `reconcile-stub`, `clean` |
| `kb graph` | `upsert-node`, `upsert-edge`, `upsert-claim`, `query`, `export`, `batch`, `lint`, `dedupe` |
| `kb index` | `build` |
| `kb search` | (hybrid retrieval + context bundle) |
| `kb code` | `list`, `show`, `check` |
| `kb math` | `show`, `glossary` |

Machine-readable output: use `--json`. All KB-aware subcommands (everything except `kb init`) accept `--kb <path>` (default: `.`).

### Development & Quality Checks

Run all codebase quality gating checks (linter and unit/e2e tests) with:

```bash
./scripts/check.sh
```

Individual checks:
- Linter: `ruff check .`
- Test suite: `pytest -W error` (130 tests)
- Static code check on a knowledge base: `kb code check --all --kb <path>`
- Graph quality audit: `kb graph lint --kb <path>`

Optional extras:

- `graph` (traverse-embedded)
- `embed` (fastembed)
- `math` (sympy)
- `pdf` (pypdf)
- `dev` (pytest, ruff)

`./scripts/bootstrap.sh` installs all optional extras. Embeddings are pluggable via `kb.toml`:

- `local` uses a fastembed model download (after which indexing is offline).
- `hash` is a deterministic offline fallback (used in tests).

### License

MIT.
