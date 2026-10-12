"""What the worktrees that use the bench at once share, and what each has of its own (ADR-0003).

Shared, in the main checkout: the always-on capture and the run records (`tools/arclog/runs/`),
so every session reads every board's trace whoever started the capture, and a record survives
its worktree. Of its own, per worktree: the Build Tree, because a build rewrites it, and it goes
with the worktree (`prune_trees`).
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

RUNS = Path("tools/arclog/runs")

#: File in a Build Tree's root naming the main checkout it was made for (written by `tree.sync`), so that the
#: prune of another clone on the same PC leaves it alone.
OWNER = "owner"


def _git_common_dir(path: Path) -> Path:
    common = subprocess.run(["git", "-C", str(path), "rev-parse", "--git-common-dir"],
                            check=True, capture_output=True, text=True).stdout.strip()
    return (Path(path) / common).resolve()


def shared_root(path: Path) -> Path:
    """The main checkout of the repository containing `path` (the path itself outside a worktree)."""
    git_dir = _git_common_dir(path)
    return git_dir.parent if git_dir.name == ".git" else Path(path).resolve()


def lease_dir(path: Path) -> Path:
    """Where board leases are taken: inside the repository's own git folder, which every worktree shares
    and git never commits."""
    return _git_common_dir(path) / "bench-leases"


def worktree_name(repo: Path) -> str:
    """'main' for the main checkout, else the worktree's folder name (`.claude/worktrees/<name>`)."""
    repo = Path(repo).resolve()
    return "main" if repo == shared_root(repo) else repo.name


def default_root(repo: Path, base: Path) -> Path:
    """The Build Tree root of a checkout: `base` for the main checkout, `<base>-<name>` for a worktree."""
    name = worktree_name(repo)
    return base if name == "main" else base.with_name(f"{base.name}-{name}")


def ensure_runs_link(repo: Path) -> None:
    """Make `tools/arclog/runs` of a worktree lead to the shared one, so the paths in the docs hold
    (`tools/arclog/runs/bench/<node>-YYYYMMDD.log`). A folder that already holds files is left alone."""
    repo = Path(repo).resolve()
    shared = shared_root(repo)
    if shared == repo:
        return
    target, link = shared / RUNS, repo / RUNS
    if link.is_symlink() or (link.exists() and any(link.iterdir())):
        return
    target.mkdir(parents=True, exist_ok=True)
    if link.exists():
        link.rmdir()
    link.parent.mkdir(parents=True, exist_ok=True)
    link.symlink_to(target)


def worktree_names(repo: Path) -> set[str]:
    """The names (`worktree_name`) of the repository's worktrees that still exist: 'main' and the folder name of
    each other one. One whose folder was deleted without `git worktree remove` is gone, though git still lists it."""
    out = subprocess.run(["git", "-C", str(repo), "worktree", "list", "--porcelain"],
                         check=True, capture_output=True, text=True).stdout
    shared = shared_root(repo)
    names = set()
    for line in out.splitlines():
        if line.startswith("worktree "):
            path = Path(line.removeprefix("worktree "))
            if path.exists():
                names.add("main" if path.resolve() == shared else path.name)
    return names


def stale_trees(repo: Path, base: Path) -> list[Path]:
    """The Build Trees of worktrees that no longer exist, oldest name first.

    A Build Tree is named after its worktree (`default_root`), and git tells nobody when a worktree is removed:
    these are the `<base>-<name>` folders beside `base` that hold bench's folders, that no worktree has the name
    of, and that were not made for another clone of the repository (a tree from before trees carried their owner
    counts as ours).
    """
    if not base.parent.is_dir():
        return []
    names, shared, prefix = worktree_names(repo), str(shared_root(repo)), f"{base.name}-"
    stale = []
    for folder in sorted(base.parent.iterdir()):
        if not (folder.is_dir() and folder.name.startswith(prefix)) or folder.name[len(prefix):] in names:
            continue
        if not any((folder / part).is_dir() for part in ("tree", "workspace", "logs")):
            continue
        owner = folder / OWNER
        if owner.is_file() and owner.read_text(encoding="utf-8").strip() != shared:
            continue
        stale.append(folder)
    return stale


def prune_trees(repo: Path, base: Path, dry_run: bool = False) -> tuple[list[Path], list[tuple[Path, str]]]:
    """Remove the stale Build Trees (`stale_trees`). Returns the trees removed (or that would be, on a dry run) and
    the ones that could not be, with the reason: a program that holds a file does not stop the others."""
    done, failed = [], []
    for folder in stale_trees(repo, base):
        if not dry_run:
            try:
                shutil.rmtree(folder)
            except OSError as exc:
                if folder.exists():
                    failed.append((folder, str(exc)))
                    continue                    # else another session's sweep got there first
        done.append(folder)
    return done, failed
