"""Deterministic, non-executing static checks for stored code snippets.

Two representations are supported, matching the standardization decision:

- `python` — Python 3.11 + NumPy source, checked with `ast.parse` and, when
  available and requested, `ruff check`.
- `sympy`  — a SymPy-parseable canonical expression, parsed with a restricted
  namespace and `evaluate=False`, then cross-checked for symbol consistency
  against the symbol-bearing nodes in the graph.

Terminology used throughout the code base:

- **statically checkable node type** — a node type whose schema declares the
  full `CODE_PROPERTIES` set, so each of its nodes can point at a snippet that
  `kb code check` verifies without executing it and without an LLM. This is the
  umbrella term; "static" here means the check is a pure inspection of stored
  text, and it covers both the Python and the SymPy representation.
- **symbol-bearing node type** — a node type whose schema declares a `symbol`
  property. These nodes are not checked themselves; they supply the vocabulary
  that the symbol-consistency check of a SymPy snippet is compared against.

The module is domain-agnostic: it never names a node label. Which node types
play which of those two roles is discovered from the applied schema by looking
for the required property sets, so the same checker serves a robotics KB, a
fluid-dynamics KB, or a KB about bird flight patterns.

Three hard rules shape this module:

1. **Nothing is ever executed.** Python snippets are only parsed to an AST;
   SymPy strings are parsed with a curated namespace, never `exec`'d.
2. **A failed check never blocks a write.** The outcome is recorded as data
   (`code_status`) on the node, so retrieval can surface or filter unverified
   facts. Only *invocation* errors raise.
3. **Warnings are not failures.** Unknown symbols and lint findings are
   reported but do not flip the status to `failed`; only a missing file or a
   genuine parse error does.
"""

from __future__ import annotations

import ast
import hashlib
import re
import shutil
import subprocess
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

from ..config import KBConfig
from ..graph.connection import GraphDB


# The contract a schema must satisfy for a node type to be statically
# checkable: it declares *all* of these properties. No node label is hardcoded
# anywhere in this module — which types are checkable is a property of the
# schema the knowledge base was built with, whatever domain it models.
CODE_PROPERTIES: tuple[str, ...] = (
    "code_language",
    "code_path",
    "code_entry",
    "code_status",
    "code_checked_at",
    "code_checker",
    "code_hash",
)

# The contract for a symbol-bearing node type — the supporting role that feeds
# the symbol-consistency check of stored expressions: it declares a `symbol`
# property. Its `symbol`, `id` and `name` values are all accepted as known
# symbols, since extraction commonly uses a slug id in expressions.
SYMBOL_PROPERTY = "symbol"

STATUS_OK = "ok"
STATUS_FAILED = "failed"
STATUS_UNCHECKED = "unchecked"

LANGUAGE_PYTHON = "python"
LANGUAGE_SYMPY = "sympy"

_SUFFIX_LANGUAGES = {
    ".py": LANGUAGE_PYTHON,
    ".sympy": LANGUAGE_SYMPY,
    ".sym": LANGUAGE_SYMPY,
    ".txt": LANGUAGE_SYMPY,
}


class CodeError(RuntimeError):
    """Raised for invocation errors (bad selector, unreadable KB, ...).

    Snippet-level problems are *not* errors: they are recorded as
    `code_status: failed` on the node.
    """


@dataclass(frozen=True)
class CodeNode:
    """A graph node carrying a code snippet reference."""

    label: str
    id: str
    name: str | None
    language: str | None
    path: str | None
    entry: str | None
    status: str
    checked_at: str | None
    checker: str | None
    stored_hash: str | None

    @property
    def ref(self) -> str:
        return f"{self.label}:{self.id}"


@dataclass
class CheckResult:
    """Outcome of statically checking one snippet."""

    label: str
    id: str
    status: str
    language: str | None
    path: str | None
    checker: str
    checked_at: str
    code_hash: str | None
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    @property
    def ref(self) -> str:
        return f"{self.label}:{self.id}"

    def to_dict(self) -> dict[str, Any]:
        return {
            "label": self.label,
            "id": self.id,
            "status": self.status,
            "language": self.language,
            "path": self.path,
            "checker": self.checker,
            "checked_at": self.checked_at,
            "code_hash": self.code_hash,
            "errors": list(self.errors),
            "warnings": list(self.warnings),
        }


# --- helpers -----------------------------------------------------------------


def _now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def content_hash(text: str) -> str:
    """Stable hash of snippet content, used to detect a stale check status."""
    return "sha256:" + hashlib.sha256(text.encode("utf-8")).hexdigest()


def resolve_code_path(kb_root: Path, config: KBConfig, code_path: str) -> Path:
    """Resolve a node's `code_path` to an absolute path inside the KB.

    `code_path` is interpreted relative to the KB root; for convenience a path
    given relative to the `code/` tree also resolves. Paths escaping the KB
    root are rejected — snippets always live inside the portable KB directory.
    """
    kb_root = kb_root.expanduser().resolve()
    candidate = Path(code_path)
    if candidate.is_absolute():
        raise CodeError(f"code_path must be relative to the KB root: {code_path!r}")

    primary = (kb_root / candidate).resolve()
    chosen = primary
    if not primary.exists():
        fallback = (kb_root / config.paths.code / candidate).resolve()
        if fallback.exists():
            chosen = fallback

    if not chosen.is_relative_to(kb_root):
        raise CodeError(f"code_path escapes the knowledge base: {code_path!r}")
    return chosen


def read_snippet(kb_root: Path, config: KBConfig, node: CodeNode) -> str:
    """Return the snippet source for `node`, verbatim."""
    if not node.path:
        raise CodeError(f"{node.ref} has no code_path")
    path = resolve_code_path(kb_root, config, node.path)
    if not path.is_file():
        raise CodeError(f"{node.ref}: code_path does not exist: {node.path}")
    return path.read_text(encoding="utf-8")


def _language_for(node: CodeNode, path: Path) -> str | None:
    if node.language:
        return node.language.strip().lower()
    return _SUFFIX_LANGUAGES.get(path.suffix.lower())


# --- inventory ---------------------------------------------------------------


def _row_to_node(label: str, row: dict[str, Any]) -> CodeNode:
    status = row.get("code_status") or STATUS_UNCHECKED
    return CodeNode(
        label=label,
        id=row["id"],
        name=row.get("name"),
        language=row.get("code_language"),
        path=row.get("code_path"),
        entry=row.get("code_entry"),
        status=status,
        checked_at=row.get("code_checked_at"),
        checker=row.get("code_checker"),
        stored_hash=row.get("code_hash"),
    )


def _table_properties(g: GraphDB, label: str) -> set[str]:
    return {str(row["name"]) for row in g.table_info(label)}


def statically_checkable_labels(g: GraphDB) -> tuple[str, ...]:
    """Return the node labels this knowledge base declares as statically checkable.

    A node type is statically checkable when its table declares the full
    `CODE_PROPERTIES` set. The answer therefore comes from the applied schema,
    not from any built-in domain vocabulary.
    """
    required = set(CODE_PROPERTIES)
    return tuple(
        label
        for label in sorted(g.node_table_names())
        if required <= _table_properties(g, label)
    )


def symbol_bearing_labels(g: GraphDB) -> tuple[str, ...]:
    """Return the node labels this knowledge base declares as symbol-bearing.

    These are not checked themselves; they define the symbol vocabulary the
    symbol-consistency check of a SymPy snippet is compared against.
    """
    return tuple(
        label
        for label in sorted(g.node_table_names())
        if SYMBOL_PROPERTY in _table_properties(g, label)
    )


def list_code_nodes(
    g: GraphDB,
    labels: Iterable[str] | None = None,
    node_id: str | None = None,
) -> list[CodeNode]:
    """Return every node that references a snippet, optionally filtered.

    The set of statically checkable labels is discovered from the schema, so a
    KB with no such node type simply yields nothing rather than raising.
    """
    available = statically_checkable_labels(g)
    wanted = tuple(labels) if labels is not None else available
    for label in wanted:
        if label not in available:
            raise CodeError(
                f"{label!r} is not a statically checkable label in this "
                "knowledge base; "
                + (
                    "expected one of " + ", ".join(available)
                    if available
                    else "the schema declares no statically checkable node type "
                    "(a node type becomes statically checkable by declaring "
                    "the properties: " + ", ".join(CODE_PROPERTIES) + ")"
                )
            )

    nodes: list[CodeNode] = []
    for label in wanted:
        cypher = (
            f"MATCH (n:{label}) WHERE n.code_path IS NOT NULL "
            "RETURN n.id AS id, n.name AS name, n.code_language AS code_language, "
            "n.code_path AS code_path, n.code_entry AS code_entry, "
            "n.code_status AS code_status, n.code_checked_at AS code_checked_at, "
            "n.code_checker AS code_checker, n.code_hash AS code_hash "
            "ORDER BY n.id"
        )
        for row in g.execute(cypher):
            if node_id is not None and row["id"] != node_id:
                continue
            nodes.append(_row_to_node(label, row))
    return nodes


def known_symbols(g: GraphDB) -> set[str]:
    """Collect symbol names the graph knows about.

    Every node type that declares a `symbol` property is a source; for each of
    its nodes the `symbol`, `id` and `name` values all count, since extraction
    commonly uses a slug id such as `pose_i` in expressions. Which types those
    are is decided by the schema, not by this module.
    """
    symbols: set[str] = set()
    for label in symbol_bearing_labels(g):
        rows = g.execute(
            f"MATCH (n:{label}) RETURN n.{SYMBOL_PROPERTY} AS symbol, "
            "n.id AS id, n.name AS name"
        )
        for row in rows:
            symbols.update(str(v) for v in row.values() if v)
    return {s for s in symbols if s}


# --- individual checks -------------------------------------------------------

# SymPy names a stored expression may reference. Deliberately curated: parsing
# happens against this namespace only, so an expression cannot reach arbitrary
# attributes even though `sympify` is `eval`-adjacent.
_SYMPY_ALLOWED = (
    "Eq Ne Symbol symbols Function Matrix Sum Product Integral Derivative diff "
    "integrate simplify expand sqrt exp log sin cos tan asin acos atan atan2 "
    "sinh cosh tanh Abs sign Min Max floor ceiling Piecewise Rational Integer "
    "Float pi E I oo Transpose Inverse Determinant trace zeros eye ones "
    "KroneckerDelta factorial binomial Pow Add Mul"
).split()


def _sympy_namespace() -> dict[str, Any]:
    import sympy  # local import: sympy is an optional extra

    # An empty `__builtins__` stops Python from injecting the real builtins
    # into the namespace `parse_expr` evaluates in, so a stored expression can
    # never reach `__import__`, `open`, `eval`, ...
    ns: dict[str, Any] = {"__builtins__": {}}
    for name in _SYMPY_ALLOWED:
        obj = getattr(sympy, name, None)
        if obj is not None:
            ns[name] = obj
    return ns


def _expression_lines(source: str) -> list[str]:
    """Split a stored SymPy file into checkable expressions.

    Blank lines and `#` comments are ignored, so an expression file can be
    annotated like any other source file.
    """
    lines: list[str] = []
    for raw in source.splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        lines.append(line)
    return lines


def _check_sympy(source: str, allowed: set[str]) -> tuple[list[str], list[str]]:
    """Parse each expression and cross-check its free symbols."""
    try:
        from sympy.parsing.sympy_parser import parse_expr
    except ImportError:
        return [], [
            "sympy is not installed; expression not parsed "
            "(install with `pip install .[math]`)"
        ]

    expressions = _expression_lines(source)
    if not expressions:
        return ["snippet contains no expression"], []

    namespace = _sympy_namespace()
    errors: list[str] = []
    free: set[str] = set()
    for line in expressions:
        try:
            expr = parse_expr(
                line,
                local_dict={},
                global_dict=namespace,
                evaluate=False,
            )
        except Exception as exc:  # sympy raises a wide range of exceptions
            errors.append(f"sympy parse error in {line!r}: {exc}")
            continue
        free.update(str(s) for s in getattr(expr, "free_symbols", set()))

    warnings: list[str] = []
    unknown = sorted(s for s in free if s not in allowed)
    if unknown and allowed:
        warnings.append(
            "symbols not found on any symbol-bearing node in the graph: "
            + ", ".join(unknown)
        )
    elif unknown:
        warnings.append(
            "symbol consistency skipped: the graph has no symbol-bearing nodes "
            "to check against"
        )
    return errors, warnings


def _check_python(source: str, path: Path) -> list[str]:
    try:
        ast.parse(source, filename=str(path))
    except SyntaxError as exc:
        return [f"python syntax error at line {exc.lineno}: {exc.msg}"]
    return []


def _run_ruff(path: Path) -> tuple[list[str], bool]:
    """Run `ruff check` on `path`. Returns (findings, ran)."""
    executable = shutil.which("ruff")
    if executable is None:
        return [], False
    try:
        proc = subprocess.run(
            [executable, "check", "--no-cache", "--quiet", str(path)],
            capture_output=True,
            text=True,
            timeout=60,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        return [f"ruff could not be run: {exc}"], False
    findings = [
        line.strip()
        for line in (proc.stdout + proc.stderr).splitlines()
        if line.strip()
    ]
    return findings, True


_EQUATION_COMMENT_RE = re.compile(
    r"#.*?\b(?:eq|equation)\.?\s*\(?\d+[a-z]?\)?", re.IGNORECASE
)


def _check_comment_equation_refs(source: str) -> list[str]:
    """Warn if snippet comments contain paper-specific equation references."""
    for line in source.splitlines():
        if "#" in line:
            comment_part = line[line.find("#") :]
            if _EQUATION_COMMENT_RE.search(comment_part):
                return [
                    "snippet comment contains paper-specific equation reference; "
                    "paper equation numbers belong on the Equation graph node property/summary, "
                    "not in code snippet comments"
                ]
    return []


# --- orchestration -----------------------------------------------------------


def check_node(
    kb_root: Path,
    config: KBConfig,
    node: CodeNode,
    allowed_symbols: set[str] | None = None,
    lint: bool = False,
) -> CheckResult:
    """Statically check one node's snippet. Never executes, never raises
    for snippet-level problems."""
    checkers: list[str] = []
    errors: list[str] = []
    warnings: list[str] = []
    language: str | None = node.language
    code_hash: str | None = None

    if not node.path:
        return CheckResult(
            label=node.label,
            id=node.id,
            status=STATUS_FAILED,
            language=language,
            path=None,
            checker="none",
            checked_at=_now(),
            code_hash=None,
            errors=["node has no code_path"],
        )

    try:
        path = resolve_code_path(kb_root, config, node.path)
    except CodeError as exc:
        return CheckResult(
            label=node.label,
            id=node.id,
            status=STATUS_FAILED,
            language=language,
            path=node.path,
            checker="none",
            checked_at=_now(),
            code_hash=None,
            errors=[str(exc)],
        )

    if not path.is_file():
        return CheckResult(
            label=node.label,
            id=node.id,
            status=STATUS_FAILED,
            language=language,
            path=node.path,
            checker="none",
            checked_at=_now(),
            code_hash=None,
            errors=[f"code_path does not exist: {node.path}"],
        )

    source = path.read_text(encoding="utf-8")
    code_hash = content_hash(source)
    language = _language_for(node, path)

    if language in (LANGUAGE_PYTHON, LANGUAGE_SYMPY):
        warnings.extend(_check_comment_equation_refs(source))

    if language == LANGUAGE_PYTHON:
        checkers.append("ast")
        errors.extend(_check_python(source, path))
        if lint and not errors:
            findings, ran = _run_ruff(path)
            if ran:
                checkers.append("ruff")
                warnings.extend(findings)
            else:
                warnings.append("ruff is not installed; lint skipped")
    elif language == LANGUAGE_SYMPY:
        checkers.append("sympy")
        if allowed_symbols is None:
            allowed_symbols = set()
        sympy_errors, sympy_warnings = _check_sympy(source, allowed_symbols)
        errors.extend(sympy_errors)
        warnings.extend(sympy_warnings)
    else:
        return CheckResult(
            label=node.label,
            id=node.id,
            status=STATUS_UNCHECKED,
            language=language,
            path=node.path,
            checker="none",
            checked_at=_now(),
            code_hash=code_hash,
            warnings=[
                "unknown code_language "
                f"{node.language!r} (expected {LANGUAGE_PYTHON!r} or {LANGUAGE_SYMPY!r})"
            ],
        )

    status = STATUS_FAILED if errors else STATUS_OK
    return CheckResult(
        label=node.label,
        id=node.id,
        status=status,
        language=language,
        path=node.path,
        checker="+".join(checkers) if checkers else "none",
        checked_at=_now(),
        code_hash=code_hash,
        errors=errors,
        warnings=warnings,
    )


def persist_result(g: GraphDB, result: CheckResult) -> None:
    """Write the check outcome back onto the node as plain data."""
    cypher = (
        f"MATCH (n:{result.label} {{id: $id}}) "
        "SET n.code_status = $status, n.code_checked_at = $checked_at, "
        "n.code_checker = $checker, n.code_hash = $code_hash"
    )
    g.execute(
        cypher,
        {
            "id": result.id,
            "status": result.status,
            "checked_at": result.checked_at,
            "checker": result.checker,
            "code_hash": result.code_hash or "",
        },
    )


def check_nodes(
    kb_root: Path,
    config: KBConfig,
    g: GraphDB,
    nodes: Iterable[CodeNode],
    lint: bool = False,
    persist: bool = True,
) -> list[CheckResult]:
    """Check several nodes, persisting each outcome on the node."""
    allowed = known_symbols(g)
    results: list[CheckResult] = []
    for node in nodes:
        result = check_node(kb_root, config, node, allowed_symbols=allowed, lint=lint)
        if persist:
            persist_result(g, result)
        results.append(result)
    return results
