"""bench command line."""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
from contextlib import ExitStack
from pathlib import Path
from urllib.parse import urlsplit

from bench.lease import Held, Leases
from bench.session import RUNS, default_root, ensure_runs_link, lease_dir, shared_root, worktree_name
from bench.tree import BuildTree

DEFAULT_ROOT = "/mnt/c/Users/Simon/arcfw-bench"


def find_repo(start: Path) -> Path:
    """The firmware repo containing `start` (the directory holding ArcLoRaM_Base.ioc)."""
    top = subprocess.run(["git", "-C", str(start), "rev-parse", "--show-toplevel"],
                         check=True, capture_output=True, text=True).stdout.strip()
    repo = Path(top)
    if not (repo / "ArcLoRaM_Base.ioc").exists():
        raise ValueError(f"{repo} is not the firmware repo (no ArcLoRaM_Base.ioc)")
    return repo


def parse_define(text: str) -> tuple[str, str]:
    name, sep, value = text.partition("=")
    if not sep:
        raise argparse.ArgumentTypeError(f"-D {text}: expected NAME=VALUE")
    return name, value


def _windows(path: Path) -> str:
    """Windows form of a Build Tree path (the tests use a local directory as is)."""
    from bench.winpath import to_windows

    return to_windows(path) if str(path).startswith("/mnt/") else str(path)


def _root(args: argparse.Namespace, repo: Path) -> Path:
    """The Build Tree root: --root / $BENCH_ROOT, else one per worktree (a build rewrites the tree)."""
    return Path(args.root) if args.root else default_root(repo, Path(DEFAULT_ROOT))


def _leases(repo: Path, args: argparse.Namespace) -> Leases:
    return Leases(lease_dir(repo), worktree=worktree_name(repo), command=getattr(args, "command_text", "bench"))


def _held_note(exc: Held) -> str:
    return f"{exc} - another session works on it: use other boards, or wait"


def cmd_build(args: argparse.Namespace) -> int:
    from bench.build import build

    repo = Path(args.repo) if args.repo else find_repo(Path.cwd())
    bt = BuildTree(_root(args, repo))
    overrides = dict(args.define or [])
    try:
        result = build(repo, bt, args.cls, overrides, windows=_windows, clean=args.clean)
    except (ValueError, subprocess.CalledProcessError) as exc:
        print(f"bench build: {exc}", file=sys.stderr)
        return 2

    for d in result.log.errors:
        print(d)
    for d in result.log.warnings:
        print(d)
    print(f"build {result.build_id} ({', '.join(result.configs)}): "
          f"{'ok' if result.ok else 'FAILED'}, {len(result.log.errors)} error(s), "
          f"{len(result.log.warnings)} warning(s); log {result.log_path}")
    for problem in result.problems:
        print(f"  {problem}")
    if result.ok:
        for (core, cfg), elf in sorted(result.elfs.items()):
            print(f"  {cfg} {core}: {elf}")
    return 0 if result.ok else 1


def parse_assignment(text: str) -> tuple[int, str]:
    """'2=C2' -> (2, 'C2'): Node ID and the class it is flashed as."""
    nid, sep, cls = text.partition("=")
    if not sep or not nid.isdigit() or cls not in ("C1", "C2", "C3"):
        raise argparse.ArgumentTypeError(f"--node {text}: expected <Node ID>=C1|C2|C3, e.g. 2=C2")
    return int(nid), cls


def parse_proposal(text: str) -> tuple[int, str]:
    """'2=C2' or '4=watch' -> (2, 'C2'): a Node ID and the class, or watch only, recommended in `bench choose`."""
    nid, sep, cls = text.partition("=")
    if not sep or not nid.isdigit() or cls not in ("C1", "C2", "C3", "watch"):
        raise argparse.ArgumentTypeError(f"--propose {text}: expected <Node ID>=C1|C2|C3|watch, e.g. 2=C2")
    return int(nid), cls


def _context(args: argparse.Namespace):
    """(repo, fleet, ports, node table, capture dir): the fleet is the local ST-LINK boards and the
    Pi Nodes of pi-nodes.toml and bench.toml behind the one programmer-shaped interface."""
    from bench.boards import load_node_table, query_ports
    from bench.capture import CAPTURE_DIR
    from bench.config import FLEET_FILE, load_remotes
    from bench.fleet import Fleet
    from bench.pinode import PiNodeLink
    from bench.programmer import Programmer

    repo = Path(args.repo) if args.repo else find_repo(Path.cwd())
    try:
        remotes = load_remotes(fleet=repo / FLEET_FILE)
    except ValueError as exc:
        raise SystemExit(f"bench: {exc}") from exc
    fleet = Fleet(Programmer(), [PiNodeLink(r) for r in remotes])
    ensure_runs_link(repo)
    return (repo, fleet, {**query_ports(), **fleet.ports()}, load_node_table(repo),
            shared_root(repo) / CAPTURE_DIR)


def _probe_arg(args: argparse.Namespace) -> bool | set[str]:
    """--probe-uids reads every unknown UID; --probe-uid ID only the named boards."""
    return True if args.probe_uids else (set(args.probe_uid) if args.probe_uid else False)


def _down_note(prog) -> str:
    """Said after a failure: the Pi Nodes that were configured but did not answer."""
    down = getattr(prog, "down", [])
    return f" (Pi Node(s) not answering: {', '.join(r.name for r in down)})" if down else ""


def _survey(args: argparse.Namespace):
    """What `bench boards` and `bench map` show: (repo, boards, Pi Nodes not answering, boards another
    session holds, boards another session's capture records). A UID is read over SWD only for the
    boards asked for and no other session holds."""
    from bench.capture import Capture
    from bench.flash import discover
    from bench.viz import capture_owner

    repo, prog, ports, table, capture_dir = _context(args)
    leases = _leases(repo, args)
    with ExitStack() as stack:
        boards = discover(prog, ports, table, capture_dir, probe_unknown=_probe_arg(args),
                          may_probe=lambda sn: leases.try_exclusive(stack, sn))
    # A session on code from before leases takes none; its capture holding a board's port is what shows it.
    foreign = {x: p.out for p in Capture(repo, data=shared_root(repo)).status([b.port for b in boards if b.port]).foreign
               for x in p.ports}
    elsewhere = {b.sn: capture_owner(foreign[b.port]) for b in boards if b.port in foreign}
    return repo, boards, prog.down, leases.holders([b.sn for b in boards]), elsewhere


def _snapshot(args: argparse.Namespace):
    from datetime import datetime, timezone

    from bench.viz import snapshot

    repo, boards, down, held, elsewhere = _survey(args)
    return snapshot(boards, down, held, worktree_name(repo), datetime.now(timezone.utc), elsewhere), repo


def cmd_boards(args: argparse.Namespace) -> int:
    import json

    from bench.boards import format_uid

    if args.json:
        snap, _ = _snapshot(args)
        print(json.dumps(snap, indent=2))
        return 0 if snap["boards"] else 1
    _, boards, down, held, elsewhere = _survey(args)
    if not boards and not down:
        print("no ST-LINK probe connected")
        return 1
    for b in boards:
        uid = f"{format_uid(b.uid)} ({b.uid_source})" if b.uid else "unknown (no BOOT in the capture)"
        nid = b.node_id if b.node_id is not None else ("not in node_id.c" if b.uid else "?")
        build = b.build or ("none (firmware without build= in BOOT)" if b.uid_source == "trace" else "?")
        where = urlsplit(b.port).netloc if b.remote else (b.port or "no COM port")
        print(f"{'pi-node' if b.remote else 'probe'} {b.sn}  {where}  Node ID {nid}  UID {uid}  last build {build}"
              + (f"  HELD by {held[b.sn]}" if b.sn in held else "")
              + (f"  (recorded by {elsewhere[b.sn]}: may be in use)" if b.sn in elsewhere else ""))
    for r in down:
        print(f"pi-node {r.name}  {r.host}:{r.log}  not answering")
    known = sum(1 for b in boards if b.node_id is not None)
    print(f"{len(boards)} board(s) connected, {known} with a known Node ID"
          + (f", {len(held)} held by another session" if held else "")
          + (f", {len(down)} Pi Node(s) not answering" if down else ""))
    return 0 if boards else 1


def cmd_map(args: argparse.Namespace) -> int:
    from bench.viz import render_html

    snap, repo = _snapshot(args)
    out = Path(args.out) if args.out else shared_root(repo) / RUNS / "bench-map.html"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(render_html(snap), encoding="utf-8")
    n = snap["boards"]
    print(f"bench map: {len(n)} board(s) connected ({sum(b['kind'] == 'st-link' for b in n)} local, "
          f"{sum(b['kind'] == 'pi-node' for b in n)} on Pi Nodes), {len(snap['not_answering'])} not answering -> {out}")
    if args.open:
        import platform

        from bench.capture import wsl_to_windows
        from bench.choose import open_in_browser

        target = wsl_to_windows(out.resolve()) if "microsoft" in platform.uname().release.lower() else str(out.resolve())
        if not open_in_browser(target):
            print(f"bench map: could not open a browser; open {target} yourself", file=sys.stderr)
            return 1
    return 0


def cmd_choose(args: argparse.Namespace) -> int:
    import json

    from arclog.expect import parse_duration

    from bench.choose import AnswerError, ask, command_line, default_proposal, open_in_browser, validate_answer

    try:
        timeout = parse_duration(args.timeout or "15m").total_seconds()
    except ValueError as exc:
        print(f"bench choose: {exc}", file=sys.stderr)
        return 3
    snap, _ = _snapshot(args)
    if not snap["boards"]:
        print("bench choose: no board is connected", file=sys.stderr)
        return 1
    proposal = dict(args.propose) if args.propose else default_proposal(snap)
    overrides = dict(args.define or [])
    try:
        if proposal:
            validate_answer(snap, {"nodes": {str(n): c for n, c in proposal.items()}, "overrides": overrides})
    except AnswerError as exc:
        print(f"bench choose: that recommendation is not possible: {exc}", file=sys.stderr)
        return 3
    try:
        answer = ask(snap, proposal, overrides, timeout, opener=(lambda url: False) if args.no_open else open_in_browser)
    except KeyboardInterrupt:
        print("bench choose: stopped", file=sys.stderr)
        return 130
    if answer is None:
        print(f"bench choose: no answer within {args.timeout or '15m'}", file=sys.stderr)
        return 2
    print(f"choice: {command_line(answer)}")
    print(json.dumps(answer))
    return 0


def cmd_capture(args: argparse.Namespace) -> int:
    from bench.capture import Capture

    repo, prog, ports, _, _ = _context(args)
    present = [ports[p.sn] for p in prog.probes() if p.sn in ports]
    cap = Capture(repo, data=shared_root(repo))
    if args.action == "status":
        st = cap.status(present)
        if st.running:
            print(f"bench capture: pid {st.running.pid}, ports {', '.join(st.running.ports)} -> {cap.dir}")
        else:
            print("bench capture: not running")
        for p in st.foreign:
            print(f"other capture: pid {p.pid}, ports {', '.join(p.ports)} -> {p.out}")
        elsewhere = {x for p in st.foreign for x in p.ports}
        missing = [m for m in st.missing if m not in elsewhere]
        if missing:
            print(f"not recorded: {', '.join(missing)} (bench capture up)")
        return 0 if st.running and not missing else 1
    try:
        _, done = cap.up(present, replace=args.replace, required=[])
    except RuntimeError as exc:
        print(f"bench capture: {exc}", file=sys.stderr)
        return 1
    print(f"bench capture {done}: {', '.join(p for p in present if p not in cap.left_alone)} -> {cap.dir}"
          + (f" ({', '.join(cap.left_alone)} stay with another capture)" if cap.left_alone else ""))
    return 0


def cmd_flash(args: argparse.Namespace) -> int:
    from bench.build import build
    from bench.capture import Capture
    from bench.flash import boot_check, discover, flash_nodes, select
    from bench.programmer import ProgrammerError, Refused

    assignments = dict(args.node)
    repo, prog, ports, table, capture_dir = _context(args)
    leases = _leases(repo, args)
    with ExitStack() as stack:     # the leases are held until the command ends
        try:
            boards = select(discover(prog, ports, table, capture_dir, probe_unknown=_probe_arg(args),
                                     may_probe=lambda sn: leases.try_exclusive(stack, sn)), assignments)
            stack.enter_context(leases.hold(exclusive=[b.sn for b in boards.values()]))
            present = [ports[p.sn] for p in prog.probes() if p.sn in ports]
            capture = Capture(repo, data=shared_root(repo))
            _, done = capture.up(present, required=[b.port for b in boards.values() if b.port])
            print(f"capture {done}: {', '.join(p for p in present if p not in capture.left_alone)}"
                  + (f" ({', '.join(capture.left_alone)} stay with another capture)" if capture.left_alone else ""))
            result = build(repo, BuildTree(_root(args, repo)), sorted(set(assignments.values())),
                           dict(args.define or []), windows=_windows)
            if not result.ok:
                for d in result.log.errors:
                    print(d)
                print(f"build {result.build_id} FAILED: {'; '.join(result.problems)}; log {result.log_path}")
                return 1
            print(f"build {result.build_id} ok; log {result.log_path}")
            since, flashed = flash_nodes(prog, boards, table, result, assignments)
        except Held as exc:
            print(f"bench flash: {_held_note(exc)}", file=sys.stderr)
            return 1
        except (LookupError, RuntimeError, ValueError, Refused, ProgrammerError) as exc:
            print(f"bench flash: {exc}{_down_note(prog)}", file=sys.stderr)
            return 1
        if args.no_check:
            return 0
        ok, verdict = boot_check(capture_dir, since, result.build_id, boards, assignments, flashed,
                                 timeout_s=args.timeout)
        print(f"boot check: {verdict}")
        return 0 if ok else 1


def cmd_reset(args: argparse.Namespace) -> int:
    from bench.flash import discover, select
    from bench.programmer import ProgrammerError

    repo, prog, ports, table, capture_dir = _context(args)
    try:
        boards = select(discover(prog, ports, table, capture_dir), {nid: "" for nid in args.node_ids})
        with _leases(repo, args).hold(exclusive=[b.sn for b in boards.values()]):
            for nid, b in boards.items():
                prog.reset(b.sn)
                print(f"reset Node ID {nid} ({b.port}, probe {b.sn})")
    except Held as exc:
        print(f"bench reset: {_held_note(exc)}", file=sys.stderr)
        return 1
    except (LookupError, ProgrammerError) as exc:
        print(f"bench reset: {exc}{_down_note(prog)}", file=sys.stderr)
        return 1
    return 0


def _scenario_from_args(args: argparse.Namespace):
    """(Scenario, TOML text) from a file, or from --node/--watch/--expect/--forbid/-D/--timeout."""
    from bench.scenario import from_dict, load, parse_expect_text, to_toml

    if args.scenario:
        if args.node or args.watch or args.expect or args.forbid or args.define:
            raise ValueError("give a scenario file or --node/--watch/--expect/--forbid/-D, not both")
        return load(args.scenario), Path(args.scenario).read_text(encoding="utf-8")
    if not args.node:
        raise ValueError("give a scenario file or at least one --node ID=CLASS")
    d: dict = {}
    if args.description:
        d["description"] = args.description
    d["timeout"] = args.timeout or "10m"
    d["nodes"] = {str(nid): cls for nid, cls in args.node}
    d["nodes"].update({str(nid): "watch" for nid in args.watch or []})
    if args.define:
        d["overrides"] = dict(args.define)
    d["expect"] = [parse_expect_text(e) for e in args.expect or []]
    d["forbid"] = [{"event": ev} for ev in args.forbid or []]
    text = to_toml(d)
    return from_dict(d), text


def cmd_run(args: argparse.Namespace) -> int:
    from bench.build import build
    from bench.capture import Capture
    from bench.flash import discover, flash_nodes, select
    from bench.collector import pi_clock_states
    from bench.manifest import dumps, read_host, start_manifest
    from bench.programmer import ProgrammerError, Refused
    from bench.run import follow, run_dir
    from bench.scenario import plan
    from bench.validate import conclude, format_validity

    try:
        s, text = _scenario_from_args(args)
    except ValueError as exc:
        print(f"bench run: {exc}", file=sys.stderr)
        return 3
    if args.save:
        Path(args.save).write_text(text, encoding="utf-8")
        print(f"scenario saved to {args.save}")
    print(plan(s))
    repo, prog, ports, table, capture_dir = _context(args)
    leases = _leases(repo, args)
    with ExitStack() as stack:     # the leases are held until the run ends
        try:
            found = discover(prog, ports, table, capture_dir, probe_unknown=_probe_arg(args),
                             may_probe=lambda sn: leases.try_exclusive(stack, sn))
            boards = select(found, s.nodes)
            others = [b for b in found if b not in boards.values()]
            written = set(s.flashed) | {a.reset for a in s.actions}     # flashed or reset: one holder
            stack.enter_context(leases.hold(
                exclusive=[boards[n].sn for n in boards if n in written],
                shared=[boards[n].sn for n in boards if n not in written]))   # only watched: many may
            present = [ports[p.sn] for p in prog.probes() if p.sn in ports]
            capture = Capture(repo, data=shared_root(repo))
            _, done = capture.up(present, required=[b.port for b in boards.values() if b.port])
            print(f"capture {done}: {', '.join(p for p in present if p not in capture.left_alone)}"
                  + (f" ({', '.join(capture.left_alone)} stay with another capture)" if capture.left_alone else ""))
            result = build(repo, BuildTree(_root(args, repo)), sorted(set(s.flashed.values())), s.overrides,
                           windows=_windows)
            if not result.ok:
                for d in result.log.errors:
                    print(d)
                print(f"build {result.build_id} FAILED: {'; '.join(result.problems)}; log {result.log_path}")
                return 1
            print(f"build {result.build_id} ok; log {result.log_path}")
            since, flashed = flash_nodes(prog, {n: boards[n] for n in s.flashed}, table, result, s.flashed)
            out = run_dir(shared_root(repo) / RUNS, since, result.build_id)
            collector_url = os.environ.get("BENCH_COLLECTOR_URL")
            pi_nodes = [b.node for b in boards.values() if b.remote]
            clocks_start = pi_clock_states(collector_url, pi_nodes)
            manifest = start_manifest(command=args.command_text, worktree=worktree_name(repo),
                                      bench_commit=_head(repo), build_id=result.build_id, scenario=s, boards=boards,
                                      others=others, started=since, flashed=flashed, host=read_host())
            out.mkdir(parents=True, exist_ok=True)      # `bench note` finds the running session by its manifest
            (out / "manifest.toml").write_text(dumps(manifest), encoding="utf-8")
            outcome = follow(s, boards, result.build_id, capture_dir, since, reset=prog.reset, flashed=flashed)
        except Held as exc:
            print(f"bench run: {_held_note(exc)}", file=sys.stderr)
            return 1
        except (LookupError, RuntimeError, ValueError, Refused, ProgrammerError) as exc:
            print(f"bench run: {exc}{_down_note(prog)}", file=sys.stderr)
            return 1
        clocks_end = pi_clock_states(collector_url, pi_nodes)
        clocks = {name: (clocks_start[name], clocks_end.get(name, {})) for name in clocks_start}
        assessment = conclude(out, capture_dir, text, s, result.build_id, outcome, boards, others, manifest,
                              read_host(), clocks)
        print(outcome.verdict)
        for line in format_validity(assessment):
            print(line)
        print(f"record: {out / 'report.md'}")
        return assessment.code


def _head(repo: Path) -> str:
    """The short commit of the checkout bench runs from."""
    return subprocess.run(["git", "-C", str(repo), "rev-parse", "--short", "HEAD"], check=True,
                          capture_output=True, text=True).stdout.strip()


def cmd_validate(args: argparse.Namespace) -> int:
    """Say again, from a stored record alone, whether its data can be used: exit 0 pass, 1 fail, 2 timeout,
    3 not a record, 4 invalid."""
    from arclog.validity import invalidating

    from bench.manifest import tomllib
    from bench.validate import assess, format_validity

    record = Path(args.record)
    if not (record / "manifest.toml").is_file():
        print(f"bench validate: {record} has no manifest.toml (not a run record, or one written before "
              "manifests)", file=sys.stderr)
        return 3
    try:
        a = assess(record)
        stored = tomllib.loads((record / "manifest.toml").read_text(encoding="utf-8"))["run"].get("exit_code")
    except (OSError, ValueError, KeyError) as exc:
        print(f"bench validate: {record}: {exc!r}", file=sys.stderr)
        return 3
    verdict = {0: "PASS", 1: "FAIL"}.get(a.verdict_code, "TIMEOUT or no verdict")
    print(f"{record.name}: {'INVALID' if invalidating(a.causes, a.dataset) else 'valid'}; the trace alone gives "
          f"{verdict}; exit code {a.code}")
    for line in format_validity(a):
        print(line)
    if stored is not None and stored != a.code:
        print(f"differs from the exit code {stored} stored in the manifest at the end of the run")
    return a.code


def cmd_note(args: argparse.Namespace) -> int:
    """A timestamped note in the running session: a board moved, the ambient temperature, a replug."""
    from datetime import datetime, timezone

    from bench.manifest import append_note, find_running

    runs = Path(args.runs) if args.runs else shared_root(Path(args.repo) if args.repo else find_repo(Path.cwd())) / RUNS
    now = datetime.now(timezone.utc)
    folder = find_running(runs, now)
    if folder is None:
        print("bench note: no running session (no record folder with a manifest that has no end yet)",
              file=sys.stderr)
        return 1
    append_note(folder, " ".join(args.text), now)
    print(f"note added to {folder.name}")
    return 0


def cmd_scenario(args: argparse.Namespace) -> int:
    from bench.scenario import load, plan

    try:
        s = load(args.file)
    except (OSError, ValueError) as exc:
        print(f"{args.file}: {exc}", file=sys.stderr)
        return 3
    print(plan(s))
    return 0


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="bench", description=__doc__)
    p.add_argument("--root", default=os.environ.get("BENCH_ROOT"),
                   help=f"Build Tree directory on the Windows disk (default: {DEFAULT_ROOT} for the main "
                        "checkout, <it>-<worktree> for a worktree, so builds do not collide)")
    p.add_argument("--repo", help="firmware repo (default: the git repo of the current directory)")
    sub = p.add_subparsers(dest="cmd", required=True)

    b = sub.add_parser("build", help="sync the Build Tree and build both cores headless; exit 0 ok, 1 failed")
    b.add_argument("--class", dest="cls", action="append", required=True, choices=["C1", "C2", "C3"],
                   help="node class to build (both cores of Debug_<class>); repeat for several")
    b.add_argument("-D", dest="define", action="append", type=parse_define, metavar="NAME=VALUE",
                   help="Build Override for this build only; repeat for several")
    b.add_argument("--clean", action="store_true", help="rebuild everything instead of an incremental build")
    b.set_defaults(func=cmd_build)

    bd = sub.add_parser("boards", help="list the connected boards: probe, COM port, UID, Node ID, last build")
    bd.add_argument("--probe-uids", action="store_true",
                    help="read unknown UIDs over SWD (reboots those boards)")
    bd.add_argument("--probe-uid", action="append", metavar="ID",
                    help="read this board's UID over SWD (probe serial number or Pi Node name); repeat for several")
    bd.add_argument("--json", action="store_true", help="print the boards as JSON (what `bench map` draws)")
    bd.set_defaults(func=cmd_boards)

    mp = sub.add_parser("map", help="draw the connected boards, local and on Pi Nodes, as one HTML page")
    mp.add_argument("--out", metavar="FILE", help="where to write the page (default: tools/arclog/runs/bench-map.html)")
    mp.add_argument("--probe-uid", action="append", metavar="ID", help="read this board's UID first (see `boards`)")
    mp.add_argument("--probe-uids", action="store_true", help="read unknown UIDs over SWD (reboots those boards)")
    mp.add_argument("--open", action="store_true", help="open the page in the browser when it is written")
    mp.set_defaults(func=cmd_map)

    ch = sub.add_parser("choose", help="ask the user, in a page that opens by itself, which board runs as which class "
                                       "and with which overrides; prints the answer as options for `run`")
    ch.add_argument("--propose", action="append", type=parse_proposal, metavar="ID=CLASS",
                    help="the recommendation shown preselected, e.g. 1=C3 or 4=watch "
                         "(default: the C3 on the local board, C2 on the Pi Nodes)")
    ch.add_argument("-D", dest="define", action="append", type=parse_define, metavar="NAME=VALUE",
                    help="a Build Override offered in the page")
    ch.add_argument("--timeout", help="how long to wait for the answer, e.g. 5m (default 15m)")
    ch.add_argument("--no-open", action="store_true", help="do not open a browser, only print the address")
    ch.add_argument("--probe-uid", action="append", metavar="ID", help="read this board's UID first (see `boards`)")
    ch.add_argument("--probe-uids", action="store_true", help="read unknown UIDs over SWD (reboots those boards)")
    ch.set_defaults(func=cmd_choose)

    c = sub.add_parser("capture", help="the always-on capture of every board's trace")
    c.add_argument("action", choices=["up", "status"])
    c.add_argument("--replace", action="store_true", help="stop another capture holding the ports")
    c.set_defaults(func=cmd_capture)

    f = sub.add_parser("flash", help="build, then flash both cores of each named board and check its boot; "
                                     "exit 0 ok, 1 failed")
    f.add_argument("--node", action="append", required=True, type=parse_assignment, metavar="ID=CLASS",
                   help="Node ID and the class to flash it as, e.g. 2=C2; repeat for several")
    f.add_argument("-D", dest="define", action="append", type=parse_define, metavar="NAME=VALUE",
                   help="Build Override for this build only")
    f.add_argument("--probe-uids", action="store_true", help="read unknown UIDs over SWD (reboots those boards)")
    f.add_argument("--probe-uid", action="append", metavar="ID",
                   help="read this board's UID over SWD (probe serial number or Pi Node name)")
    f.add_argument("--timeout", type=float, default=60, help="seconds to wait for the boot check")
    f.add_argument("--no-check", action="store_true", help="do not wait for the boot")
    f.set_defaults(func=cmd_flash)

    rn = sub.add_parser("run", help="run a scenario: build, flash, act, decide, record; "
                                    "exit 0 pass, 1 fail, 2 timeout, 3 invalid scenario, "
                                    "4 invalid run (a bench fault, or a firmware event in a dataset)")
    rn.add_argument("scenario", nargs="?", help="scenario file (TOML); or describe the run with the options")
    rn.add_argument("--node", action="append", type=parse_assignment, metavar="ID=CLASS",
                    help="flash Node ID as C1/C2/C3, e.g. 2=C2; repeat for several")
    rn.add_argument("--watch", action="append", type=int, metavar="ID",
                    help="check this Node ID's trace without flashing it")
    rn.add_argument("--expect", action="append", metavar="'ID|any EVENT [field=value] [within=7m] [count=2]'",
                    help="e.g. '2 CLK to=WARM within=7m'; repeat for several")
    rn.add_argument("--forbid", action="append", metavar="EVENT", help="event that fails the run, e.g. TX_LATE")
    rn.add_argument("-D", dest="define", action="append", type=parse_define, metavar="NAME=VALUE",
                    help="Build Override for this run only")
    rn.add_argument("--timeout", help="e.g. 10m (default 10m)")
    rn.add_argument("--description", help="one line saved with the scenario")
    rn.add_argument("--save", metavar="FILE", help="also save the scenario described by the options")
    rn.add_argument("--probe-uids", action="store_true", help="read unknown UIDs over SWD (reboots those boards)")
    rn.add_argument("--probe-uid", action="append", metavar="ID",
                    help="read this board's UID over SWD (probe serial number or Pi Node name)")
    rn.set_defaults(func=cmd_run)

    v = sub.add_parser("validate", help="say again, from a stored run record alone, whether its data can be used; "
                                        "exit 0 pass, 1 fail, 2 timeout, 3 not a record, 4 invalid")
    v.add_argument("record", metavar="RUN_DIR", help="a record folder of tools/arclog/runs/")
    v.set_defaults(func=cmd_validate)

    n = sub.add_parser("note", help="add a timestamped note to the running session (a board moved, the ambient "
                                    "temperature, a replug)")
    n.add_argument("text", nargs="+")
    n.add_argument("--runs", help=argparse.SUPPRESS)
    n.set_defaults(func=cmd_note)

    sc = sub.add_parser("scenario", help="check a scenario file without touching the boards")
    sc.add_argument("action", choices=["check"])
    sc.add_argument("file")
    sc.set_defaults(func=cmd_scenario)

    r = sub.add_parser("reset", help="reset boards (no flash)")
    r.add_argument("node_ids", nargs="+", type=int, metavar="NODE_ID")
    r.set_defaults(func=cmd_reset)
    return p


def main(argv: list[str] | None = None) -> int:
    # Progress must reach a pipe (Monitor, tee) as it happens, not when the run ends.
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(line_buffering=True)
    args = build_parser().parse_args(argv)
    args.command_text = " ".join(["bench", *(sys.argv[1:] if argv is None else argv)])[:160]
    try:
        return args.func(args)
    except subprocess.CalledProcessError as exc:
        tool = Path(str(exc.cmd[0])).name if exc.cmd else "a command"
        detail = (exc.stderr or exc.stdout or "").strip().splitlines()
        print(f"bench {args.cmd}: {tool} failed (exit {exc.returncode})"
              + (f": {detail[0]}" if detail else ""), file=sys.stderr)
        return 1
