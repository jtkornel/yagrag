---
name: code-representation
description: Write and check statically checkable code for equations, algorithms, and models. TRIGGER when creating or updating statically checkable nodes to ensure formal consistency.
---

## When to use

Use this skill whenever you create or update a node of a **statically checkable type** — any node type whose schema declares the code property set. This is a property of the knowledge base's schema, not a fixed vocabulary: in a robotics KB it may be `Equation`, `Algorithm`, `Method`, `Factor`, `MotionModel`, `SensorModel`, `NoiseModel`; in a wave-mechanics or bird-flight KB it will be whatever types that domain declared. Never assume the list — read it from the live schema (see step 0). The skill is also useful when `kb code list` identifies entries as `failed`, `stale`, or `unchecked`, requiring you to fix or verify the associated code snippets.

Trigger this skill when:
*   You are extracting a mathematical relationship that can be expressed canonically.
*   You are capturing a reference implementation for an algorithm, method, procedure or model.
*   `kb code list` shows nodes with `missing` files or `stale` hashes.
*   You need to verify the symbol consistency of an expression against the graph.

## Terminology

*   **Statically checkable type** — a node type whose schema declares the seven `code_*` properties, so its nodes may point at a snippet that `kb code check` verifies by reading it: never executed, never sent to an LLM. This is the umbrella term and covers both languages — a SymPy expression is as much "code" here as a Python function.
*   **Symbol-bearing type** — a node type whose schema declares a `symbol` property. It is not checked itself; it supplies the symbol vocabulary that SymPy snippets are cross-checked against.

Use only these two terms; do not fall back on "code-bearing" or "machine-checkable".

## The schema contract

A node type can hold statically checked code **only if** its schema declares all seven properties: `code_language`, `code_path`, `code_entry`, `code_status`, `code_checked_at`, `code_checker`, `code_hash`. The first three you set; the last four the checker writes back. Symbol consistency draws on every node type that declares a `symbol` property (its `symbol`, `id` and `name` values all count).

If the type you want to attach code to is not statically checkable, that is a **schema gap** — hand off to the `schema-evolution` skill to add the property set; do not work around it by attaching the snippet to an unrelated type.

## Steps

0.  **Read the Contract**: Run `kb code list --json` (an empty result is fine) or `kb schema show` to confirm the target type is statically checkable in *this* knowledge base. `kb code check --label <Label>` fails with the list of statically checkable labels if the type is not one of them.
1.  **Choose the Language**: Determine the appropriate representation. Use `sympy` for mathematical relations, closed-form expressions, and residuals. Use `python` for procedures involving control flow, iteration, or complex matrix operations.
2.  **Write the Snippet**: Create a file under the `code/` directory of the knowledge base.
    *   For **SymPy**: Use a `.sympy` extension. Write one relation per line (e.g., `Eq(y, x + 1)`). Reuse symbols already present on symbol-bearing nodes in the graph.
    *   For **Python**: Use a `.py` extension. Target Python 3.11 + NumPy. Define a module-level function with type annotations.
    *   Conventional layout: `code/equations/*.sympy`, `code/algorithms/*.py`, `code/models/*.py`, with a file slug matching the node id.
3.  **Set the Entry Point**: Decide on the `code_entry`. This is the function name for Python snippets or the name of the primary relation for SymPy snippets.
4.  **Upsert the Node**: Run `kb graph upsert-node <Label> --props '...'` including the code properties: `code_language`, `code_path` (relative to KB root), and `code_entry`.
5.  **Check the Code**: Run `kb code check --label <Label> --id <ID>` (add `--lint` for Python) to perform static analysis.
6.  **Verify Status**: Run `kb code list --json` or `kb code show <Label>:<ID>` to read the `code_status` and any warnings or errors recorded.

## Rules

*   **Files-Only Storage**: Snippets must reside in the `code/` tree. Never point `code_path` at a file that does not exist or sits outside the knowledge base directory.
*   **Never Assume the Labels**: Which types are statically checkable comes from the schema, always. Do not rely on a memorised list — it differs per knowledge base and grows with every migration.
*   **LaTeX Parity**: If the type also declares a `latex` property (as `Equation` does in the seed schema), always maintain it alongside the SymPy representation. The SymPy form is for checking; LaTeX is for display.
*   **Symbol Consistency**: In SymPy snippets, prefer symbols that match the `symbol`, `id` or `name` of a symbol-bearing node. An unrecognised symbol is reported as a *warning* only — if the symbol is legitimate, either accept the warning or add the missing symbol-bearing node.
*   **Only Parse Errors Fail**: `failed` means a missing file, a `code_path` outside the KB, or a genuine parse/syntax error. Unknown symbols and lint findings never flip the status; an unrecognised `code_language` yields `unchecked`.
*   **No Side Effects**: Python snippets must not perform I/O, networking, or top-level side effects. They should be pure functions.
*   **Failed Checks are Data**: A `failed` status does not block a write. Record the failure, and either fix the snippet or proceed if the extraction is still valuable.
*   **Refresh on Edit**: If you manually edit a file in the `code/` directory, you must re-run `kb code check` to update the `code_hash` and status, otherwise it will be flagged as `stale`.
*   **No Execution**: The checker only performs static analysis (parsing and symbol resolution). Do not rely on the code being executed.

## Example

The labels below (`Equation`, `Algorithm`) come from the seed schema. In another knowledge base they would be whatever statically checkable types that schema declares; the commands and properties are identical.

Creating a SymPy equation for a range residual.

```bash
# 1. Write the SymPy expression to a file
# Content: Eq(residual, norm(p_robot - p_landmark) - range_meas)
cat > code/equations/range_res.sympy <<EOF
Eq(residual, sqrt((x_r - x_l)**2 + (y_r - y_l)**2) - rho)
EOF

# 2. Upsert the Equation node with code properties
kb graph upsert-node Equation --props '{
  "id": "eq_range_res",
  "name": "2D Range Residual",
  "latex": "\\rho - \\sqrt{(x_r - x_l)^2 + (y_r - y_l)^2}",
  "code_language": "sympy",
  "code_path": "code/equations/range_res.sympy",
  "code_entry": "residual",
  "origin": "raw",
  "sources": ["raw-0001"]
}'

# 3. Check the snippet
kb code check --label Equation --id eq_range_res --json

# 4. List code status to verify
kb code list --label Equation
```

Creating a Python implementation for an algorithm.

```bash
# 1. Write the Python function
cat > code/algorithms/mean_filter.py <<EOF
import numpy as np

def compute_mean(data: np.ndarray) -> float:
    """Compute the arithmetic mean of a 1D array."""
    return float(np.mean(data))
EOF

# 2. Upsert the Algorithm node
kb graph upsert-node Algorithm --props '{
  "id": "alg_mean_filter",
  "name": "Simple Mean Filter",
  "code_language": "python",
  "code_path": "code/algorithms/mean_filter.py",
  "code_entry": "compute_mean",
  "origin": "raw",
  "sources": ["raw-0001"]
}'

# 3. Check with linting
kb code check --label Algorithm --id alg_mean_filter --lint
```
