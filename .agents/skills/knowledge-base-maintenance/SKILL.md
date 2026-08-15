---
name: knowledge-base-maintenance
description: Audit, clean, and maintain knowledge base quality across documents, citations, domain entities, and symbols. TRIGGER when the user asks to clean up the KB, check data quality, deduplicate entities, canonicalize citations, or run maintenance passes.
---

## When to use

Use this skill periodically after batch ingestion sessions, or whenever the user asks to "clean up", "audit", "deduplicate", or "check data quality" in the knowledge base. It applies deterministic, reviewable maintenance passes to keep the knowledge graph tidy, canonical, and free of duplicates without losing provenance.

Trigger this skill when:
*   Multiple new papers have been ingested and extracted.
*   The user asks to "audit the graph", "run linter", or "clean up the knowledge base".
*   You need to merge duplicate entities across papers (e.g., duplicate tools, datasets, or concepts).
*   You need to canonicalize literature URLs/DOIs and merge placeholder citation stubs.
*   You want to inspect top-cited external literature to recommend future paper acquisitions.

## Steps

### 1. Graph Quality Audit (`kb graph lint`)
Always start by checking structural and capability integrity:
```bash
kb graph lint
```
*   **What it checks**:
    *   **Floating nodes**: Flags any node with 0 connected relationship edges.
    *   **Claim violations**: Flags claims missing `summary` assertions, missing predicates, or with excessively long `name` labels.
    *   **Symbol anomalies**: Flags corrupted, concatenated, or missing LaTeX symbols.
    *   **Symbolic consistency**: Verifies that quantities and variables connected via `EXPRESSED_BY` or `USES_SYMBOL` appear in the equation's LaTeX formula.
    *   **Acronym quality**: Flags `Acronym` nodes missing `short_form` or `expansion`, or having redundant enclosing parentheses in `short_form`.
    *   **Code status**: Flags checkable nodes with failed AST or SymPy syntax checks.

### 2. Document & Citation Maintenance (`kb doc clean`)
Clean and canonicalize bibliographic references and external literature placeholders:
```bash
# First inspect proposed changes in dry-run mode
kb doc clean

# Execute the cleanups safely
kb doc clean --apply
```
*   **What it does**:
    *   **URL/DOI Canonicalization**: Standardizes DOIs to `https://doi.org/10.xxxx` and arXiv links to `https://arxiv.org/abs/xxxx.xxxxx`.
    *   **Title Hygiene**: Strips leading citation numbers (e.g. `[14] ...`) and normalizes casing.
    *   **Near-Duplicate Stub Merging**: Merges placeholder stubs referencing the same literature, redirecting `CITES` edges and accumulating citing provenance.
    *   **Orphaned Stub Pruning**: Removes unreferenced stubs that lost all incoming citations.

### 3. Domain Entity Deduplication (`kb graph dedupe`)
Detect and merge near-duplicate domain entities across node tables:
```bash
# Dry-run inspection across all domain tables
kb graph dedupe

# Filter to a specific table if desired (e.g. Tool, Dataset, Concept)
kb graph dedupe --label Tool

# Apply merges safely
kb graph dedupe --apply
```
*   **What it does**:
    *   Clusters near-duplicate entities using token overlap ($\ge 0.85$) while protecting distinct symbols on mathematical nodes (`Quantity`) and distinct short forms on `Acronym` nodes.
    *   Selects the canonical node based on highest connected graph degree and metadata richness.
    *   Re-routes all incoming and outgoing edges (`USES`, `EVALUATED_ON`, `IMPLEMENTS`, `ABOUT`, `EXPRESSED_BY`) to the canonical node.
    *   Combines `sources` lists (strict union) and deletes the duplicate entity.

### 4. Code & Snippet Verification
Verify that all stored equation and algorithm snippets on disk are checked and in sync:
```bash
kb code check --all
```

### 5. Review Cited Literature Recommendations (`kb doc stubs`)
Identify external papers frequently cited across the collection that are not yet in the knowledge base:
```bash
kb doc stubs --min-cites 2
```
*   Use this list to recommend high-value foundational papers for future acquisition.

### 6. Full Quality Gating (`./scripts/check.sh`)
Run the unified quality gating check across linter and test suite:
```bash
./scripts/check.sh
```
*   Runs `ruff check .` across the codebase.
*   Runs `pytest -W error` unit and end-to-end test suites.
*   Optionally pass a KB directory (e.g. `./scripts/check.sh <kb-dir>`) to also check domain snippets in that knowledge base.

## Rules

*   **Dry-Run First**: Always inspect proposed changes with dry-run commands before executing with `--apply`.
*   **Non-Destructive Provenance**: Merging nodes must always take the **union** of their `sources` lists. Citing document references must never be deleted.
*   **Symbol Protection**: Never merge two `Quantity` nodes if their `symbol` properties differ, even if their titles sound similar (e.g., $F_R$ thrust vs. $f_r$ resistance coefficient).
*   **Acronym Disambiguation Protection**: Never merge two `Acronym` nodes if their `short_form` properties differ (e.g. `EKF` vs `UKF`).
*   **Re-Lint After Cleanups**: Always conclude maintenance by running `kb graph lint` to verify that 0 warnings or errors remain.

## Example Session

Performing a full maintenance pass on the active knowledge base.

```bash
# 1. Audit graph health
kb graph lint --json

# 2. Clean citations and stubs
kb doc clean --apply

# 3. Deduplicate domain entities
kb graph dedupe --apply

# 4. Verify code snippets
kb code check --all

# 5. Final audit confirmation
kb graph lint
```
