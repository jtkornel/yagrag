#!/usr/bin/env bash
# Bootstrap a development environment for graphrag_knowledge_base.
#
# Idempotent: safe to re-run. Installs `uv` if missing, creates `.venv` with a
# supported Python, and installs the project with all optional extras.
#
# Usage:  ./scripts/bootstrap.sh [--recreate]
set -euo pipefail

PYTHON_VERSION="3.11"
REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
VENV="$REPO_ROOT/.venv"
RECREATE=0

for arg in "$@"; do
    case "$arg" in
        --recreate) RECREATE=1 ;;
        *) echo "unknown argument: $arg" >&2; exit 2 ;;
    esac
done

# --- 1. uv ------------------------------------------------------------------
# A stale PATH entry from a previous host can shadow a working interpreter, so
# resolve uv explicitly rather than relying on the ambient environment.
if ! command -v uv >/dev/null 2>&1; then
    if [ -x "$HOME/.local/bin/uv" ]; then
        export PATH="$HOME/.local/bin:$PATH"
    else
        echo "==> installing uv"
        curl -LsSf https://astral.sh/uv/install.sh | sh
        export PATH="$HOME/.local/bin:$PATH"
    fi
fi
echo "==> uv $(uv --version)"

# --- 2. virtualenv ----------------------------------------------------------
# A venv copied from another machine points at an interpreter that no longer
# exists; detect that and rebuild instead of failing cryptically later.
if [ -d "$VENV" ] && [ "$RECREATE" -eq 0 ]; then
    if ! "$VENV/bin/python" -c "" >/dev/null 2>&1; then
        echo "==> existing .venv is broken (stale interpreter); recreating"
        RECREATE=1
    fi
fi
if [ "$RECREATE" -eq 1 ]; then
    rm -rf "$VENV"
fi
if [ ! -d "$VENV" ]; then
    echo "==> creating .venv (python $PYTHON_VERSION)"
    uv venv --python "$PYTHON_VERSION" "$VENV"
fi

# --- 3. dependencies --------------------------------------------------------
echo "==> installing project (editable, all extras)"
VIRTUAL_ENV="$VENV" uv pip install --python "$VENV/bin/python" \
    -e "$REPO_ROOT[graph,embed,pdf,math,dev]"

echo
echo "Done. Activate with:  source .venv/bin/activate"
echo "Run tests with:       .venv/bin/python -m pytest -q"
