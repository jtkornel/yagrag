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
2.  **Add Document**: Run `kb doc add <file> --kind raw` with optional flags:
    *   `--title T`: A human-readable title.
    *   `--tag T`: Useful keywords for grouping (repeatable).
    *   `--notes N`: Any specific context about the acquisition of the document.
    *   `--url U`: Source URL if downloaded from the web.
3.  **Capture ID**: The CLI will return a unique document ID (e.g., `raw-0001`). You must record this ID; it is the primary key for all graph entities derived from this document.
4.  **Verify Content**: Run `kb doc text <id>` to ensure the text was extracted correctly. If the text is empty or garbled, the ingest failed. Run `kb doc show <id>` to check metadata.
5.  **Graph Node Creation**: If the graph database exists, `kb doc add` automatically upserts the corresponding `Document` node in Kuzu DB. You can also explicitly verify or update properties via `kb graph upsert-node Document --props '...'`.
6.  **Check for Duplicates**: Run `kb doc list` to see if a document with the same title or path already exists to avoid redundant extractions.
7.  **Hand off**: Proceed to `deep-knowledge-extraction` to pull out the domain entities.

## Rules

*   **Immutable Raw Data**: Raw documents are immutable once ingested. Do not attempt to modify their text or metadata after the initial `kb doc add`.
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
