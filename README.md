# graphrag_knowledge_base

A **lightweight, local-first GraphRAG system** for building and maintaining
*domain-specific property-graph databases and document collections*.

The system is split in two:

- **A deterministic CLI (`kb`)** — implemented in Python, performs all
  document-store and graph-database operations. **No LLM calls inside the CLI.**
- **An agent layer** — a set of `.md` skill files (in `.junie/skills/`) that tell
  an LLM agent *how* to interview the user, model the domain, ingest and analyse
  documents, evolve the schema, update the graph, synthesize documents, and
  answer questions — always by invoking the deterministic CLI.

The knowledge-graph philosophy is **Wikidata, not Wikipedia**: the graph is
designed to capture structured domain knowledge that lives *inside* documents
(variables, factors, methods, equations, quantities, claims and their
relations), not just bibliographic links between documents. See the plan in
`.junie/plans/graphrag-knowledge-base-bootstrap.md` for the full design.

### Status

First milestone complete (feature-complete for this stage):

- **Deterministic CLI (`kb`)**: scriptable, offline, provenance-enforced operations with **no LLM calls**.
- **Document store**: raw immutable ingestion + synthesized documents with provenance.
- **Kuzu graph + layered seed schema**: a domain-appropriate property graph with a reified `Claim` pattern.
- **Hybrid retrieval**: embeddings + full-text + graph context bundles via `kb search`.
- **Agent skills**: structured `.md` skill files that drive reasoning purely by invoking the CLI.

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
  graph.kuzu/             # embedded Kuzu database directory (created on first `kb schema apply`)
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
- The agent is guided by the markdown skills in `.junie/skills/` and acts only by invoking the CLI.

Benefits: the tool is harness-agnostic, deterministic, unit-testable, and the “intelligence” is swappable.

### Agent skills

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

### The layered schema philosophy

**Wikidata, not Wikipedia**: the goal is a graph of structured facts *inside* documents — not a fuzzy
“what this paper is about” summary.

The seed schema in `schema/migrations/0001_seed_domain.json` defines **29 node types** and **28 relation types**.

- **Layer 1: document/bibliographic layer** (general, reusable across research fields)
  - Node types: `Document`, `Author`, `Venue`
  - Relation types: `CITES`, `AUTHORED_BY`, `PUBLISHED_IN`, `DERIVED_FROM`, `MENTIONS`, `DEFINES`, `SUPPORTS`, `CONTRADICTS`

- **Layer 2: deep domain knowledge** (models internals of sensor fusion / factor graphs / UGV navigation)
  - Node types: `FactorGraph`, `Variable`, `Factor`, `StateEstimator`, `MotionModel`, `SensorModel`, `NoiseModel`, `Sensor`, `Solver`, `Equation`, `Quantity`, `Assumption`, `CoordinateFrame`, `Robot`, `Task`, `Dataset`, `Metric`, `Tool` and others
  - Relation types: wired by `HAS_VARIABLE`, `HAS_FACTOR`, `CONNECTS`, `ESTIMATES`, `MEASURES`, `ASSUMES`, `SOLVED_BY`, `DEFINED_BY`, `EVALUATED_ON`, `EXPRESSED_IN`, `HAS_NOISE` and others

- **Reified `Claim` layer**
  - A claim is its own node (subject–predicate–object, plus qualifiers, confidence, sources).
  - Claims are linked by `ABOUT`/`HAS_OBJECT`.
  - Documents attach via `SUPPORTS`/`CONTRADICTS`.
  - This makes conflicting assertions from different sources coexist with full provenance instead of being flattened.

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

#### Terminology

Two distinct roles are involved, and they are deliberately named differently:

*   **Statically checkable node type** — a node type whose schema declares the seven `code_*` properties, so each
    of its nodes may point at a snippet that `kb code check` verifies. *Statically* means the check reads the
    stored text only: nothing is executed and no LLM is consulted. This is the umbrella term, and it covers both
    supported languages — the SymPy expressions as much as the Python procedures.
*   **Symbol-bearing node type** — a node type whose schema declares a `symbol` property. Such nodes are not
    checked themselves; they contribute the symbol vocabulary that a SymPy snippet's free symbols are compared
    against.

A type can be both, one, or neither. Use these two terms only — earlier drafts also said "code-bearing" and
"machine-checkable", which blurred the two roles. The `code_*` property prefix is retained as-is and always
refers to the stored snippet, whatever its language: a SymPy expression lives in a `code_path` file too.

#### This is a schema requirement, not a built-in list of types

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

Optionally, a node type may declare a **`symbol`** `STRING` property. Every type that does becomes a source of
known symbols for SymPy symbol-consistency checking (its `symbol`, `id` and `name` values all count). In the
seed schema those are `Quantity` and `Variable`; in another domain they might be `PhysicalConstant` or
`FieldComponent`. Without any symbol-bearing type, expressions still parse — the symbol cross-check is simply
reported as skipped.

**What you get in return**, for free, on every statically checkable type:

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

#### Execution roadmap

The system follows a staged path towards verified knowledge:
*   **Stage 0 (Current)**: Static checking only. Syntax validation and graph-wide symbol consistency. No execution, no sandbox.
*   **Stage 1**: Local execution via `kb code run`. Uses `resource` limits and whitelisted imports for soft isolation in a local subprocess.
*   **Stage 2**: Full container/VM sandbox. Hard isolation with no network and read-only filesystems, also hosting the agent's own verification scripts.
*   **Stage 3**: Verification as data. `Check` nodes linked by `VERIFIES` edges define expected inputs/outputs, turning the KB into a reproducible regression suite.

### Schema evolution

Schema evolves via versioned, numbered migrations:

- `kb schema migrate` scaffolds a numbered empty JSON migration in `schema/migrations/`.
- `kb schema apply` applies all pending migrations **idempotently**.
- `kb schema validate` checks the database against the target schema.
- `kb schema status` lists applied vs pending migrations.

Migrations are append-only and never edited once applied.

**Deliberate limitation — no `ALTER TABLE` yet.** The migration model supports
`create_node_table`, `create_rel_table` and raw `cypher` only; there is no
`add_node_property` operation. While no long-lived knowledge base exists (every
KB in this repo — tests and `examples/factor-graph-slam/` — is rebuilt from
scratch by a script), adding a property to an existing type means **editing the
seed migration `schema/migrations/0001_seed_domain.json` and rebuilding**. An
`add_node_property` / `ALTER TABLE` operation will be added once a KB has to
survive a schema change.

### CLI reference

| Command group | Subcommands |
|---|---|
| `kb init` | `<path>` (and `--name`, `--json`) |
| `kb schema` | `show`, `validate`, `apply`, `migrate`, `status` |
| `kb doc` | `add`, `list`, `show`, `text`, `remove` |
| `kb graph` | `upsert-node`, `upsert-edge`, `upsert-claim`, `query`, `export` |
| `kb index` | `build` |
| `kb search` | (hybrid retrieval + context bundle) |
| `kb code` | `list`, `show`, `check` |

Machine-readable output: use `--json`. All KB-aware subcommands (everything except `kb init`) accept `--kb <path>` (default: `.`).

### Development

Run tests with:

`.venv/bin/python -m pytest -q` (103 tests).

Optional extras:

- `graph` (kuzu)
- `embed` (fastembed)
- `math` (sympy)
- `pdf` (pypdf)
- `dev` (pytest, ruff)

`./scripts/bootstrap.sh` installs all optional extras. Embeddings are pluggable via `kb.toml`:

- `local` uses a fastembed model download (after which indexing is offline).
- `hash` is a deterministic offline fallback (used in tests).

### License

MIT.
