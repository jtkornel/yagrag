---
name: save-html-as-digestible
description: Convert a web page or HTML content into clean Markdown before ingesting it into the KB. TRIGGER when the user provides a URL or HTML content to be added to the knowledge base.
---

## When to use

Use this skill when you encounter information on the web that needs to be stored. The `kb` CLI does not support native HTML parsing to avoid "noise" like navigation menus, ads, and scripts. You must transform the content into clean, structured Markdown first.

Trigger this skill when:
*   The user provides a URL (e.g., a link to a blog post, documentation page, or arXiv landing page).
*   The user provides raw HTML source code.
*   The user asks to "scrape" or "save" a website.

## Steps

1.  **Extract Content**: Use a tool or method to get the clean text from the HTML.
    *   **Browser Extensions**: Use "Save as Markdown" or Reader Mode extensions to download the page.
    *   **CLI Tools**: Use `pandoc -f html -t markdown <url_or_file> -o output.md`. Ensure you use flags that preserve tables and math if possible.
    *   **Manual**: Copy the relevant content (headings, body text, code blocks) from the browser and paste it into a new `.md` file.
2.  **Clean the Markdown**: Ensure that the resulting file:
    *   Retains all headings (`#`, `##`, etc.) to preserve document structure.
    *   Keeps tables, which often contain critical sensor data or comparison metrics.
    *   Preserves LaTeX equations (e.g., `$x = y$`) for motion and sensor models.
    *   Removes navigation bars, footers, ads, cookie banners, and sidebars.
    *   Includes the original URL at the top for easy reference.
3.  **Prepare for Ingest**: Save the cleaned content to a file with a `.md` extension.
4.  **Ingest**: Use `kb doc add <file> --kind raw --notes "Source URL: <url>"` to add the document.
5.  **Graph Update**: When creating the corresponding `Document` node, ensure the `url` property is populated with the original source URL.

## Rules

*   **No Raw HTML**: Do not attempt to run `kb doc add` on a `.html` file. The CLI will either reject it or extract useless tag soup.
*   **Retain Technical Detail**: In the sensor fusion domain, preserving LaTeX equations and data tables is critical. Do not use "summarization" tools that strip these out.
*   **Url Traceability**: Always record the original URL in the `Document` node properties and the document notes.
*   **Manual Inspection**: Briefly verify that the conversion didn't mangle technical code snippets or mathematical notation.
*   **Plain Markdown**: Avoid using complex Markdown extensions that the `kb` CLI text extractor might not handle (like custom Hugo shortcodes).

## Example

Converting a blog post about Factor Graph Optimization.

```bash
# Assume you used a tool to create 'optimization_post.md' from a URL

# Ingest the cleaned Markdown
kb doc add optimization_post.md --kind raw --title "Factor Graph Optimization Blog" --notes "https://example.com/blog/factor-graphs"

# Result: Successfully added document raw-0001

# Now create the graph node with the URL
kb graph upsert-node Document --props '{
  "id": "raw-0001",
  "name": "Factor Graph Optimization Blog",
  "origin": "raw",
  "sources": ["raw-0001"],
  "path": "optimization_post.md",
  "format": "md",
  "url": "https://example.com/blog/factor-graphs"
}'
```
