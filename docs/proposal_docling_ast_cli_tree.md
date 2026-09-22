# Proposal: Exposing the Docling Document Tree in the CLI (`kb doc tree`)

## 1. Executive Summary & Motivation

With the adoption of Docling as the core document extraction engine, `yagrag` generates and caches a lossless structural document AST (`documents/cache/<doc_id>/document.json` conforming to `DoclingDocument`) alongside the linear Markdown (`content.md`) and cropped figures (`figures/`).

Currently, the CLI exposes:
- **Linear text / Markdown**: `kb doc text <id>`
- **Cropped visual assets**: `kb doc figures <id>`

### The Problem for LLM / VLM Agents
When an agent analyzes technical literature (e.g. state estimation, robotics, optimization, mechanical design):
1. **Context Window Waste & Noise**: Full Markdown documents can span 10,000–30,000+ tokens. To locate a specific benchmark table, convergence proof, or mathematical formulation, the agent must ingest the entire document stream into its context window.
2. **Ambiguity in Markdown Representation**:
   - Complex multi-level tables (e.g., benchmark comparison matrices with multi-row headers and sub-metrics) are notoriously difficult for LLMs to parse reliably when flattened into linear ASCII Markdown pipes.
   - Equations embedded in Markdown text lack explicit layout metadata (e.g., page provenance, equation labels/numbers like `(12)`, or distinction between inline vs display math).
3. **No Query Language Reinvention**: We do **not** want to invent a custom AST query language, custom parser, or complex query DSL. Instead, we want to expose Docling's native structural hierarchy (`doc.tables`, `FormulaItem`, `doc.export_to_element_tree()`, and JSON Pointer `cref` references) via simple, composable, high-leverage CLI commands.

---

## 2. Docling AST Architecture & Native Primitives

Docling represents parsed documents via `docling_core.types.doc.DoclingDocument`. Key primitives already available in the cached AST:

1. **Item References (`cref`)**: Every node in the AST has a unique, deterministic JSON Pointer reference:
   - `#/tables/0`, `#/tables/1`, ...
   - `#/texts/14` (e.g. a `FormulaItem` or `SectionHeaderItem`)
   - `#/pictures/0`
2. **Tables (`TableItem`)**:
   - `table.export_to_markdown(doc)`: Standalone clean Markdown table representation.
   - `table.export_to_html(doc)`: Clean HTML table structure (preserves colspans/rowspans).
   - `table.data.grid`: Structured cell array with row/col spans and cell text.
   - `table.caption_text(doc)`: Associated table caption.
   - `table.prov`: Page number and bounding box coordinates.
3. **Formulas / Equations (`FormulaItem`)**:
   - `formula.text`: Normalized LaTeX formula string (e.g. `\mathbf{x}_{k} = \mathbf{f}(\mathbf{x}_{k-1}, \mathbf{u}_k) + \mathbf{w}_k`).
   - `formula.orig`: Original extracted text/glyphs.
   - `formula.prov`: Page number and bounding box coordinates.
4. **Section / Outline Tree**:
   - Document items are hierarchically linked via `parent` and `children` `RefItem` pointers, forming an outline tree of sections (`SectionHeaderItem`), paragraphs, tables, and figures.

Because `CacheManager` already writes `document.json` upon first access, all this rich structural data is already cached on disk—zero additional ML inference is needed to query it!

---

## 3. Proposed CLI Design: High-Value, Zero-Complexity Subcommands

Rather than adding an ad-hoc query engine, we propose adding targeted subcommands under `kb doc` that expose the native Docling items cleanly:

### 3.1 `kb doc tables <doc_id>`: Structured Table Inspection

Allows agents to quickly discover and inspect tables without loading the entire document markdown.

#### Synopsis
```bash
kb doc tables <doc_id> [OPTIONS]
```

#### Options
- `--index <N>` / `-i <N>`: Inspect a single table by zero-based index. If omitted, lists all tables with captions and dimensions.
- `--format [md|html|json]`: Output format for the table content (default: `md`).
  - `md`: Markdown pipe table.
  - `html`: HTML `<table>` (best for complex merged cells/headers).
  - `json`: Structured rows/columns matrix.
- `--json`: Emit full JSON metadata (including page, caption, bbox, dimensions).

#### Agent Workflow Example
```bash
# 1. Discover tables in paper
$ kb doc tables raw-0001
Table #0 [Page 4] (Rows: 6, Cols: 5)
  Caption: Table 1: Trajectory estimation RMSE (m) on EuRoC MAV dataset.
Table #1 [Page 7] (Rows: 4, Cols: 4)
  Caption: Table 2: Runtime and memory breakdown per factor graph optimization iteration.

# 2. Agent fetches only Table 1 directly into context
$ kb doc tables raw-0001 --index 0
| Sequence | VINS-Mono | ORB-SLAM3 | Proposed (Ours) |
|----------|-----------|-----------|-----------------|
| MH_01    | 0.12      | 0.08      | 0.06            |
| MH_02    | 0.15      | 0.10      | 0.07            |
| V1_01    | 0.09      | 0.07      | 0.05            |
```

---

### 3.2 `kb doc equations <doc_id>`: Isolated Mathematical Formulations

Technical papers contain dozens of inline math symbols, but usually only a handful of key display formulas defining systems, kinematics, or loss functions.

#### Synopsis
```bash
kb doc equations <doc_id> [OPTIONS]
```

#### Options
- `--index <N>` / `-i <N>`: View a specific equation by index.
- `--page <N>` / `-p <N>`: Filter equations appearing on a specific page.
- `--json`: Emit JSON array of equations with page provenance and LaTeX strings.

#### Agent Workflow Example
```bash
# 1. List key display formulas
$ kb doc equations raw-0001
Eq #0 [Page 3]: \mathbf{x}_{k} = \mathbf{f}(\mathbf{x}_{k-1}, \mathbf{u}_k) + \mathbf{w}_k
Eq #1 [Page 3]: \mathbf{z}_{k} = \mathbf{h}(\mathbf{x}_k) + \mathbf{v}_k
Eq #2 [Page 5]: \min_{\mathcal{X}} \sum_{k} \|\mathbf{r}_k(\mathbf{x}_k, \mathbf{z}_k)\|_{\mathbf{\Sigma}_k}^2

# 2. Read specific equation in clean LaTeX for grounding an `Equation` node in the graph:
$ kb doc equations raw-0001 -i 2
\min_{\mathcal{X}} \sum_{k} \|\mathbf{r}_k(\mathbf{x}_k, \mathbf{z}_k)\|_{\mathbf{\Sigma}_k}^2
```

---

### 3.3 `kb doc outline <doc_id>`: Hierarchical Outline & Pointer Discovery

Gives the agent an instant high-level bird's-eye view of the paper structure (sections, headings, and which sections contain figures, tables, and equations). Crucially, the outline directly annotates each node with its native JSON Pointer (`cref`), making pointer discovery immediate and unambiguous.

#### Synopsis
```bash
kb doc outline <doc_id> [OPTIONS]
```

#### Options
- `--depth <N>` / `-d <N>`: Maximum section nesting depth to display (default: all levels).
- `--items` / `-i`: Include leaf items (equations, tables, pictures) under each section header with their `cref` pointers.
- `--json`: Emit machine-readable tree with `cref`, section title, page number, and child pointers.

#### Agent Workflow Example (CLI Text Display)
```bash
$ kb doc outline raw-0001 --items
1. Introduction [p.1] (#/texts/0)
2. Related Work [p.2] (#/texts/5)
3. Methodology [p.3] (#/texts/12)
   3.1 State Representation [p.3] (#/texts/15)
       • formula: \mathbf{x}_{k} = \mathbf{f}(\mathbf{x}_{k-1}) (#/texts/18)
       • formula: \mathbf{z}_{k} = \mathbf{h}(\mathbf{x}_k) (#/texts/21)
   3.2 IMU Preintegration [p.4] (#/texts/25)
       • formula: \Delta \mathbf{R}_{ij} = \prod \dots (#/texts/29)
       • table: Runtime breakdown (#/tables/0)
   3.3 Visual Factor Formulation [p.4] (#/texts/34)
       • picture: Factor graph diagram (#/pictures/0)
4. Experimental Evaluation [p.6] (#/texts/45)
   4.1 EuRoC MAV Benchmark [p.6] (#/texts/48)
       • table: Trajectory RMSE (m) (#/tables/1)
       • picture: Trajectory error plots (#/pictures/1)
5. Conclusion [p.8] (#/texts/60)
```

#### Machine-Readable Output (`--json`)
In `--json` mode, `kb doc outline` emits a clean hierarchical JSON structure that maps the document tree directly to JSON Pointer targets:
```json
{
  "id": "raw-0001",
  "outline": [
    {
      "title": "3. Methodology",
      "level": 1,
      "cref": "#/texts/12",
      "page": 3,
      "children": [
        {
          "title": "3.1 State Representation",
          "level": 2,
          "cref": "#/texts/15",
          "page": 3,
          "items": [
            {"label": "formula", "cref": "#/texts/18", "page": 3, "summary": "\\mathbf{x}_{k} = \\mathbf{f}(\\mathbf{x}_{k-1})"},
            {"label": "formula", "cref": "#/texts/21", "page": 3, "summary": "\\mathbf{z}_{k} = \\mathbf{h}(\\mathbf{x}_k)"}
          ]
        },
        {
          "title": "3.2 IMU Preintegration",
          "level": 2,
          "cref": "#/texts/25",
          "page": 4,
          "items": [
            {"label": "table", "cref": "#/tables/0", "page": 4, "summary": "Runtime breakdown"}
          ]
        }
      ]
    }
  ]
}
```

---

### 3.4 Direct Item Fetch by JSON Pointer (`kb doc item`)

Once the agent discovers the pointer in the outline (e.g. `#/tables/0` or `#/texts/18`), it can query that precise AST node directly without loading the rest of the document.

#### Synopsis
```bash
kb doc item <doc_id> <cref> [OPTIONS]
```

#### Options
- `<cref>`: RFC 6901 JSON pointer (e.g. `#/tables/0`, `#/texts/18`, `#/pictures/0`, or `/tables/0`).
- `--format [text|md|html|json]`: Rendering mode (default: `text` or `md` depending on item type).
  - For `FormulaItem`: Prints clean LaTeX formula text.
  - For `TableItem`: Prints formatted Markdown pipe table (or HTML/JSON if requested).
  - For `PictureItem`: Prints figure caption, page, bbox, and local path to cropped PNG.
  - For `SectionHeaderItem` / `ParagraphItem`: Prints plain text and child item list.
  - `--json`: Emits the raw Docling AST node dictionary with all metadata (provenance, bounding box, content layer).

#### Agent Workflow Example
```bash
# Agent discovers table pointer in outline: #/tables/1
$ kb doc item raw-0001 "#/tables/1" --format md
| Sequence | VINS-Mono | ORB-SLAM3 | Proposed (Ours) |
|----------|-----------|-----------|-----------------|
| MH_01    | 0.12      | 0.08      | 0.06            |
| MH_02    | 0.15      | 0.10      | 0.07            |

# Agent discovers formula pointer in outline: #/texts/18
$ kb doc item raw-0001 "#/texts/18"
\mathbf{x}_{k} = \mathbf{f}(\mathbf{x}_{k-1}, \mathbf{u}_k) + \mathbf{w}_k
```

This establishes a clean, two-step discovery & fetch loop:
1. `kb doc outline <doc_id> [--items]` $\rightarrow$ Agent scans high-level hierarchy and identifies candidate `cref` pointers.
2. `kb doc item <doc_id> <cref>` $\rightarrow$ Agent fetches the targeted AST node in isolation.

---

## 4. Architectural Implementation Details

### 4.1 Loading from Cache
When running `kb doc tables`, `kb doc equations`, or `kb doc outline`:
1. Check if `cache.is_valid(doc_id, rec.hash)`. If not, call `store.parse_document(doc_id)` to generate the cache transparently (exactly like `kb doc text` and `kb doc figures`).
2. Read `documents/cache/<doc_id>/document.json` via `store.cache.read_document_json(doc_id)`.
3. If `document.json` is present:
   - For fast traversal, deserialize using `DoclingDocument.model_validate_json(raw_json_str)` or parse the native dict.
   - For `tables`: iterate `doc.tables`.
   - For `equations`: iterate `doc.iterate_items()`, collecting `FormulaItem` instances (or checking `item.label == DocItemLabel.FORMULA`).
   - For `outline`: iterate `SectionHeaderItem` nodes and their child counts.

### 4.2 Universal AST Generation Across All Formats (Markdown, Plain Text, and PDF)
Docling natively parses Markdown (`.md`) and plain text (`.txt`) as first-class input formats without needing computer-vision pipeline models:
- Markdown and text conversion executes in milliseconds (pure text/syntax parsing without OCR or ML layout models).
- Passing `.md` and `.txt` through Docling produces the exact same unified `DoclingDocument` AST with consistent `#/texts/...` and `#/tables/...` JSON pointer addresses and section hierarchies.
- By parsing all ingested documents (`pdf`, `md`, `txt`) through Docling into `document.json`:
  1. The AST query interface (`kb doc outline`, `kb doc tables`, `kb doc equations`, `kb doc item`) becomes 100% universal across all documents in the knowledge base, whether raw PDF preprints or synthesized Markdown analyses.
  2. Synthesized documents created by agents also get structured outlines, table extraction, and equation isolation for free.
  3. No format branching or fallback logic is required anywhere in the document store or CLI commands.

---

## 5. Benefits & Comparison

| Approach | Developer / System Complexity | Agent Token Consumption | Fidelity for Tables & Math |
|---|---|---|---|
| **Status Quo (`kb doc text`)** | Zero extra CLI commands | High (agent must read entire 20-page Markdown) | Moderate (markdown pipes, inline equations mixed with text) |
| **Custom Query Language (e.g. JSONPath/XPath DSL)** | High (must document, parse, sanitize, and test custom DSL) | Moderate | Depends on query skill of the agent |
| **Native Item CLI (`kb doc tables`, `equations`, `outline`)** | **Very Low** (standard Typer CLI commands wrapping existing `DoclingDocument` fields) | **Minimal** (agent queries outline, fetches only the specific table/equation needed) | **Maximum** (native LaTeX, HTML/MD tables, exact page/bbox provenance) |

---

## 6. Integration with Agent Skills

Update `.agents/skills/deep-knowledge-extraction/SKILL.md`:
1. Instruct the extraction agent to run `kb doc outline <doc_id>` first to assess paper structure.
2. Direct the agent to inspect specific tables (`kb doc tables <doc_id> -i <idx>`) when creating `Benchmark`, `Dataset`, or `Metric` nodes.
3. Direct the agent to inspect specific equations (`kb doc equations <doc_id> -i <idx>`) when creating `Equation` or `Formulation` nodes, copying the pristine LaTeX directly into `Equation.latex`.
