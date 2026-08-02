"""Static checking of the formal content stored in the knowledge base.

Formal content — equations, algorithms, numerical procedures — is stored not
only as presentation LaTeX but as real source files under the KB's `code/`
tree, referenced by `code_path` on statically checkable nodes, whatever domain
the knowledge base models. This package checks those snippets *statically* —
it never executes them, and never asks an LLM — and records the outcome back
on the node as data.

No domain vocabulary lives here: a node type is statically checkable because
the schema gives it the `CODE_PROPERTIES` set, not because of its name.
"""

from .checker import (
    CODE_PROPERTIES,
    STATUS_FAILED,
    STATUS_OK,
    STATUS_UNCHECKED,
    SYMBOL_PROPERTY,
    CheckResult,
    CodeError,
    CodeNode,
    check_node,
    check_nodes,
    known_symbols,
    list_code_nodes,
    persist_result,
    read_snippet,
    resolve_code_path,
    statically_checkable_labels,
    symbol_bearing_labels,
)

__all__ = [
    "CODE_PROPERTIES",
    "STATUS_FAILED",
    "STATUS_OK",
    "STATUS_UNCHECKED",
    "SYMBOL_PROPERTY",
    "CheckResult",
    "CodeError",
    "CodeNode",
    "check_node",
    "check_nodes",
    "known_symbols",
    "list_code_nodes",
    "persist_result",
    "read_snippet",
    "resolve_code_path",
    "statically_checkable_labels",
    "symbol_bearing_labels",
]
