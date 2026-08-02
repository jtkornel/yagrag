# Agent skills

This directory holds the skills that guide an LLM agent through the
knowledge-base workflow. The skills invoke the deterministic `kb` CLI to
perform actual document-store and graph-database operations.

Each skill is a folder with a `SKILL.md` file, following the open
[Agent Skills](https://agentskills.io) format. They live under `.agents/skills/`
rather than a vendor-specific directory (`.junie/skills/`, `.claude/skills/`,
`.cursor/skills/`, …), which is the cross-client convention: agents that
support the format discover them here without any per-tool configuration.

Core workflow skills:
- [domain-modeling](./domain-modeling/SKILL.md) — interview the user, propose an initial schema.
- [ingest-document](./ingest-document/SKILL.md) — add a raw document via `kb doc add --kind raw`.
- [save-html-as-digestible](./save-html-as-digestible/SKILL.md) — how to save a web page as clean Markdown before ingest.
- [deep-knowledge-extraction](./deep-knowledge-extraction/SKILL.md) — extract domain entities and reified `Claim` facts.
- [schema-evolution](./schema-evolution/SKILL.md) — grow the domain layer via tracked migrations.
- [graph-update](./graph-update/SKILL.md) — reconcile facts, update the graph consistently.
- [document-synthesis](./document-synthesis/SKILL.md) — write synthesized documents with provenance.
- [question-answering](./question-answering/SKILL.md) — retrieve context and answer grounded questions.
- [code-representation](./code-representation/SKILL.md) — store equations as SymPy and algorithms as Python under `code/`, then check them with `kb code check`.
