---
name: schema-evolution
description: Extend the knowledge base schema by adding new node or relation types via migrations. TRIGGER when the current schema cannot express a domain concept or relationship encountered during extraction.
---

## When to use

Use this skill whenever the knowledge base's *current* schema cannot express something you need to record. Never reason from a memorised list of types — the schema grows over time, so always read the live schema with `kb schema show` first and compare it against what the material actually asserts.

Trigger this skill when:
*   No existing node type fits a domain entity you need to store (e.g. a new sub-domain introduces `CalibrationParameters`, or a move into world models introduces `LatentState`).
*   No existing relation type expresses a link you need, or the link you need is between a `from`/`to` pair that the existing relation does not allow (e.g. `SYNCHRONIZED_WITH` between two sensors).
*   An existing type *almost* fits but lacks a property you need to capture (e.g. adding `sample_rate` to `Sensor`).
*   `domain-modeling` identifies a new necessary concept, or `deep-knowledge-extraction` hits a write the CLI rejects because the label, relation, or pair does not exist.
*   The user explicitly asks to "add a new type" or "change the schema".

The schema is meant to be **deeply domain-specific**, whatever the domain is. Do not force new material into generic buckets just to avoid a migration — but do prefer extending a close existing type over creating a near-duplicate one.

## Steps

1.  **Verify Gap**: Run `kb schema show` to confirm the type doesn't already exist and check if there's an existing type that could be extended instead.
2.  **Propose Changes**: Explain the proposed new node or relation types to the user. Show the planned properties and, for relations, the allowed `from` and `to` pairs.
3.  **Scaffold Migration**: Run `kb schema migrate`. This creates a new empty JSON migration file in `schema/migrations/` (e.g., `0002_add_world_model.json`).
4.  **Define Schema**: Edit the generated JSON file. Each migration contains an array of `operations`. Supported operations include:
    *   `create_node_table`: Define a new node type with its properties (name and type).
    *   `create_rel_table`: Define a new relationship type with its allowed pairs.
5.  **Apply Migration**: Run `kb schema apply`. This is an idempotent operation that applies all pending migrations to the database.
6.  **Validate**: Run `kb schema validate` to ensure the physical database state matches the migrations on disk.
7.  **Check Status**: Run `kb schema status` to confirm the new migration is marked as `applied: true`.
8.  **Verify in CLI**: Run `kb schema show` to see your new types in the combined target schema.

### Making a node type statically checkable

Statically checked code is an **opt-in schema capability**, not a built-in list of types. If the user wants to hold statically checkable equations, algorithms or numerical procedures on a node type — in *any* domain, be it factor graphs, wave mechanics, or bird flight patterns — that type must declare the full property set:

```jsonc
{ "op": "create_node_table", "table": { "name": "DispersionRelation", "properties": [
  { "name": "latex",           "type": "STRING" },  // optional: display form
  { "name": "code_language",   "type": "STRING" },  // "python" | "sympy"
  { "name": "code_path",       "type": "STRING" },
  { "name": "code_entry",      "type": "STRING" },
  { "name": "code_status",     "type": "STRING" },  // written by `kb code check`
  { "name": "code_checked_at", "type": "STRING" },  // written by `kb code check`
  { "name": "code_checker",    "type": "STRING" },  // written by `kb code check`
  { "name": "code_hash",       "type": "STRING" }   // written by `kb code check`
] } }
```

All seven `code_*` properties are required — the last four are the slots the checker writes its result into, so a partial declaration is not recognised as statically checkable. In return the type gets `kb code list`, `kb code show` and `kb code check` (syntax gate, symbol consistency, optional lint), with the outcome persisted on the node as data.

Related: a node type that names a mathematical symbol should declare a `symbol` STRING property. Every such type feeds the symbol-consistency check for SymPy snippets, using its `symbol`, `id` and `name` values.

Because there is no `ALTER TABLE` yet, making an *existing* type statically checkable means editing the seed migration and rebuilding — see below.

### Property Evolution and Limitations

The migration model currently supports only `create_node_table`, `create_rel_table`, and raw `cypher`. There is no `add_node_property` or `ALTER TABLE` operation. While knowledge bases are disposable and rebuilt from scratch, adding a property to an existing type requires editing the seed migration `schema/migrations/0001_seed_domain.json` and rebuilding the KB. An `ALTER TABLE` operation will be added once a KB must survive a schema change. Keep the rule that already-applied migrations are never edited—this is the one explicit exception, and it applies only while KBs are disposable.

## Rules

*   **Append-Only**: Migrations are permanent records of the schema's history. Never edit or delete a migration file once it has been applied with `kb schema apply`.
*   **One Concept Per Migration**: Keep migrations granular. If you are adding `IMU` and `Lidar` types, use separate migrations unless they are inextricably linked.
*   **No Data Loss**: Never use operations that drop tables or columns unless the user explicitly requests a destructive migration (rare).
*   **Property Consistency**: New node tables should include the standard fields: `id`, `name`, `summary`, `origin`, `sources`, `confidence`, `created_at`, `updated_at`.
*   **Formal Content is Opt-In**: If a new type is meant to carry equations, algorithms or other statically checkable content, declare the full static-check property set on it at creation time. Half of it is worse than none — the checker ignores a type that does not declare all seven.
*   **Snake Case**: Use `snake_case` for property names and relationship types, and `PascalCase` for node labels.

## Example

Adding a new `WorldModel` node type.

```bash
# 1. Scaffold the migration
kb schema migrate

# 2. You edit the file schema/migrations/0002_add_world_model.json:
# {
#   "name": "add_world_model",
#   "operations": [
#     {
#       "op": "create_node_table",
#       "table": {
#         "name": "WorldModel",
#         "properties": [
#           {"name": "representation", "type": "STRING"},
#           {"name": "static_probability", "type": "FLOAT"}
#         ]
#       }
#     }
#   ]
# }

# 3. Apply the migration
kb schema apply

# 4. Verify
kb schema validate
kb schema status
```
