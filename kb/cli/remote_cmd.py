"""CLI subcommands for remote knowledge base lifecycle: clone, pull, push, remote."""

from __future__ import annotations

import contextlib
import json as _json
from pathlib import Path
from typing import Any

import typer
from rich.console import Console

from ..config import CONFIG_FILENAME, IncompatibleKBVersionError, KBConfig
from ..remote import GitError, clone_kb, get_remotes, pull_kb, push_kb

remote_app = typer.Typer(
    help="Manage remote repositories for knowledge bases.",
    no_args_is_help=True,
)
_console = Console()

_JSON_OPT = typer.Option(
    False,
    "--json",
    "-j",
    help="Emit machine-readable JSON on stdout.",
)

_KB_OPT = typer.Option(
    Path("."),
    "--kb",
    help="Knowledge base directory (containing kb.toml).",
)


def _fail(msg: str, json_output: bool, code: int = 1) -> None:
    if json_output:
        typer.echo(_json.dumps({"ok": False, "error": msg}))
    else:
        _console.print(f"[red]error:[/red] {msg}")
    raise typer.Exit(code=code)


@remote_app.command("list")
def cmd_remote_list(
    kb: Path = _KB_OPT,
    json_output: bool = _JSON_OPT,
) -> None:
    """List configured git remotes for this knowledge base."""
    remotes = get_remotes(kb)
    if json_output:
        typer.echo(_json.dumps({"ok": True, "remotes": remotes}))
    else:
        if not remotes:
            _console.print("[yellow]No git remotes configured for this KB.[/yellow]")
        else:
            for name, url in remotes.items():
                _console.print(f"  [bold]{name}[/bold]\t{url}")


def handle_clone(
    url: str,
    dest: Path | None = None,
    restore_dump: bool = True,
    json_output: bool = False,
) -> None:
    """Core logic for cloning a knowledge base and verifying compatibility."""
    try:
        dest_dir = clone_kb(url, dest)
    except GitError as exc:
        _fail(str(exc), json_output)
        return

    # Check that dest_dir has kb.toml
    config_file = dest_dir / CONFIG_FILENAME
    if not config_file.is_file():
        _fail(
            f"Cloned repository at {dest_dir} does not contain a {CONFIG_FILENAME}. "
            "It may not be a valid yagrag knowledge base.",
            json_output,
        )
        return

    # Validate version compatibility
    try:
        cfg = KBConfig.load(dest_dir, check_compat=True)
    except IncompatibleKBVersionError as exc:
        _fail(str(exc), json_output)
        return
    except Exception as exc:  # noqa: BLE001
        _fail(f"Failed to parse {CONFIG_FILENAME}: {exc}", json_output)
        return

    # If dump file is present and restore_dump is True, restore the graph automatically
    dump_path = dest_dir / "graph_dump.json.gz"
    if not dump_path.is_file():
        dump_path = dest_dir / "graph_dump.json"

    if restore_dump and dump_path.is_file():
        # Check if database file already exists
        db_file = dest_dir / cfg.paths.graph_db
        if not db_file.exists():
            from .graph_cmd import cmd_restore
            with contextlib.suppress(Exception):
                cmd_restore(input_file=dump_path, kb=dest_dir, apply_schema=True, json_output=json_output)

    result: dict[str, Any] = {
        "ok": True,
        "cloned_to": str(dest_dir),
        "name": cfg.name,
        "format_version": cfg.format_version,
    }

    if json_output:
        typer.echo(_json.dumps(result))
    else:
        _console.print(
            f"[green]Successfully cloned knowledge base '{cfg.name}' into:[/green] {dest_dir}\n"
            f"Format version: {cfg.format_version}\n"
            f"You can now run commands like:\n"
            f"  kb search --kb {dest_dir} <query>\n"
            f"  kb graph lint --kb {dest_dir}"
        )


def handle_pull(
    kb: Path = Path("."),
    migrate: bool = True,
    json_output: bool = False,
) -> None:
    """Core logic for pulling changes and optionally applying migrations."""
    # Check compatibility first
    try:
        cfg = KBConfig.load(kb, check_compat=True)
    except Exception as exc:  # noqa: BLE001
        _fail(str(exc), json_output)
        return

    try:
        out = pull_kb(kb)
    except GitError as exc:
        _fail(str(exc), json_output)
        return

    migrations_applied: list[str] = []
    if migrate:
        migrations_dir = kb / cfg.paths.schema_dir / "migrations"
        if migrations_dir.is_dir():
            from ..graph.connection import open_graph
            from ..schema.migrations import apply_migrations
            g = open_graph(kb)
            try:
                migrations_applied = apply_migrations(g, migrations_dir)
            finally:
                g.close()

    result = {
        "ok": True,
        "pull_output": out,
        "migrations_applied": migrations_applied,
    }
    if json_output:
        typer.echo(_json.dumps(result))
    else:
        _console.print(f"[green]pull complete:[/green] {out}")
        if migrations_applied:
            _console.print(
                f"[green]applied {len(migrations_applied)} new migration(s):[/green] "
                f"{', '.join(migrations_applied)}"
            )


def handle_push(
    kb: Path = Path("."),
    remote: str = "origin",
    branch: str | None = None,
    json_output: bool = False,
) -> None:
    """Core logic for pushing changes to remote."""
    try:
        KBConfig.load(kb, check_compat=True)
    except Exception as exc:  # noqa: BLE001
        _fail(str(exc), json_output)
        return

    try:
        out = push_kb(kb, remote=remote, branch=branch)
    except GitError as exc:
        _fail(str(exc), json_output)
        return

    result = {"ok": True, "push_output": out}
    if json_output:
        typer.echo(_json.dumps(result))
    else:
        _console.print(f"[green]push complete:[/green] {out or 'Up to date'}")
