#!/usr/bin/env bash
# Quality gating script: linter and test suite.
#
# Usage:
#   ./scripts/check.sh          # Run codebase quality checks (ruff + pytest)
#   ./scripts/check.sh <kb-dir> # Optionally verify domain nodes in a knowledge base
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO_ROOT"

echo "==> [1/2] Running linter (ruff)..."
ruff check .

echo "==> [2/2] Running test suite (pytest -W error)..."
pytest -W error

if [ $# -ge 1 ] && [ -n "$1" ]; then
    TARGET_KB="$1"
    if [ -f "$TARGET_KB/kb.toml" ]; then
        echo "==> Optional: checking statically checkable graph nodes in $TARGET_KB..."
        python3 -m kb.cli.main code check --all --kb "$TARGET_KB"
    else
        echo "Warning: $TARGET_KB is not a valid knowledge base (missing kb.toml)" >&2
    fi
fi

echo "==> Codebase quality checks passed cleanly!"
