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
    *   `--tag T`: Useful keywords for grouping (repeatable).
    *   `--notes N`: Any specific context about the acquisition of the document.
    *   `--url U`: Source URL if downloaded from the web.
4.  **Capture ID**: The CLI will return a unique document ID (e.g., `raw-0001`). You must record this ID; it is the primary key for all graph entities derived from this document.
5.  **Verify Content & Metadata**: Run `kb doc text <id>` to ensure the text was extracted correctly. If the text is empty or garbled, the ingest failed. Run `kb doc show <id>` to check metadata. Verify that `manifest.json` holds the clean, human-readable title rather than a filename hash.
6.  **Graph Node Creation & Automatic Stub Reconciliation**: If the graph database exists, `kb doc add` automatically upserts the corresponding `Document` node in Kuzu DB using the title provided. If a placeholder stub (`kind: "stub"`) already existed for this paper from earlier citations, `kb doc add` automatically reconciles the stub and redirects all existing `CITES` edges to the new document ID.
7.  **Hand off**: Proceed to `deep-knowledge-extraction` to extract domain entities and record bibliography citations using `kb doc cite`.

## Rules

*   **Mandatory Human-Readable Title**: Never omit `--title` when ingesting documents unless the input filename is already verified to be a clean, human-readable title. Do not allow default fallback to uninformative filename stems (such as publisher PII codes, DOIs, arXiv numbers, or hashes). Both `manifest.json` and the `Document` graph node `name` property must hold the full paper title.
*   **Title Synchronization**: If an ingestion previously occurred with an uninformative or placeholder title, update the `manifest.json` record and the `Document` graph node `name` property, then rebuild search indexes using `kb index build`.
*   **Immutable Raw Data**: Raw document source files are immutable once ingested. Do not attempt to modify their extracted text after the initial `kb doc add`.
*   **Mandatory ID**: Never proceed to extraction without capturing the document ID returned by `kb doc add`.
*   **Supported Formats Only**: The CLI only supports PDF, TXT, and Markdown. Do not try to ingest other binary formats like `.docx` or `.xlsx` without conversion.
*   **Provenance**: The `sources` list for the `Document` node must contain its own ID. This is the root of the provenance chain.
*   **No LLM in CLI**: The ingest process is purely deterministic text extraction.

## Example

Ingesting a new paper on factor graphs.

```bash
# Add the PDF to the document store
kb doc add papers/gtsam_manual.pdf --kind raw --title "GTSAM Manual" --tag "factor-graphs" --tag "slam"

# Result: Successfully added document raw-0001

# Verify the text was extracted
kb doc text raw-0001 | head -n 20

# Create the node in the graph
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
