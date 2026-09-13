---
name: ingest-document
description: Add a new raw document to the knowledge base and initialize its record. TRIGGER when the user provides a file path or content to be added to the KB.
---

## When to use

Use this skill whenever you receive a new source of information, such as a PDF research paper, a text file with notes, or a Markdown technical specification. This is the entry point for all knowledge.

Trigger this skill when:
*   The user uploads a file or provides a local file path.
*   The user pastes raw text they want to "save" or "remember".
*   You have finished `save-html-as-digestible` and have a clean Markdown file.

## Steps

1.  **Check Format**: Ensure the file is a `.pdf`, `.txt`, or `.md`. These are the only natively supported formats. If it is HTML, redirect to `save-html-as-digestible`.
2.  **Extract/Determine Title**: BEFORE running `kb doc add`, inspect the file content, header, or source metadata to extract the full, human-readable document or paper title. Do NOT rely on the filename stem alone, as source files frequently have uninformative, obscure, or hash-like names (e.g., `1-s2.0-S0921889025000156-main.pdf`, `s41598-025-96066-8.pdf`, `2505.00200v2.pdf`).
3.  **Add Document**: Run `kb doc add <file> --kind raw --title "<Full Human-Readable Title>"` with flags:
    *   `--title T`: **Mandatory for research papers and documents with non-descriptive filenames.** Full, human-readable title.
    *   `--url U`: Source URL if downloaded from the web. **If `--url` contains a DOI (e.g. `https://doi.org/10.1109/...` or `doi:10.xxxx/...`) or an arXiv link/identifier (e.g. `https://arxiv.org/abs/2305.12345`), the CLI automatically maps and extracts the DOI (mapping arXiv papers to their official DataCite DOI `10.48550/arXiv.<id>`).**
    *   `--doi D`: **Digital Object Identifier (DOI)** (e.g. `10.1109/TRO.2023.123456` or `arXiv:2305.12345`). Optional if already present in `--url`; only needed when the URL does not contain the DOI or when ingesting a local file directly.
    *   `--no-doi` / `--allow-no-doi`: Explicit override allowing ingestion of raw documents without a DOI (for unpublished, internal, or offline documents where remote re-retrieval is not needed).
    *   `--tag T`: Useful keywords for grouping (repeatable).
    *   `--notes N`: Any specific context about the acquisition of the document.
4.  **Capture ID**: The CLI will return a unique document ID (e.g., `raw-0001`). You must record this ID; it is the primary key for all graph entities derived from this document.
5.  **Verify Content & Metadata**: Run `kb doc text <id>` to ensure the text was extracted correctly. If the text is empty or garbled, the ingest failed. Run `kb doc show <id>` to check metadata. Verify that `manifest.json` holds the clean, human-readable title rather than a filename hash.
6.  **Graph Node Creation & Automatic Stub Reconciliation**: If the graph database exists, `kb doc add` automatically upserts the corresponding `Document` node in the Traverse graph database using the title provided. If a placeholder stub (`kind: "stub"`) already existed for this paper from earlier citations, `kb doc add` automatically reconciles the stub and redirects all existing `CITES` edges to the new document ID.
7.  **Hand off**: Proceed to `deep-knowledge-extraction` to extract domain entities and record bibliography citations using `kb doc cite`.

## Rules

*   **Mandatory Human-Readable Title**: Never omit `--title` when ingesting documents unless the input filename is already verified to be a clean, human-readable title. Do not allow default fallback to uninformative filename stems (such as publisher PII codes, DOIs, arXiv numbers, or hashes). Both `manifest.json` and the `Document` graph node `name` property must hold the full paper title.
*   **Traceability & DOI Requirement**: Raw documents require a DOI by default to ensure remote re-retrieval and traceability. The `--doi` CLI argument is optional if a DOI or arXiv URL/ID is provided via `--url` or `--doi` (from which it is automatically extracted or mapped to `10.48550/arXiv.<id>`). Pass `--no-doi` only when ingesting documents that legitimately do not possess a DOI (e.g., unpublished tech notes, whitepapers, internal documentation).
*   **Title Synchronization**: If an ingestion previously occurred with an uninformative or placeholder title, update the `manifest.json` record and the `Document` graph node `name` property, then rebuild search indexes using `kb index build`.
*   **Immutable Raw Data**: Raw document source files are immutable once ingested. Do not attempt to modify their extracted text after the initial `kb doc add`.
*   **Mandatory ID**: Never proceed to extraction without capturing the document ID returned by `kb doc add`.
*   **Supported Formats Only**: The CLI only supports PDF, TXT, and Markdown. Do not try to ingest other binary formats like `.docx` or `.xlsx` without conversion.
*   **Provenance**: The `sources` list for the `Document` node must contain its own ID. This is the root of the provenance chain.
*   **No LLM in CLI**: The ingest process is purely deterministic text extraction.

## Example

Ingesting a new paper on factor graphs.

```bash
# Add the PDF with an explicit DOI (or let it auto-extract if passing a DOI URL via --url)
kb doc add papers/gtsam_manual.pdf --kind raw --title "GTSAM Manual" --doi "10.1109/TRO.2023.123456" --tag "factor-graphs" --tag "slam"

# If the URL already contains the DOI, --doi can be omitted:
# kb doc add papers/gtsam_manual.pdf --kind raw --title "GTSAM Manual" --url "https://doi.org/10.1109/TRO.2023.123456"

# Or if the document has no DOI (internal/unpublished):
# kb doc add papers/internal_notes.md --kind raw --title "Internal Notes" --no-doi

# Result: Successfully added document raw-0001

# Verify the text was extracted
kb doc text raw-0001 | head -n 20

# Optional: 'kb doc add' automatically creates the Document node in the graph;
# 'kb graph upsert-node Document' can be used to enrich additional metadata if needed:
kb graph upsert-node Document --props '{
  "id": "raw-0001",
  "name": "GTSAM Manual",
  "origin": "raw",
  "sources": ["raw-0001"],
  "path": "papers/gtsam_manual.pdf",
  "format": "pdf",
  "year": 2024
}'

# Check the node status
kb doc show raw-0001
```
