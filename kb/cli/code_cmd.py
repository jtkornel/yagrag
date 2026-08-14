"""`kb code` command group: list | show | check.

Inspect and statically check stored code snippets referenced by
statically checkable nodes.
"""

from __future__ import annotations

import json as _json
from pathlib import Path
from typing import Optional

import typer
from rich.console import Console
from rich.table import Table

from .. import code as kb_code
from ..code import checker as code_checker
from ..config import KBConfig
from ..graph.connection import GraphDB, KuzuNotInstalled


code_app = typer.Typer(
    name="code",
    help="Inspect and statically check stored code snippets.",
    no_args_is_help=True,
)

_console = Console()
_err_console = Console(stderr=True)


def _fail(message: str, json_output: bool, code: int = 2) -> None:
    if json_output:
        typer.echo(_json.dumps({"error": message}))
    else:
        _err_console.print(f"[red]error:[/red] {message}")
    raise typer.Exit(code=code)


def _load_config(kb_root: Path, json_output: bool) -> KBConfig:
    try:
        return KBConfig.load(kb_root)
    except FileNotFoundError as exc:
        _fail(str(exc), json_output)
        raise AssertionError  # unreachable


def _open_db(kb_root: Path, config: KBConfig, json_output: bool) -> GraphDB:
    try:
        return GraphDB(kb_root / config.paths.graph_db)
    except KuzuNotInstalled as exc:
        _fail(str(exc), json_output)
        raise AssertionError  # unreachable


_KB_OPT = typer.Option(
    Path("."),
    "--kb",
    help="Knowledge base directory (containing kb.toml).",
)
_JSON_OPT = typer.Option(False, "--json", help="Emit JSON output.")


def _format_status(status: str) -> str:
    if status == kb_code.STATUS_OK:
        return "[green]ok[/green]"
    if status == kb_code.STATUS_FAILED:
        return "[red]failed[/red]"
    if status == kb_code.STATUS_UNCHECKED:
        return "[yellow]unchecked[/yellow]"
    return status


@code_app.command("list")
def cmd_list(
    status: Optional[str] = typer.Option(
        None,
        "--status",
        help="Filter by status (ok | failed | unchecked).",
    ),  # noqa: UP007
    label: Optional[str] = typer.Option(
        None,
        "--label",
        help="Filter by node label (must be statically checkable in this schema).",
    ),  # noqa: UP007
    kb: Path = _KB_OPT,
    json_output: bool = _JSON_OPT,
) -> None:
    """List all nodes carrying a snippet, with optional filtering."""
    if status is not None and status not in (
        kb_code.STATUS_OK,
        kb_code.STATUS_FAILED,
        kb_code.STATUS_UNCHECKED,
    ):
        _fail(
            f"invalid --status {status!r}; expected one of ok, failed, unchecked",
            json_output,
        )

    config = _load_config(kb, json_output)
    g = _open_db(kb, config, json_output)
    try:
        try:
            nodes = kb_code.list_code_nodes(
                g,
                labels=[label] if label else None,
            )
        except kb_code.CodeError as exc:
            _fail(str(exc), json_output)
            return

        if status is not None:
            nodes = [n for n in nodes if n.status == status]

        enriched: list[dict[str, object]] = []
        for node in nodes:
            stale = False
            missing = False
            if not node.path:
                missing = True
            else:
                try:
                    path = kb_code.resolve_code_path(kb, config, node.path)
                    if path.is_file():
                        file_text = path.read_text(encoding="utf-8")
                        stale = bool(node.stored_hash) and (
                            node.stored_hash
                            != code_checker.content_hash(file_text)
                        )
                    else:
                        missing = True
                except kb_code.CodeError:
                    missing = True

            enriched.append(
                {
                    "label": node.label,
                    "id": node.id,
                    "name": node.name,
                    "language": node.language,
                    "path": node.path,
                    "entry": node.entry,
                    "status": node.status,
                    "checked_at": node.checked_at,
                    "checker": node.checker,
                    "stale": stale,
                    "missing": missing,
                }
            )
    finally:
        g.close()

    if json_output:
        typer.echo(_json.dumps({"nodes": enriched}, indent=2))
        return

    if not enriched:
        _console.print("[dim]no nodes carry a snippet[/dim]")
        raise typer.Exit(code=0)

    table = Table(
        "Ref",
        "Language",
        "Status",
        "Path",
        "Checked",
        "Flags",
    )
    for item in enriched:
        flags: list[str] = []
        if bool(item["stale"]):
            flags.append("stale")
        if bool(item["missing"]):
            flags.append("missing")
        flags_str = ", ".join(flags) if flags else "-"

        node_ref = f"{item['label']}:{item['id']}"
        table.add_row(
            node_ref,
            str(item["language"] or "-"),
            _format_status(str(item["status"])),
            str(item["path"] or "-"),
            str(item["checked_at"] or "-"),
            flags_str,
        )
    _console.print(table)


@code_app.command("show")
def cmd_show(
    ref: str = typer.Argument(..., help="Ref in the form <Label>:<id> ."),
    kb: Path = _KB_OPT,
) -> None:
    """Print the snippet source for the given ref."""
    if ":" not in ref:
        _fail("ref must be in the form <Label>:<id>", json_output=False)

    label, node_id = ref.split(":", 1)
    if not label or not node_id:
        _fail("ref must be in the form <Label>:<id>", json_output=False)

    config = _load_config(kb, json_output=False)
    g = _open_db(kb, config, json_output=False)
    try:
        try:
            nodes = kb_code.list_code_nodes(
                g,
                labels=[label],
                node_id=node_id,
            )
        except kb_code.CodeError as exc:
            _fail(str(exc), json_output=False)
            return

        if not nodes:
            _fail(f"no such code node: {ref}", json_output=False)
        node = nodes[0]
        try:
            typer.echo(kb_code.read_snippet(kb, config, node))
        except kb_code.CodeError as exc:
            _fail(str(exc), json_output=False)
            return
    finally:
        g.close()


@code_app.command("check")
def cmd_check(
    label: Optional[str] = typer.Option(
        None,
        "--label",
        help="Filter by node label (must be statically checkable in this schema).",
    ),  # noqa: UP007
    node_id: Optional[str] = typer.Option(
        None,
        "--id",
        help="Filter by node id.",
    ),  # noqa: UP007
    all: bool = typer.Option(
        False,
        "--all",
        "-a",
        help="Check all statically checkable nodes (default behavior).",
    ),
    lint: bool = typer.Option(False, "--lint", help="Run optional lint checks."),
    kb: Path = _KB_OPT,
    json_output: bool = _JSON_OPT,
) -> None:
    """Statically check stored code snippets."""
    config = _load_config(kb, json_output)
    g = _open_db(kb, config, json_output)
    try:
        try:
            nodes = kb_code.list_code_nodes(
                g,
                labels=[label] if label else None,
                node_id=node_id,
            )
        except kb_code.CodeError as exc:
            _fail(str(exc), json_output)
            return

        results = kb_code.check_nodes(kb, config, g, nodes, lint=lint)
    finally:
        g.close()

    checked = len(results)
    ok = sum(1 for r in results if r.status == kb_code.STATUS_OK)
    failed = sum(1 for r in results if r.status == kb_code.STATUS_FAILED)
    unchecked = sum(1 for r in results if r.status == kb_code.STATUS_UNCHECKED)

    if json_output:
        payload = {
            "checked": checked,
            "ok": ok,
            "failed": failed,
            "unchecked": unchecked,
            "results": [r.to_dict() for r in results],
        }
        typer.echo(_json.dumps(payload, indent=2))
        return

    for r in results:
        _console.print(
            f"{_format_status(r.status)} {r.ref} {r.language or '-'} {r.path or '-'}"
        )
        for err in r.errors:
            _console.print(f"  [red]error:[/red] {err}")
        for warn in r.warnings:
            _console.print(f"  [yellow]warn:[/yellow] {warn}")

    _console.print(
        f"summary: ok={ok}, failed={failed}, unchecked={unchecked}, checked={checked}"
    )
