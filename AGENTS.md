# Agent Guidelines for Software Development

Lightweight instructions for agents and developers modifying code in this repository.

## Python Environment

The repository provides a `uv.lock` file and uses a local virtual environment located at `.venv/`.

### Activating Existing Environment
If `.venv` is present:
```bash
source .venv/bin/activate
```
Always use `.venv` or run tools via `.venv/bin/python`, `.venv/bin/pytest`, and `.venv/bin/ruff`.

### Setting Up Environment with `uv`
If `.venv` is not present or needs to be recreated, the recommended approach is to use [`uv`](https://github.com/astral-sh/uv):

1. **Bootstrap Script (Recommended)**:
   The repository includes an idempotent bootstrap script that installs `uv` (if missing), creates `.venv`, installs all optional extras in editable mode, and builds needed native binaries (such as `traverse-server`):
   ```bash
   ./scripts/bootstrap.sh
   # Or force recreate if the venv is stale:
   ./scripts/bootstrap.sh --recreate
   ```

2. **Direct `uv sync`**:
   Because `uv.lock` is tracked in the repository, you can sync the exact locked dependencies:
   ```bash
   uv sync --all-extras
   ```
   Or create the virtual environment and install in editable mode:
   ```bash
   uv venv
   source .venv/bin/activate
   uv sync --all-extras
   ```

Always ensure the environment is activated before running tests, linter, or CLI commands.

## Quality Gating & Pre-Completion Verification

Before claiming any feature or bug fix as complete:

1. **Run the quality check script**:
   ```bash
   source .venv/bin/activate
   ./scripts/check.sh
   ```
   This script runs:
   - `ruff check .` (linter checks)
   - `pytest -W error` (test suite with warning escalation)

2. **Targeted testing during iteration**:
   ```bash
   .venv/bin/pytest tests/test_<relevant>.py
   ```

3. **Check for lint errors early and often**:
   ```bash
   .venv/bin/ruff check .
   ```
   - Avoid catching broad `Exception` without justification; catch specific exceptions (e.g. `_json.JSONDecodeError`, `OSError`) or annotate intentional fallbacks with `# noqa: BLE001`.
   - Never leave silent `except: pass` blocks without logging or justification.

4. **Never declare completion with failing checks**:
   `task_complete` must only be invoked after all tests and linter checks pass cleanly.

## Key Principles & Conventions

- **Deterministic CLI (`kb`)**: The CLI contains **zero LLM calls**. All AI reasoning belongs in `.agents/skills/`.
- **GQL Schema Evolution**: Forward-only numbered `.gql` migrations in `schema/migrations/`. Keep `schema/schema_companion.json` in sync with canonical directions and domain/range rules.
- **Mandatory Provenance**: Every entity and relationship created or modified requires `origin` (`raw`, `synthesized`, `inferred`, or `mardi`) and non-empty `sources`.
- **No Floating Nodes**: Extracted nodes must be connected via valid domain edges. Run `kb graph lint` to verify graph consistency.
