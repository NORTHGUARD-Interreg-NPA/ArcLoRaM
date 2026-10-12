"""Copy the log collector's record of a run into a folder, so that a judge reads the folder and not the network.

The collector stores every Pi Node's UART log and keeps recording while the host is off. A run of days, or of
many nodes, does not fit one answer: `/logs` gives its newest 10000 lines unless it is paged (`limit`, `after`),
and a judge that asked once judged a truncated record (#111). This pull pages, one node per call:

    python -m bench.pull --build ID --since 2026-10-07T14:06:55Z --node 2=nuna-node-03 --node 5=nuna-node-01 \\
        --dir tools/arclog/runs/<start>-<build>-collector

The folder is the record. It is append-only and idempotent: a second pull adds only what the collector got since,
and an answer that is smaller than what the folder holds never shrinks it.

    <node>-YYYYMMDD.log   the node's trace lines, `<UTC>\\t<device line>`, by UTC day (what every arclog tool reads)
    <node>-events.log     what the collector saw besides the trace: openocd crash lines, its own reconnects, marks
    pull.json             what the folder is: build, schema, where each node's pull stands (cursor), the collector's
                          view of each node at the end of the pull, and what makes a node's record unfit
    schema.json           the event table of the build, so the record is judged with the schema it was made with

A node's first pull asks for the newest lines since the start of the run and goes back with `until` while the
collector says `truncated`, because `after` pages from a cursor, not from a time. Later pulls go forward from the
cursor, oldest first, until `truncated` is false. A chunk is committed (lines appended, then the cursor saved) as
a whole: an interrupted pull is rolled back to the last committed chunk the next time, so no line is twice in
the folder and none is missed.

The collector stores a line it recovered after a reconnect after the lines that came live, with its own (older)
Pi time. The folder keeps the order of arrival; `arclog.stream` puts such a line back in place when it reads.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone, tzinfo
from pathlib import Path
from typing import Callable
from zoneinfo import ZoneInfo

from arclog.expect import capture_files, parse_since
from arclog.schema import events_from_source, events_to_dict

from bench.manifest import firmware_commit

STATE = "pull.json"
SCHEMA = "schema.json"
EVENTS_SUFFIX = "events"
#: The pull starts this long before the run's `since` (the flash), as the collector judge always has.
LEAD = timedelta(minutes=5)
#: Lines asked for in one call (the collector's own ceiling is 1000000). A node's pull is held to this many lines at
#: a time, which is what bounds its memory.
CHUNK = 20000
#: Chunk boundaries kept in the state, for `bench.collector` to tell a gap an export made from one the firmware made.
MAX_BOUNDARIES = 5000
#: A cursor is `<node number>:<generation of the store>:<offset>`; it is opaque to the collector's clients, and this
#: only reads it to see that the store has not been reset behind the pull.
_CURSOR = re.compile(r"^(\d+):(\d+):(\d+)$")

_SCHEMA_FILE = "tools/arclog/src/arclog/schema.py"


class PullError(ValueError):
    """The folder or the arguments cannot be used (exit 3), not a fault of the record."""


def http_get(url: str, timeout: float = 120.0) -> str:
    # The collector speaks plain http on the tailnet.
    with urllib.request.urlopen(url, timeout=timeout) as response:  # noqa: S310
        return response.read().decode("utf-8", errors="replace")


def _get_json(base: str, path: str, params: dict[str, str] | None = None, timeout: float = 120.0) -> dict:
    query = f"?{urllib.parse.urlencode(params)}" if params else ""
    return json.loads(http_get(f"{base}{path}{query}", timeout))


# ---------------------------------------------------------------------------
# State
# ---------------------------------------------------------------------------


@dataclass
class NodeState:
    """Where the pull of one node stands, and what it found."""

    name: str
    cursor: str | None = None          # the collector's `next` after the last committed chunk
    complete_to: str | None = None     # UTC: the folder holds every line the collector had until then
    problem: str | None = None         # why this node's record cannot be used (never cleared by a later pull)
    lines: int = 0                     # trace lines in the folder
    other: dict[str, int] = field(default_factory=dict)       # lines of the other sources (openocd, collector, mark)
    files: dict[str, int] = field(default_factory=dict)       # committed size of each file, in bytes
    boundaries: list[str] = field(default_factory=list)       # UTC time of the first line of each chunk after the first
    seen_above: list[str] = field(default_factory=list)       # ids above the cursor that are already in the folder
    status: dict = field(default_factory=dict)                # the collector's /nodes entry at the end of the last pull

    def to_dict(self) -> dict:
        return {k: v for k, v in self.__dict__.items() if k != "name"}


def _load(directory: Path) -> dict | None:
    path = directory / STATE
    if not path.is_file():
        return None
    try:
        state = json.loads(path.read_text(encoding="utf-8"))
        state["nodes"] = {name: NodeState(name, **d) for name, d in state["nodes"].items()}
        return state
    except (ValueError, TypeError, KeyError) as exc:
        raise PullError(f"{path}: not a pull record ({exc})") from exc


def load_state(directory: Path) -> dict | None:
    """The state of a pulled folder (`record` keys at the top, `nodes` as `NodeState`), or None if it has none."""
    return _load(Path(directory))


def _save(directory: Path, state: dict) -> None:
    data = {**state, "nodes": {name: n.to_dict() for name, n in state["nodes"].items()}}
    tmp = directory / f"{STATE}.tmp"
    tmp.write_text(json.dumps(data, indent=1) + "\n", encoding="utf-8")
    os.replace(tmp, directory / STATE)


def schema_from_git(commit: str) -> str | None:
    """The source of `schema.py` as the firmware commit had it, from this checkout's git; None if it cannot be had."""
    repo = Path(__file__).resolve().parents[4]
    try:
        done = subprocess.run(["git", "-C", str(repo), "show", f"{commit}:{_SCHEMA_FILE}"], capture_output=True,
                              text=True, timeout=30)
    except (OSError, subprocess.SubprocessError):
        return None
    return done.stdout if done.returncode == 0 else None


def _schema_record(build: str, directory: Path, schema_source: Callable[[str], str | None]) -> dict:
    """Which schema the record is judged with, and the table itself kept in the folder: that of the build's commit,
    read from git, or (when git does not have the commit) none, and the judge says so."""
    commit, dirty = firmware_commit(build)
    source = schema_source(commit)
    if source is not None:
        try:
            table = events_to_dict(events_from_source(source))
        except (ValueError, SyntaxError) as exc:
            return {"source": "checkout", "why": f"the schema of {commit} could not be read ({exc})"}
        (directory / SCHEMA).write_text(json.dumps(table, indent=1) + "\n", encoding="utf-8")
        return {"source": f"commit {commit}", "events": len(table), "dirty": dirty}
    return {"source": "checkout", "why": f"git has no commit {commit} to take the schema from"}


# ---------------------------------------------------------------------------
# One call to the collector
# ---------------------------------------------------------------------------


def _cursor(text: str | None) -> tuple[int, int, int] | None:
    m = _CURSOR.match(text or "")
    return (int(m[1]), int(m[2]), int(m[3])) if m else None


def _cursor_problem(old: str | None, new: str | None) -> str | None:
    """Why a `next` that is not ahead of the cursor we sent means the store was reset or rotated behind the pull."""
    a, b = _cursor(old), _cursor(new)
    if a is None or b is None:
        return None
    if b[1] != a[1]:
        return (f"the collector's storage for this node changed generation ({a[1]} -> {b[1]}): "
                "lines between the two may be missing")
    if b[2] < a[2]:
        return (f"the collector's storage is behind the pull (it is at {b[2]}, the cursor at {a[2]}): "
                "it was reset, and what came before is not in the folder")
    return None


def _utc(ts: str, pi_tz: tzinfo) -> datetime:
    when = datetime.fromisoformat(ts)
    return (when.replace(tzinfo=pi_tz) if when.tzinfo is None else when).astimezone(timezone.utc)


def _stamp(t: datetime) -> str:
    return t.strftime("%Y-%m-%dT%H:%M:%S.%fZ")


class _Writer:
    """Appends lines to a node's files and tells how many bytes each file holds once they are on disk."""

    def __init__(self, directory: Path, node: NodeState) -> None:
        self.directory = directory
        self.node = node
        self.rows: dict[str, list[str]] = {}
        self.trace = 0
        self.other: dict[str, int] = {}

    def add(self, when: datetime, source: str, text: str) -> None:
        text = text.replace("\r", " ").replace("\n", " ")
        if source == "uart":
            name = f"{self.node.name}-{when:%Y%m%d}.log"
            self.trace += 1
        else:
            name = f"{self.node.name}-{EVENTS_SUFFIX}.log"
            self.other[source] = self.other.get(source, 0) + 1
        self.rows.setdefault(name, []).append(f"{_stamp(when)}\t{text}\n")

    def commit(self) -> None:
        for name, rows in self.rows.items():
            with (self.directory / name).open("ab") as f:
                f.write("".join(rows).encode("utf-8"))
                f.flush()
                os.fsync(f.fileno())
                self.node.files[name] = f.tell()
        self.node.lines += self.trace
        for source, n in self.other.items():
            self.node.other[source] = self.node.other.get(source, 0) + n
        self.rows.clear()
        self.trace = 0
        self.other = {}


def _rollback(directory: Path, node: NodeState) -> None:
    """Back to the last committed chunk: what an interrupted pull appended beyond it is cut, and a file it created
    is removed. Nothing committed is touched."""
    names = {p.name for p in capture_files(directory, node.name, datetime.min.replace(tzinfo=timezone.utc))}
    names |= {p.name for p in directory.glob(f"{node.name}-{EVENTS_SUFFIX}.log")}
    for name in names:
        path = directory / name
        if name not in node.files:
            path.unlink()
        elif path.stat().st_size > node.files[name]:
            with path.open("r+b") as f:
                f.truncate(node.files[name])


def _chunk_params(name: str, chunk: int, **extra: str) -> dict[str, str]:
    return {"nodes": name, "limit": str(chunk), "exclude": "pi", "format": "json", **extra}


def _check_answer(answer: dict, node: str) -> list[dict]:
    if not isinstance(answer.get("lines"), list) or "truncated" not in answer:
        raise PullError(f"{node}: the collector's answer to /logs has no lines or no truncated flag")
    return answer["lines"]


def _above(old: list[str], lines: list[dict], cursor: str | None) -> list[str]:
    """The ids already in the folder that sit above the cursor: the collector orders a response by the Pi's time and
    its cursor by arrival, so a recovered line can be returned now and be above the cursor of this very answer."""
    end = _cursor(cursor)
    if end is None:
        return []
    ids = list(old) + [line["id"] for line in lines]
    return sorted({i for i in ids if (c := _cursor(i)) is not None and c[:2] == end[:2] and c[2] > end[2]})


def _forward(base: str, directory: Path, state: dict, node: NodeState, chunk: int, pi_tz: tzinfo,
             now: datetime) -> None:
    """From the cursor on, oldest first, until the collector has no more."""
    while True:
        answer = _get_json(base, "/logs", _chunk_params(node.name, chunk, after=node.cursor))
        lines = _check_answer(answer, node.name)
        problem = _cursor_problem(node.cursor, answer.get("next"))
        if problem:
            node.problem = problem
            return
        seen = set(node.seen_above)
        fresh = [line for line in lines if line["id"] not in seen]
        _append(directory, node, fresh, answer.get("next") or node.cursor, pi_tz, first=False)
        _save(directory, state)
        if not answer["truncated"]:
            node.complete_to = _stamp(now)
            return
        if not lines:
            node.problem = "the collector says there is more but sent no line: the pull cannot go on"
            return


def _append(directory: Path, node: NodeState, lines: list[dict], cursor: str | None, pi_tz: tzinfo,
            first: bool) -> None:
    """Commit one chunk (time-sorted, as the collector sends it): its lines, then the cursor."""
    writer = _Writer(directory, node)
    for line in lines:
        writer.add(_utc(line["ts"], pi_tz), line.get("source", "uart"), line["line"])
    if lines and not first and len(node.boundaries) < MAX_BOUNDARIES:
        node.boundaries.append(_stamp(_utc(lines[0]["ts"], pi_tz)))
    writer.commit()
    node.seen_above = _above(node.seen_above, lines, cursor)
    node.cursor = cursor


def _first(base: str, directory: Path, state: dict, node: NodeState, start: datetime, chunk: int, pi_tz: tzinfo,
           now: datetime) -> None:
    """The first pull of a node: the newest lines since `start`, then earlier ones while `truncated`, then written
    oldest first. (`after` pages oldest first from a cursor, and there is none for a time.)"""
    scratch = directory / ".pull"
    scratch.mkdir(exist_ok=True)
    spilled: list[Path] = []
    until: datetime | None = None
    carried: set[str] = set()
    cursor = None
    try:
        while True:
            params = _chunk_params(node.name, chunk, since=_stamp(start))
            if until is not None:
                params["until"] = _stamp(until)
            answer = _get_json(base, "/logs", params)
            lines = _check_answer(answer, node.name)
            if cursor is None:
                cursor = answer.get("next")
            kept = [line for line in lines if line["id"] not in carried]
            path = scratch / f"{node.name}-{len(spilled)}.json"
            path.write_text(json.dumps(kept), encoding="utf-8")
            spilled.append(path)
            if not answer["truncated"]:
                break
            oldest = _utc(lines[0]["ts"], pi_tz) if lines else None
            if oldest is None or (until is not None and oldest >= until):
                node.problem = "the collector says there is more but the pull cannot get earlier lines"
                return
            until = oldest  # `until` excludes its own instant; were it to include it, the line is told by its id
            carried = {line["id"] for line in lines if _utc(line["ts"], pi_tz) == oldest}
        for n, path in enumerate(reversed(spilled)):
            _append(directory, node, json.loads(path.read_text(encoding="utf-8")), cursor, pi_tz, first=n == 0)
        node.complete_to = _stamp(now)
        _save(directory, state)
    finally:
        for path in spilled:
            path.unlink(missing_ok=True)
        try:
            scratch.rmdir()
        except OSError:
            pass


# ---------------------------------------------------------------------------
# A pull
# ---------------------------------------------------------------------------

#: What of a /nodes entry the record keeps (no address of a Pi: a record is committed with its dataset).
_STATUS_KEYS = ("connected", "connected_since", "last_line_at", "silent_for_s", "oldest_line_at", "pi_ntp",
                "clock_lag_s", "recovered_from_replay", "last_error", "stored_bytes", "max_bytes")


def _snapshot(entry: dict | None) -> dict:
    return {k: entry[k] for k in _STATUS_KEYS if k in entry} if entry else {}


def pull(base: str, names: list[str], build: str, since: datetime, directory: Path, pi_tz: tzinfo, *,
         chunk: int = CHUNK, now: Callable[[], datetime] = lambda: datetime.now(timezone.utc),
         schema_source: Callable[[str], str | None] = schema_from_git,
         report: Callable[[str], None] = lambda msg: None) -> dict:
    """Bring `directory` up to what the collector holds for `names`; the state of the folder afterwards.

    Raises `PullError` for a folder or an argument that cannot be used, and `OSError` when the collector cannot be
    reached; the folder is then as the last committed chunk left it, and the next pull goes on from there.
    """
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True)
    start = (since - LEAD).astimezone(timezone.utc)
    state = _load(directory)
    if state is None:
        if any(directory.glob("*.log")):
            raise PullError(f"{directory} holds capture files but no {STATE}: it is not a pulled record, "
                            "pull into a new folder")
        state = {"build": build, "firmware_commit": firmware_commit(build)[0],
                 "schema": _schema_record(build, directory, schema_source), "start": _stamp(start),
                 "chunk": chunk, "nodes": {}}
    else:
        if state["build"] != build:
            raise PullError(f"{directory} was pulled for build {state['build']}, not {build}: pull into a new folder")
        if start < _parse_stamp(state["start"]) - timedelta(seconds=1):
            raise PullError(f"{directory} starts at {state['start']}, after the {_stamp(start)} asked for: "
                            "a folder is only ever appended to, pull into a new folder")
    start = _parse_stamp(state["start"])
    before = _get_json(base, "/nodes", timeout=30.0)
    for name in names:
        state["nodes"].setdefault(name, NodeState(name))
    _save(directory, state)  # a pull cut short leaves a folder that says so, not one nothing made
    for name in names:
        node = state["nodes"][name]
        _rollback(directory, node)
        if not node.problem:
            _pull_node(base, directory, state, node, name in before, (before.get(name) or {}).get("oldest_line_at"),
                       start, chunk, pi_tz, now())
        report(_pull_line(node))
    after = _get_json(base, "/nodes", timeout=30.0)
    for name in names:
        state["nodes"][name].status = _snapshot(after.get(name))
    state["pulled_at"] = _stamp(now())
    _save(directory, state)
    return state


def _pull_node(base: str, directory: Path, state: dict, node: NodeState, known: bool, oldest: str | None,
               start: datetime, chunk: int, pi_tz: tzinfo, now: datetime) -> None:
    if node.cursor is not None:
        if known:
            _forward(base, directory, state, node, chunk, pi_tz, now)
        return
    if oldest is not None and _utc(oldest, pi_tz) > start:
        node.problem = (f"the collector's oldest line for this node is {_stamp(_utc(oldest, pi_tz))}, after "
                        f"the start of the record ({_stamp(start)}): it no longer holds the beginning")
    elif not known:
        node.complete_to = _stamp(now)  # nothing to pull; the judge says the collector does not know it
    else:
        _first(base, directory, state, node, start, chunk, pi_tz, now)


def _parse_stamp(text: str) -> datetime:
    return datetime.fromisoformat(text.replace("Z", "+00:00"))


def _pull_line(node: NodeState) -> str:
    state = f"complete to {node.complete_to[11:19]}Z" if node.complete_to else "not complete"
    other = "".join(f", {n} {source}" for source, n in sorted(node.other.items()))
    return f"{node.name}: {node.lines} lines{other}, {state}" + (f"; UNFIT: {node.problem}" if node.problem else "")


# ---------------------------------------------------------------------------
# Command line
# ---------------------------------------------------------------------------


def node_pairs(values: list[str], what: str) -> dict[int, str]:
    out = {}
    for v in values:
        key, sep, val = v.partition("=")
        if not sep or not key.isdigit():
            raise ValueError(f"{what} {v!r}: use ID=VALUE")
        out[int(key)] = val
    return out


def add_record_arguments(p: argparse.ArgumentParser) -> None:
    p.add_argument("--collector", default=os.environ.get("BENCH_COLLECTOR_URL"),
                   help="base URL of the log collector (default: $BENCH_COLLECTOR_URL)")
    p.add_argument("--build", required=True, help="Build ID the flashed boards boot (bench flash prints it)")
    p.add_argument("--since", required=True, help="ISO UTC time, before the flash")
    p.add_argument("--node", action="append", default=[], metavar="ID=NAME",
                   help="the collector's name of a Node ID's Pi Node, e.g. 2=nuna-node-03; repeat")
    p.add_argument("--dir", required=True, type=Path, help="the run folder: the record is pulled into it")
    p.add_argument("--pi-tz", default="Europe/Paris", help="the Pi's time zone, for a line with no offset "
                   "(default: %(default)s)")
    p.add_argument("--chunk", type=int, default=CHUNK, help="lines asked for in one call (default: %(default)s)")


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="python -m bench.pull", description=__doc__.split("\n\n")[0])
    add_record_arguments(p)
    args = p.parse_args(argv)
    try:
        if not args.collector:
            raise ValueError("no collector: pass --collector or set BENCH_COLLECTOR_URL")
        names = list(node_pairs(args.node, "--node").values())
        if not names:
            raise ValueError("no --node: name at least one Pi Node")
        state = pull(args.collector.rstrip("/"), names, args.build, parse_since(args.since), args.dir,
                     ZoneInfo(args.pi_tz), chunk=args.chunk, report=print)
    except (OSError, ValueError) as exc:
        print(f"bench.pull: {exc}", file=sys.stderr)
        return 3
    return 4 if any(n.problem for n in state["nodes"].values()) else 0


if __name__ == "__main__":
    sys.exit(main())
