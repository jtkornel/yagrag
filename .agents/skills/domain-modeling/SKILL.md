---
name: domain-modeling
description: Interview the user to define node and relation types for a new domain and verify against the existing schema. TRIGGER when the user wants to start a new knowledge base or expand into a new area of expertise.
---

## When to use

Use this skill at the very beginning of a project or when a new sub-domain is introduced. You need to understand the "physics" of the domain—what the core entities are and how they interact—rather than just how documents are organized. For example, in sensor fusion, you care about `Variable`, `Factor`, and `NoiseModel`, not just `Paper` and `Author`. 

Trigger this skill when:
*   The user asks to "start a new knowledge base".
*   You encounter a new technical area (e.g., "world models") not covered by the existing node types.
*   The user wants to refine the granularity of their knowledge graph.

## Steps

1.  **Interview the User**: Ask about the core entities (nodes) and their relationships (edges). Focus on the technical internals of the field. What questions should the graph answer? What are the key variables, constraints, or methods?
2.  **Inventory Existing Schema**: Run `kb schema show` to see what node and relation types are already available in the seed schema. This prevents reinventing the wheel.
3.  **Map Entities**: Map the user's domain concepts to existing types. If a concept like "IMU Preintegration" fits under `Factor`, use that. If a specific "Pose" state is discussed, map it to `Variable`.
4.  **Identify Gaps**: If the user needs a type that doesn't exist (e.g., `CoordinateFrame` was already in seed, but maybe `EnvironmentMap` is not), note it down clearly.
5.  **Decide What Must Be Checkable**: Ask which concepts carry formal content — equations, algorithms, numerical procedures, residuals, closed-form relations. Whatever the domain (factor graphs, wave mechanics, bird flight patterns), any node type that should hold statically checked code must declare the static-check property set: `code_language`, `code_path`, `code_entry`, `code_status`, `code_checked_at`, `code_checker`, `code_hash`. Types that name a mathematical symbol should declare `symbol`. Flag these in the proposal.
6.  **Propose Schema**: Present a list of nodes and relations to the user. Explain how they represent the domain. Use a structured format (Markdown table or list) to show proposed node labels, their intended use, and which of them are statically checkable.
7.  **Hand off**: Once the user approves, if new types are needed, use the `schema-evolution` skill to implement them. If the schema is sufficient, proceed to `ingest-document`.

## Rules

*   **Wikidata, not Wikipedia**: Focus on structured domain knowledge (equations, factors, variables) inside documents, not just bibliographic metadata. A summary of a paper is not a model of the domain.
*   **No LLM in CLI**: Remember that the `kb` CLI is deterministic. You are the "brain" that decides what to store; the CLI is the "hand" that stores it. All reasoning happens in the agent.
*   **Minimalism**: Prefer existing node/relation types before proposing new ones — but read the live schema with `kb schema show` rather than assuming a fixed inventory; it grows with every migration.
*   **Provenance Ready**: Ensure every concept you plan to extract can be tied back to an `origin` (raw, synthesized, or inferred) and specific `sources`.
*   **Entity Internals**: Model the internal variables, factors, and methods of the field. Avoid over-focusing on Document-to-Document links (CITES).
*   **Checkability is a Design Choice**: Deciding which types are statically checkable is part of modelling the domain, not an afterthought. It is cheap to declare the property set on a type that may later carry formal content, and expensive to add it afterwards (there is no `ALTER TABLE` yet).

## Example

You are helping a user model a new project on wheel-odometry motion models for skid-drive robots.

```bash
# First, see what we already have
kb schema show

# You identify that we need to represent a specific motion model and its noise parameters.
# You verify that 'MotionModel' and 'NoiseModel' exist in the schema.

# You interview the user:
# Junie: "To model skid-drive odometry, I'll use 'MotionModel' for the kinematics 
# and 'NoiseModel' for the slip parameters. Do we need to capture specific 
# 'Variable' states like x, y, theta?"

# User: "Yes, and we need to track the 'Solver' used for the state estimation."

# You confirm:
# Junie: "Great. I'll use the existing 'MotionModel', 'NoiseModel', 'Variable', 
# and 'Solver' types. I'll link them using 'USES' and 'ESTIMATES' relations."
```
