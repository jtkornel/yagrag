# Plan: Align the GraphRAG KB schema with MaRDI MathModDB / MathAlgoDB

Status: **DECIDED — ready for implementation**
Date: 2026-08-16
Author: Zed agent (for @jtkornel)

## Decisions (from user Q&A, 2026-08-16)

1. **Scope**: full alignment, with the adaptations recorded below.
2. **Naming**: keep existing names where semantically close (e.g. `Equation`), PascalCase throughout; no renames for style alone. **Exception**: adopt the paper-era MaRDI semantics for the application side — `ApplicationDomain` + `ApplicationProblem` (not the newer "academic discipline"/"research problem", which are too narrow for an engineering-oriented KB).
3. **Assumptions**: follow the MaRDI approach *where the source is formal* — assumptions stated as equations are captured as `Equation` nodes linked via `ASSUMES`. **Revised 2026-08-16**: `Assumption` remains a first-class, non-deprecated type for informally-stated assumptions; documents are captured at their own level of formalization. Both `Equation` and `Assumption` are valid `ASSUMES` targets. The 17 existing Assumption nodes stay as-is.
4. **Benchmarks**: yes, adopt a first-class `Benchmark` node type (MathAlgoDB semantics: `Benchmark —INSTANCE_OF→ ComputationalTask`).
5. **MaRDI data import**: yes — for matching types and only generic (non-domain-alien) data relevant to robotics/state-estimation/etc. Must carry attribution: per-node `origin="mardi"` + source IRIs, ontology-level attribution in `docs/mardi/ATTRIBUTION.md`. Prepare as if kb-data will be public one day.
6. **Edge qualifiers**: Option A — a machine-readable **schema companion** (`schema/schema_companion.json`) + query-time expansion; single-direction storage ("lean" convention like MathModDB); soft enforcement in extraction/graph-update flows. Extended beyond bare qualifiers to include Wikidata-style per-type labels, descriptions, usage notes, examples, domain/range, and mardi_source cross-references (see §4.3). Serves prompt injection, linting, user help, and a future OWL/RDF export.
7. **Method/Algorithm**: keep `Method` for now (no consolidation).

## 1. Sources and where to look things up

| Source | What it is | Local copy / URL |
|---|---|---|
| Schembera et al. 2024, "Ontologies for Models and Algorithms in Applied Mathematics and Related Disciplines" (arXiv:2310.20443v2) | High-level paper describing the merged MathModDB + AlgoData ontologies and the MSO (modeling–simulation–optimization) workflow | https://arxiv.org/abs/2310.20443 (HTML: https://arxiv.org/html/2310.20443v2) |
| MathModDB ontology + KG (v2.0.0, 2026-07-28) | OWL/RDFXML export of the Wikibase-backed model ontology. 7 classes, ~4300 individuals | `docs/mardi/MathModDB.owl` (8.7 MB — inspect with `docs/mardi/inspect_*.py`, do **not** paste into LLM context) |
| MathModDB docs (Widoco) | Human-readable class/property documentation | https://mardi4nfdi.github.io/MathModDB/ |
| MathAlgoDB ontology (v0.1) | Small Turtle ontology: 5 classes, ~15 property pairs | `docs/mardi/MathAlgoDB_Ontology.ttl` (readable directly) |
| MathAlgoDB data | Sample KG (numerical analysis + model order reduction) | `docs/mardi/MathAlgoDB_Data.ttl` |
| MathAlgoDB docs / frontend | Browsable KG (Jena Fuseki + SPARQL + Django) | https://mardi4nfdi.github.io/MathAlgoDB/ and https://mathalgodb.mardi4nfdi.de/ |
| MaRDI portal | Wikibase instance, canonical up-to-date data | https://portal.mardi4nfdi.de |

Inspection helpers (rdflib-based, run with `.venv/bin/python`):
- `docs/mardi/inspect_mathmoddb.py` — classes, datatype properties, per-class individual counts
- `docs/mardi/inspect_props.py` — every Wikibase P-property with subject→object type usage counts
- `docs/mardi/inspect_core.py` — core relation examples + full dump of a sample model individual

## 2. What the MaRDI data model actually is

### 2.1 MathModDB (models side) — 7 classes

| Class | Individuals | Role |
|---|---|---|
| mathematical model | 229 | Central anchor |
| formula | 713 | Mathematical formulation (has MathML/LaTeX "defining formula") |
| quantity | 813 | Concrete quantities appearing in formulas |
| quantity kind | 68 | Abstract quantity types (linked to QUDT) |
| computational task | 173 | What you *do* with a model (simulation, parameter estimation, …) — this is the bridge to algorithms |
| research problem | 107 | Application problem being solved |
| academic discipline | 50 | Application domain |

Core object properties (Wikibase P-IDs in parentheses; the OWL export is "lean" — only one direction is materialized, inverses are left to a reasoner):

- `modelled by` (P1513): research problem → mathematical model
- `used by` (P147): mathematical model → computational task  ← **the MathModDB→MathAlgoDB bridge** (paper: "uses algorithmic problem")
- `contains` (P1560): model→formula, task→quantity/formula, discipline→research problem, and intra-class part-whole (model→model, formula→formula, task→task)
- `specialized by` (P1684): intra-class specialization hierarchy for **all** classes (transitive in spirit)
- `assumes` (P1674): model/formula → formula (assumptions expressed as formulas)
- Transformation properties between models/formulas/tasks: `approximated by` (P1655), `discretized by` (P1656), `linearized by` (P1657), `nondimensionalized by` (P1658), `has weak formulation` (P1736)
- `similar to` (P1691): symmetric intra-class similarity
- `solution to` (P1692): formula → model
- `corresponds to` (P1869): task → external entity
- Datatype properties: `defining formula` (P989, MathML/LaTeX literal), `in defining formula` (P983, symbol→formula link), `Description (long)` (P1459), plus a large long tail of external-identifier properties (Wikidata QID P12, QUDT IDs, MSC ID P226, dozens of encyclopedia IDs — mostly noise for our purposes)

### 2.2 MathAlgoDB (algorithms side) — 5 classes

`problem` (Algorithmic Task), `algorithm`, `benchmark`, `software`, `publication`.

Properties (each defined as an inverse pair; **qualifiers are first-class OWL features**):
- `specializes`/`specializedBy` (problem→problem, **transitive**)
- `subclassOf`/`hasSubclass` (algorithm→algorithm, **transitive**)
- `hasComponent`/`componentOf` (algorithm→algorithm, **transitive**)
- `relatedTo` (algorithm→algorithm, **symmetric** and transitive)
- `solves`/`solvedBy` (algorithm→problem)
- `instanceOf`/`instantiates` (benchmark→problem)
- `implements`/`implementedBy` (software→algorithm)
- `tests`/`testedBy` (software→benchmark)
- Publication roles, all algorithm→publication with inverses: `inventedIn`, `analyzedIn`, `studiedIn`, `appliedIn`, `reviewedIn`; plus `documentedIn`/`usedIn` for software/benchmark→publication
- `dc:hasIdentifier` (DOI/URL literal), `:category` (free-text category tag)

### 2.3 Key design ideas worth adopting

1. **Model ↔ algorithm decoupling via an intermediate "task/problem" node.** Models don't link to algorithms directly; they link to *computational tasks / algorithmic problems*, which algorithms then solve. New algorithms become available to all models sharing a task without re-linking.
2. **Edge qualifiers**: transitivity (specialization hierarchies), symmetry (relatedTo), inverse pairs. Our graph engine stores directed edges, so we emulate: inverse pairs → store one direction (the "lean" MathModDB choice) and document the canonical direction; transitivity/symmetry → recorded as schema metadata and handled in query/traversal helpers.
3. **Rich intra-class relations**: specialization hierarchies on every class, plus mathematical transformations (discretizedBy, linearizedBy, approximatedBy, weak formulation) — these map beautifully onto how our users navigate models.
4. **Assumptions as nodes** (formulas/models that other models `assume`), not free text.
5. **External identifiers as first-class properties** (Wikidata QID, QUDT, MSC, DOI) for future federation with the MaRDI portal / Wikidata.

## 3. Current state of our schema

Defined in `schema/migrations/0001–0004` (mirrored in `kb-data/schema/migrations/`). 30 node types, 28 edge types. Overlap and gaps vs. MaRDI:

| Our type | MaRDI counterpart | Assessment |
|---|---|---|
| `Model` | mathematical model | Keep, align properties |
| `Equation` (has `latex`) | formula (has defining formula) | Near-perfect match; rename considered but **keep `Equation`** (see Q2) |
| `Quantity` (`symbol`, `unit`) | quantity + quantity kind | We conflate the two levels; consider splitting or adding `kind` link |
| `Algorithm` | algorithm | Keep |
| `Method`, `Solver`, `StateEstimator`, `MotionModel`, `SensorModel`, `Factor`, `FactorGraph`, `NoiseModel` | algorithm subclasses / components | Map onto MathAlgoDB `subclassOf`/`hasComponent` hierarchies |
| `Application` | research problem | Rename/realign to `ResearchProblem` semantics |
| `Concept` | academic discipline (partially) | Split out `AcademicDiscipline`? |
| `Document` | publication | Align publication-role edges |
| `Dataset` | benchmark (partial) | Add `Benchmark` or treat Dataset as benchmark |
| `Tool` | software | Align; `IMPLEMENTS` edge already exists |
| `Task` | computational task / algorithmic problem | **This is the key addition** — promote to first-class `ComputationalTask` bridge node |
| `Application` | application problem (paper) / research problem (v2.0) | Realign to `ApplicationProblem` |
| `Concept` (partial) | application domain (paper) / academic discipline (v2.0) | Split out `ApplicationDomain` |
| `Assumption` | assumes-edge target (formula) | MaRDI models assumptions *as formulas*; we have a dedicated node type — decide (Q3) |
| — | quantity kind, transformation edges, publication-role edges | **Missing** |
| `Claim` (reified predicate+qualifiers) | — | Ours alone; keep — it covers qualifiers MaRDI expresses in OWL |

Domain-specific types we have that MaRDI lacks (Robot, Sensor, CoordinateFrame, System, Variable, Metric, Venue, Author, Acronym) stay as extensions.

## 4. Proposed target schema (migration 0005+)

### 4.1 New / changed node types

1. **`ComputationalTask`** (new) — the bridge: `Model —USED_BY→ ComputationalTask —SOLVED_BY→ Algorithm`. Properties: standard provenance + `category`.
2. **`QuantityKind`** (new) — abstract quantity types; `Quantity —SPECIALIZES→ QuantityKind` (or `HAS_KIND`); optional `qudt_id`, `isq_dimension`.
3. **`Benchmark`** (new) — `Benchmark —INSTANCE_OF→ ComputationalTask`, `Tool —TESTS→ Benchmark`.
4. **`ApplicationProblem`** — realign (retype) `Application` nodes; edge pair `ApplicationProblem —MODELLED_BY→ Model` (+ canonical inverse `MODELS`).
5. **`ApplicationDomain`** (new, lightweight anchor) — `ApplicationDomain —CONTAINS→ ApplicationProblem`; optional external-classification props (`msc_id`, `wikidata_qid`, `category` free text). Adopts the paper-era semantics; per user decision, not limited to research.
6. `Equation`: keep name; properties unchanged (`latex`).
7. External-identifier properties on relevant types: `wikidata_qid`, `doi`, `qudt_id`, `msc_id` (plain STRING columns, nullable).
8. `Assumption`: **deprecated** — existing nodes retyped as `Equation` (or folded into `Model`/`ComputationalTask` where equationally-phrased); `ASSUMES` edge retained with equation-style targets. A domain rule in extraction/graph-update prompts: prefer phrasing assumptions as equations and link via `ASSUMES`.

### 4.2 New / changed edge types

| Edge | Direction (canonical) | Qualifier | MaRDI source |
|---|---|---|---|
| `MODELLED_BY` | ResearchProblem → Model | inverse materialized on demand | P1513 |
| `USED_BY` (task bridge) | Model → ComputationalTask | — | P147 |
| `SOLVES` | Algorithm → ComputationalTask | — | MathAlgoDB solves |
| `INSTANCE_OF` | Benchmark → ComputationalTask | — | instanceOf |
| `TESTS` | Tool → Benchmark | — | tests |
| `SUBCLASS_OF` | Algorithm → Algorithm | **transitive** | subclassOf |
| `HAS_COMPONENT` | Algorithm → Algorithm | **transitive** | hasComponent |
| `RELATED_TO` | Algorithm → Algorithm | **symmetric** (+transitive) | relatedTo |
| `DISCRETIZED_BY` | Model/Equation → Model/Equation | — | P1656 |
| `LINEARIZED_BY` | Model/Equation/Task → same | — | P1657 |
| `APPROXIMATED_BY` | Model/Equation → same | — | P1655 |
| `NONDIMENSIONALIZED_BY` | Quantity → Quantity | — | P1658 |
| `HAS_WEAK_FORMULATION` | Equation → Equation | — | P1736 |
| `SOLUTION_TO` | Equation → Model | — | P1692 |
| `SIMILAR_TO` | any → any (same type) | **symmetric** | P1691 |
| `INVENTED_IN` / `ANALYZED_IN` / `STUDIED_IN` / `APPLIED_IN` / `REVIEWED_IN` | Algorithm → Document | — | MathAlgoDB publication roles |
| `DOCUMENTED_IN` / `USED_IN` | Tool/Benchmark → Document | — | documentedIn/usedIn |

Existing `SPECIALIZES` gains **transitive** metadata and extends to all MaRDI classes (Model, Equation, Quantity, ComputationalTask, ResearchProblem, AcademicDiscipline). Existing `ASSUMES` is kept and aligned so targets are typically Equations (per MaRDI) while still allowing our `Assumption` nodes.

### 4.3 Schema companion (semantics not expressible in GQL)

GQL migrations define *structure* only; they cannot express qualifiers, domain/range constraints, or human-readable semantics, and are not machine-parseable by our tooling. We therefore add a **schema companion**: `schema/schema_companion.json`, with one entry per node type and edge type:

```json
{
  "edges": {
    "MODELLED_BY": {
      "label": "modelled by",
      "description": "An application problem P is modelled by a mathematical model M if M formalizes P so it can be treated with mathematical/numerical methods.",
      "usage": "Connect an ApplicationProblem to the Model that addresses it.",
      "example": "microfracture detection in porous media —MODELLED_BY→ X-ray transform model",
      "domain": ["ApplicationProblem"], "range": ["Model"],
      "inverse_of": "MODELS", "transitive": false, "symmetric": false,
      "mardi_source": "MathModDB P1513"
    }
  },
  "nodes": { "ComputationalTask": { "label": "...", "description": "...", "mardi_source": "MathModDB Q6534247" } }
}
```

Decided rules:
- **Storage stays lean** (MathModDB convention): one canonical direction per edge pair, recorded in the companion; no materialized inverses or closures.
- Query/traversal helpers in `kb/` read the companion to expand `symmetric`/`transitive`/`inverse_of` at query time (bounded closure; our hierarchies are shallow, depth 2–4).
- The companion is the single source of truth for semantics and feeds four consumers: (1) extraction/graph-update prompt injection, (2) a lint pass checking GQL↔companion consistency and domain/range violations in data, (3) a future RDF/OWL export (`rdfs:label`/`rdfs:comment` + OWL property axioms), (4) user-facing `kb explain`-style help.

## 5. Data migration plan for `kb-data`

0. **Status update (2026-08-16, final)**: all steps implemented.
   - `0005_mardi_alignment.gql` written, mirrored, and **applied to live `kb-data`** (snapshot in `kb-data/backups/pre-mardi-20260816/`); `validate` reports ok.
   - `schema/schema_companion.json` + `kb/schema/companion.py` loader + `tests/test_companion.py`; full suite 139 passed.
   - `scripts/migrate_mardi_alignment.py` executed: review queue at `kb-data/mardi_migration_review.json` (17 Assumptions, 1 Task, 1 QuantityKind candidate; no Application nodes existed); 24 derivable DOIs logged only (Document lacks a `doi` column; grafeo cannot add columns to existing types).
   - Extraction/graph-update skills updated to reference the companion (canonical directions, qualifiers, new types, dual-form assumptions).
   - **Revision (2026-08-16)**: Assumption deprecation reversed — dual-form `ASSUMES` (Equation or Assumption targets); review queue assumptions marked informational; existing 17 Assumption nodes retained.
   - `kb/graph/upsert.py`: `"mardi"` added to VALID_ORIGINS (attribution requirement).
   - `scripts/import_mardi_reference.py` executed: keyword-filtered import from MathModDB + MathAlgoDB with per-node `origin="mardi"` + portal-IRI sources; floating imported nodes pruned. Result: **296 connected reference nodes** (87 Models, 74 ComputationalTasks, 63 Algorithms, 33 Equations, 13 ApplicationProblems, 13 Quantities, 6 Benchmarks, 6 Tools, 1 QuantityKind) + ~300 edges (SPECIALIZES 141, SUBCLASS_OF 57, USED_BY 53, IMPLEMENTS 22, MODELLED_BY 13, SOLVES 9, ...). `kb graph lint` clean (0 floating). Import stats: `kb-data/mardi_import_stats.json`.
   - **Review queue processed (2026-08-16)**: `task-angular-velocity-estimation` retyped Task → ComputationalTask (edges preserved; no Task nodes remain). QuantityKind dispositions applied: retyped Lagrange multiplier / controllability Gramian / observability Gramian / normal mode momentum to QuantityKind; Gaussian process + global/local stochastic process SPECIALIZES the `stochastic process` kind; role quantities linked via HAS_KIND; generalized/dimensionless variants via SPECIALIZES + HAS_KIND; `qty-effective-tread-width` symbol fixed to `B_eff`. 17 Assumptions retained as-is (informational). `kb graph lint` clean, 139 tests pass.
   - **Grounded ComputationalTask mapping (2026-08-16)**: `scripts/ground_computational_tasks.py` created 12 local ComputationalTask nodes and 39 edges (SOLVES / USED_BY / APPLIES_TO / MENTIONS), each grounded in a quoted passage from the source document (recorded in task `summary`; edges carry `origin="raw"` + the citing doc). The migration-era `task-angular-velocity-estimation` was merged into the grounded `task_angular_velocity_state_estimation_sswmr` (Robot + Document links preserved, APPLIES_TO duplicates subsumed by SOLVES). All 3 local Models now have USED_BY chains; 20 local Algorithms + estimators + methods linked to tasks. `kb graph lint` clean; 139 tests pass.
   - **Review queue closed (2026-08-16)**: `kb-data/mardi_migration_review.json` removed after all items were resolved (17 Assumptions kept informal by decision; Task retyped; QuantityKind splits applied). `scripts/migrate_mardi_alignment.py` retained as a rerunnable audit.
   - Remaining manual work: tune the importer's keyword lists if the domain filter proves too broad/narrow, and wire MaRDI tasks to local Algorithms where matches exist (e.g. local EKF ↔ MaRDI Kalman-filter task hierarchy).
1. **Snapshot first**: copy `kb-data/graph.grafeo` + `kb-data/graph.duckdb` (and `kb-data/graph.grafeo.spill` if present) into `kb-data/backups/pre-mardi-YYYYMMDD/`.
2. **Schema migrations**: add `schema/migrations/0005_mardi_alignment.gql` (new node/edge types + new columns) and mirror into `kb-data/schema/migrations/`. Run against the snapshot first, then the live DB.
3. **Node remapping script** (`scripts/migrate_mardi_alignment.py`):
   - `Application` → `ApplicationProblem` (retype nodes; rewrite edges `APPLIES_TO` → `MODELLED_BY` where target was a `Model`; otherwise keep `APPLIES_TO`).
   - `Assumption` → retype as `Equation` when equationally-phrased (human review list prepared as JSON), else fold into parent and record in migration log (deprecates type; `ASSUMES` edge continuation).
   - Create `ComputationalTask` nodes from existing `Task` nodes that are computational (review list; others stay `Task` or become `Concept`).
   - Rewire `Model —SOLVED_BY→ Algorithm` and `Model —USES→ Algorithm` edges to `Model —USED_BY→ ComputationalTask` for identifiable tasks.
   - `Tool` nodes: keep (add `software` category when changed contextually); `IMPLEMENTS` edges unchanged.
   - Existing `Algorithm`→`Algorithm` `USES`/`SPECIALIZES` edges → `HAS_COMPONENT`/`SUBCLASS_OF` where semantics fit (review queue).
   - Split `Quantity` nodes that are kinds (no `symbol`, generic names like "length"/"time") into `QuantityKind` + `SPECIALIZES` link.
   - Populate `wikidata_qid`/`doi` where trivially derivable from existing `url` fields.
4. **Schema companion**: `schema/schema_companion.json` + loader + tests (labels, descriptions, usage, examples, domain/range, transitive/symmetric/inverse_of, canonical direction, mardi_source per type).
5. **Extraction-skill updates**: update `.agents/skills/deep-knowledge-extraction` and `graph-update` prompts to emit the new node/edge types (esp. `ComputationalTask` bridging and publication-role edges) and the assumption-as-equation convention.
6. **Validation**: 
   - `pytest tests/` plus a migration test that loads the pre/post graph and asserts node/edge counts per type and zero dangling edges.
   - Spot-check 2–3 documents end-to-end (e.g. raw-0010 factor-graph paper): model → task → algorithm chains navigable.
7. **Attribution + import tooling**:
   - `docs/mardi/ATTRIBUTION.md` records MathModDB (Zenodo DOI 10.5281/zenodo.14887915) and MathAlgoDB licenses (CC-BY 4.0) and both papers' citations.
   - `scripts/import_mardi_reference.py` (separate change-set, after main migration): imports a vetted subset of MathModDB/MathAlgoDB individuals matching our domains (robotics, state estimation, numerical methods). Each node carries `origin="mardi"` and `sources=[portal IRI]` so attribution survives any future public export.
   - Import mapping must use the **MaRDI canonical identifiers** into `wikidata_qid` / portal IRI fields to enable future federated linking.

## 6. Note on the MaRDI class evolution (Application → Research)

Between the paper (2023/2024) and MathModDB v2.0.0 (2026-07), MaRDI renamed:
- `Application Domain` → `academic discipline` ("academic field of study or profession")
- `Application Problem` → `research problem` ("knowledge gap addressable by research")

Verified against the v2.0 OWL export descriptions. We deliberately adopt the **paper-era names/semantics** (`ApplicationDomain`, `ApplicationProblem`) because this KB serves applied/engineering content, not only research. External subject classifiers (MSC, PhysSH, ANZSRC, etc.) are treated as optional properties/hooks on `ApplicationDomain`, matching MaRDI's stated plan to outsource discipline taxonomies to external ontologies.

## 7. Future extension noted by user

A browsable web frontend like the MaRDI portal (Wikibase) or MathAlgoDB (Jena Fuseki + SPARQL + Django). Not in scope for this migration, but the alignment (stable IDs, external identifiers, canonical edge directions) is designed to make a later SPARQL/RDF export straightforward.
