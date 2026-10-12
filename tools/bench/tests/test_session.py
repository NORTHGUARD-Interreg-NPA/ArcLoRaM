"""Several worktrees use the bench at once (ADR-0003): what they share and what each has of its own."""

import subprocess
from pathlib import Path

import pytest

from bench.session import (default_root, ensure_runs_link, prune_trees, shared_root, stale_trees,
                           worktree_name)

BASE = Path("/mnt/c/Users/Simon/arcfw-bench")


def git(cwd, *args):
    subprocess.run(["git", "-c", "user.name=t", "-c", "user.email=t@t", *args], cwd=cwd, check=True,
                   capture_output=True)


@pytest.fixture
def repos(tmp_path):
    main = tmp_path / "Firmware"
    main.mkdir()
    git(main, "init", "-b", "main")
    git(main, "commit", "--allow-empty", "-m", "root")
    wt = tmp_path / "Firmware" / ".claude" / "worktrees" / "78-remote"
    git(main, "worktree", "add", "-b", "feat/78", str(wt))
    return main.resolve(), wt.resolve()


def test_the_main_checkout_is_its_own_shared_root(repos):
    main, _ = repos
    assert shared_root(main) == main
    assert worktree_name(main) == "main"


def test_a_worktree_and_its_subdirectories_share_the_main_checkouts_root(repos):
    main, wt = repos
    (wt / "tools" / "bench").mkdir(parents=True)
    assert shared_root(wt) == main
    assert shared_root(wt / "tools" / "bench") == main
    assert worktree_name(wt) == "78-remote"


def test_each_worktree_builds_in_a_tree_of_its_own(repos):
    main, wt = repos
    assert default_root(main, BASE) == BASE
    assert default_root(wt, BASE) == Path("/mnt/c/Users/Simon/arcfw-bench-78-remote")


def test_a_worktree_reaches_the_shared_runs_through_its_own_runs_folder(repos):
    main, wt = repos
    (main / "tools" / "arclog" / "runs" / "bench").mkdir(parents=True)
    ensure_runs_link(wt)
    link = wt / "tools" / "arclog" / "runs"
    assert link.is_symlink() and link.resolve() == main / "tools" / "arclog" / "runs"
    ensure_runs_link(wt)                                   # idempotent
    assert link.resolve() == main / "tools" / "arclog" / "runs"


def test_a_runs_folder_that_already_holds_files_is_left_alone(repos):
    main, wt = repos
    own = wt / "tools" / "arclog" / "runs"
    own.mkdir(parents=True)
    (own / "keep.txt").write_text("x")
    ensure_runs_link(wt)
    assert not own.is_symlink() and (own / "keep.txt").exists()


def test_the_main_checkout_needs_no_link(repos):
    main, _ = repos
    ensure_runs_link(main)
    assert not (main / "tools" / "arclog" / "runs").is_symlink()


def test_every_worktree_takes_its_leases_in_the_same_folder(repos):
    from bench.session import lease_dir

    main, wt = repos
    assert lease_dir(main) == lease_dir(wt) == main / ".git" / "bench-leases"


# --- a Build Tree follows its worktree ----------------------------------------------------------------------------
# Git runs nothing when a worktree is removed, so the Build Tree of a removed worktree (`<base>-<name>`) is found by
# its name: no worktree has it any more.


def make_tree(base: Path, name: str | None, owner: Path | None = None) -> Path:
    """A Build Tree as bench leaves it: the main one is `base`, a worktree's is `<base>-<name>`."""
    root = base if name is None else base.with_name(f"{base.name}-{name}")
    for part in ("tree", "workspace", "logs"):
        (root / part).mkdir(parents=True)
    if owner is not None:
        (root / "owner").write_text(f"{owner}\n", encoding="utf-8")
    return root


@pytest.fixture
def base(tmp_path):
    return tmp_path / "bench-root" / "arcfw-bench"


def test_the_tree_of_a_worktree_that_no_longer_exists_is_stale(repos, base):
    main, wt = repos
    make_tree(base, None)
    make_tree(base, "78-remote")
    gone = make_tree(base, "36-guard-run")

    assert stale_trees(main, base) == [gone]


def test_removing_a_worktree_makes_its_tree_stale(repos, base):
    main, wt = repos
    tree = make_tree(base, "78-remote")
    assert stale_trees(main, base) == []

    git(main, "worktree", "remove", str(wt))

    assert stale_trees(main, base) == [tree]


def test_a_worktree_folder_deleted_by_hand_is_gone_too(repos, base):
    import shutil

    main, wt = repos
    tree = make_tree(base, "78-remote")
    shutil.rmtree(wt)             # git still lists it, as prunable

    assert stale_trees(main, base) == [tree]


def test_a_folder_that_is_not_a_build_tree_is_left_alone(repos, base):
    main, _ = repos
    (base.parent / "arcfw-bench-notes").mkdir(parents=True)
    (base.parent / "arcfw-bench-notes" / "todo.txt").write_text("x")
    (base.parent / "arcfw-bench-file").write_text("x")
    (base.parent / "other-36-guard-run").mkdir()
    make_tree(base.parent / "other", "36-guard-run")      # another tool's folder, not named after this base

    assert stale_trees(main, base) == []


def test_a_tree_made_for_another_clone_is_left_alone(repos, base, tmp_path):
    main, _ = repos
    mine = make_tree(base, "old-a", owner=main)
    legacy = make_tree(base, "old-b")                      # made before trees carried their owner
    make_tree(base, "old-c", owner=tmp_path / "SomeOtherClone")

    assert stale_trees(main, base) == [mine, legacy]


def test_prune_removes_the_stale_trees_and_only_them(repos, base):
    main, _ = repos
    keep_main, keep_wt = make_tree(base, None), make_tree(base, "78-remote")
    gone_a, gone_b = make_tree(base, "36-guard-run"), make_tree(base, "node2-c3-swap")

    done, failed = prune_trees(main, base)

    assert done == [gone_a, gone_b] and failed == []
    assert not gone_a.exists() and not gone_b.exists()
    assert keep_main.exists() and keep_wt.exists()
    assert prune_trees(main, base) == ([], [])             # nothing left to do


def test_a_dry_run_removes_nothing(repos, base):
    main, _ = repos
    gone = make_tree(base, "36-guard-run")

    done, failed = prune_trees(main, base, dry_run=True)

    assert (done, failed) == ([gone], []) and gone.exists()


def test_a_tree_that_cannot_be_removed_does_not_stop_the_others(repos, base, monkeypatch):
    import bench.session as session

    main, _ = repos
    locked, free = make_tree(base, "a-locked"), make_tree(base, "b-free")
    real = session.shutil.rmtree

    def rmtree(path, *args, **kwargs):
        if Path(path) == locked:
            raise PermissionError("in use by another program")
        real(path, *args, **kwargs)

    monkeypatch.setattr(session.shutil, "rmtree", rmtree)

    done, failed = prune_trees(main, base)

    assert done == [free] and not free.exists()
    assert [(d, "in use" in why) for d, why in failed] == [(locked, True)] and locked.exists()


def test_a_tree_another_session_removed_meanwhile_is_not_a_failure(repos, base, monkeypatch):
    import bench.session as session

    main, _ = repos
    gone = make_tree(base, "36-guard-run")
    real = session.shutil.rmtree

    def rmtree(path, *args, **kwargs):
        real(path, *args, **kwargs)          # the other sweep got there first: ours finds nothing left
        raise FileNotFoundError(path)

    monkeypatch.setattr(session.shutil, "rmtree", rmtree)

    assert prune_trees(main, base) == ([gone], [])


def test_bench_prune_lists_and_removes_the_trees_of_removed_worktrees(repos, base, monkeypatch, capsys):
    from bench.cli import main as bench_main

    main, _ = repos
    monkeypatch.setattr("bench.cli.DEFAULT_ROOT", str(base))
    keep = make_tree(base, "78-remote")
    gone = make_tree(base, "36-guard-run")

    assert bench_main(["--repo", str(main), "prune", "--dry-run"]) == 0
    assert "would remove the Build Tree of a removed worktree: arcfw-bench-36-guard-run" in capsys.readouterr().out
    assert gone.exists()

    assert bench_main(["--repo", str(main), "prune"]) == 0
    assert "removed the Build Tree of a removed worktree: arcfw-bench-36-guard-run" in capsys.readouterr().out
    assert not gone.exists() and keep.exists()

    assert bench_main(["--repo", str(main), "prune"]) == 0
    assert "no Build Tree to prune" in capsys.readouterr().out


def test_a_build_sweeps_the_trees_of_removed_worktrees(repos, base, monkeypatch, capsys):
    """Nobody has to remember `bench prune`: the next command that builds does it."""
    from types import SimpleNamespace

    import bench.build as build_mod
    from bench.cli import main as bench_main

    main, wt = repos
    monkeypatch.setattr("bench.cli.DEFAULT_ROOT", str(base))
    gone = make_tree(base, "36-guard-run")
    seen = []
    log = SimpleNamespace(errors=[], warnings=[])
    monkeypatch.setattr(build_mod, "build", lambda *a, **k: seen.append(gone.exists()) or SimpleNamespace(
        build_id="b", configs=["Debug_C2"], ok=True, log=log, log_path="l", problems=[], elfs={}))

    assert bench_main(["--repo", str(wt), "build", "--class", "C2"]) == 0

    out = capsys.readouterr().out
    assert "pruned the Build Tree of a removed worktree: arcfw-bench-36-guard-run" in out
    assert not gone.exists() and seen == [False]            # gone before the build started


def test_a_root_chosen_by_hand_is_not_swept(repos, base, tmp_path, monkeypatch):
    from types import SimpleNamespace

    import bench.build as build_mod
    from bench.cli import main as bench_main

    main, wt = repos
    monkeypatch.setattr("bench.cli.DEFAULT_ROOT", str(base))
    gone = make_tree(base, "36-guard-run")
    log = SimpleNamespace(errors=[], warnings=[])
    monkeypatch.setattr(build_mod, "build", lambda *a, **k: SimpleNamespace(
        build_id="b", configs=["Debug_C2"], ok=True, log=log, log_path="l", problems=[], elfs={}))

    assert bench_main(["--root", str(tmp_path / "mine"), "--repo", str(wt), "build", "--class", "C2"]) == 0

    assert gone.exists()


def test_a_sweep_that_fails_never_fails_the_build(repos, base, monkeypatch, capsys):
    from types import SimpleNamespace

    import bench.build as build_mod
    from bench.cli import main as bench_main

    main, wt = repos
    monkeypatch.setattr("bench.cli.DEFAULT_ROOT", str(base))
    log = SimpleNamespace(errors=[], warnings=[])
    monkeypatch.setattr(build_mod, "build", lambda *a, **k: SimpleNamespace(
        build_id="b", configs=["Debug_C2"], ok=True, log=log, log_path="l", problems=[], elfs={}))
    monkeypatch.setattr("bench.cli.prune_trees", lambda *a, **k: (_ for _ in ()).throw(OSError("disk gone")))

    assert bench_main(["--repo", str(wt), "build", "--class", "C2"]) == 0

    assert "Build Tree sweep skipped: disk gone" in capsys.readouterr().err
