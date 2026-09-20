This document lists concepts that are considered out of scope for the yagrag project. It acts as a permanent record of things the package deliberately will not do, for various reasons.

## 1. Built-in Interactive HTML Graph Visualizer (`kb graph view`)
* **Decision**: Out of scope.
* **Rationale**: Creating and maintaining a rich, interactive in-browser graph visualization tool (custom D3/vis.js canvas, search, physics simulation, clustering layout) requires substantial frontend overhead and maintenance. High-quality interactive exploration and querying are already provided out of the box by the embedded database tooling (TrueSpar Traverse Studio UI via `traverse-server --data <kb-dir>`). Duplicating this in `yagrag` provides low marginal utility relative to implementation complexity.
* **Reference**: See Candidate 1 in [`docs/comparison_yagrag_kggen.md`](docs/comparison_yagrag_kggen.md).

## 2. Dialogue, Chat, and Transcript Ingestion (`ingest-dialogue`)
* **Decision**: Out of scope for current core goals.
* **Rationale**: `yagrag`'s primary architecture is optimized for technical, scientific, and mathematical literature with formal structures (equations, algorithms, models, assumptions, citations). Processing informal conversational turns (Slack/Discord threads, meeting transcripts, LLM dialogues) involves different noise profiles and extraction semantics.
* **Note**: Ingestion of structured external artifacts related to technical literature—specifically public source code repositories—is considered separately in [`IDEAS.md`](IDEAS.md).
* **Reference**: See Candidate 4 in [`docs/comparison_yagrag_kggen.md`](docs/comparison_yagrag_kggen.md).


