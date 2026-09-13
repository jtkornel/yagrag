"""Git transport and remote management helpers for knowledge base repositories."""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path


class GitError(RuntimeError):
    """Raised when a git command fails or git is unavailable."""


def _check_git_available() -> str:
    git_bin = shutil.which("git")
    if not git_bin:
        raise GitError(
            "Git executable not found on PATH. "
            "Please install git to clone, pull, or push remote knowledge bases."
        )
    return git_bin


def run_git(args: list[str], cwd: Path | None = None) -> str:
    """Execute a git command and return stripped stdout. Raises GitError on failure."""
    git_bin = _check_git_available()
    cmd = [git_bin, *args]
    res = subprocess.run(
        cmd,
        cwd=cwd,
        capture_output=True,
        text=True,
        check=False,
    )
    if res.returncode != 0:
        err_msg = res.stderr.strip() or res.stdout.strip() or f"exit code {res.returncode}"
        raise GitError(f"git {' '.join(args)} failed: {err_msg}")
    return res.stdout.strip()


def clone_kb(url: str, dest_dir: Path | None = None) -> Path:
    """Clone a knowledge base Git repository from a URL or GitHub shorthand (owner/repo)."""
    # Expand GitHub shorthand like "user/repo" into full git URL
    target_url = url
    if not (url.startswith("http://") or url.startswith("https://") or url.startswith("git@") or url.startswith("ssh://") or Path(url).exists()):
        if "/" in url and not url.startswith("."):
            target_url = f"https://github.com/{url}.git"

    if dest_dir is None:
        # Determine name from URL
        stem = target_url.rstrip("/").split("/")[-1]
        if stem.endswith(".git"):
            stem = stem[:-4]
        dest_dir = Path.cwd() / stem

    dest_dir = Path(dest_dir).resolve()
    if dest_dir.exists() and any(dest_dir.iterdir()):
        raise GitError(f"destination directory {dest_dir} already exists and is not empty")

    run_git(["clone", target_url, str(dest_dir)])
    return dest_dir


def pull_kb(kb_root: Path) -> str:
    """Run `git pull` inside a knowledge base root directory."""
    return run_git(["pull"], cwd=kb_root)


def push_kb(kb_root: Path, remote: str = "origin", branch: str | None = None) -> str:
    """Run `git push` inside a knowledge base root directory."""
    args = ["push", remote]
    if branch:
        args.append(branch)
    return run_git(args, cwd=kb_root)


def get_remotes(kb_root: Path) -> dict[str, str]:
    """Return a mapping of remote name to fetch URL."""
    try:
        out = run_git(["remote", "-v"], cwd=kb_root)
    except GitError:
        return {}
    remotes: dict[str, str] = {}
    for line in out.splitlines():
        parts = line.split()
        if len(parts) >= 2 and parts[2].startswith("(fetch)"):
            remotes[parts[0]] = parts[1]
    return remotes
