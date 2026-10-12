"""Decide a scenario from the log collector's record, so a long run does not need the host.

The collector is a server that stores every Pi Node's UART log.
`bench flash` arms the boards in a few minutes; the host can then be switched off, the collector keeps
recording, and this module decides the scenario from its lines with the same engine and the same expect file
as `bench run` (`expect_spec`, arclog's `Run`).

    python -m bench.collector scenarios/x.toml --collector http://HOST:PORT --build ID \\
        --since 2026-10-07T08:40:00Z --node 2=nuna-node-03 --node 5=nuna-node-01 --dir tools/arclog/runs/<run>

It does two things, each of which can be done alone. `bench.pull` copies the collector's lines into the run
folder, paged and append-only (a second call adds only what is new); this module judges the folder, streaming it,
so a record of days costs memory for the expectations and not for the run. `--no-pull` judges the folder as it is.

What it prints is bounded: the verdict, one line per node, the first few causes. The stage-by-stage detail goes to
`judge.log` in the folder (`--verbose` prints it too). An agent decides a run from the summary and never reads
the record.

Exit code: 0 pass, 1 fail, 2 no verdict yet (nothing failed so far, or the run timed out undecided),
3 an invalid scenario or argument, 4 the record is invalid: a bench fault (see `collector_causes`), or an
export that is not whole (a pull that is not complete, a collector that no longer holds the start of the run).
An unfit record is not judged: its verdict would be about the export and not about the nodes.
"""

from __future__ import annotations

import argparse
import json
import re
import statistics
import sys
from collections import Counter
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from zoneinfo import ZoneInfo

from arclog.expect import Run, parse_since, spec_from_dict
from arclog.health import SeqTracker
from arclog.model import Line, format_host_time
from arclog.schema import events_from_dict
from arclog.stream import StreamStats, merged_lines
from arclog.validity import Cause, SilenceWatch, TraceAudit, exit_code, invalidating

from bench.pull import (SCHEMA, PullError, add_record_arguments, http_get, load_state, node_pairs, pull,
                        schema_from_git)
from bench.run import expect_spec
from bench.scenario import Scenario, load

JUDGE_LOG = "judge.log"
# The run is armed by the BOOTs after `bench flash`, minutes after `since`.
SMOKE_WINDOW = "15m"
# Clock offsets beyond this between a Pi and the collector shift every time of the run.
CLOCK_SKEW_S = 5.0
# A node that logs a slot every 20 s and has been quiet this long has stopped.
SILENT_WARN_S = 600.0
# The summary shows this many causes and says how many more the detail holds.
MAX_SHOWN = 5
# A gap in the sequence numbers this close after a chunk boundary of the pull is taken for the export's.
BOUNDARY_S = 2.0
# The summary keeps a verdict's reason to this long; the detail has it whole.
CLIP = 300


def node_number(name: str) -> int:
    """The collector's number of a node: nuna-node-03 is 3."""
    m = re.search(r"(\d+)$", name)
    if m is None:
        raise ValueError(f"{name!r} does not end with a node number")
    return int(m[1])


def pi_clock_states(base: str | None, names: list[str]) -> dict[str, dict]:
    """The collector's view of the clock of each named Pi Node (`pi_ntp`, `clock_lag_s`), for a manifest.

    Nothing when there is no collector or it cannot be asked: the manifest then has no Pi clock to compare."""
    if not base:
        return {}
    try:
        status = json.loads(http_get(f"{base.rstrip('/')}/nodes", timeout=15.0))
    except (OSError, ValueError):
        return {}
    return {name: {k: status[name][k] for k in ("pi_ntp", "clock_lag_s") if k in status[name]}
            for name in names if name in status}


def quiet_notes(names: list[str], status: dict) -> list[str]:
    """A heads-up for a node the collector is connected to and has heard nothing from for a while. Not a cause:
    only a scenario's `max_silence` makes silence fail a run."""
    return [f"{name}: the collector has seen no line from it for {n['silent_for_s']:.0f} s while connected to its Pi"
            f" (set max_silence in the scenario to fail the run on it)"
            for name in names
            for n in [status.get(name) or {}]
            if n.get("connected") and n.get("silent_for_s") is not None and n["silent_for_s"] > SILENT_WARN_S]


def collector_causes(names: list[str], status: dict, since: datetime, lost: dict[str, int]) -> list[Cause]:
    """What makes the collector's record of the run unfit to use, as causes (`Cause.fault`).

    `status` is the collector's /nodes; `lost` is the lines each node lost, by the trace's sequence numbers.
    """
    causes = []
    for name in names:
        n = status.get(name)
        if n is None:
            causes.append(Cause("collector_node", f"{name}: the collector does not know this node", name))
            continue
        if not n.get("connected"):
            causes.append(Cause("collector_node", f"{name}: the collector is not connected to it now "
                                f"({n.get('last_error')})", name))
        started = n.get("connected_since")
        if started and datetime.fromisoformat(started) > since and lost.get(name):
            causes.append(Cause("collector_gap", f"{name}: the collector reconnected at {started}, after the run "
                                f"started, and {lost[name]} lines lost (the Pi replayed "
                                f"{n.get('recovered_from_replay', 0)} time(s) what it had buffered)", name))
        if n.get("pi_ntp") not in (None, "synced"):
            causes.append(Cause("pi_clock", f"{name}: the Pi's clock is not synchronised ({n['pi_ntp']}): "
                                "times are shifted", name))
        lag = n.get("clock_lag_s")
        if lag is not None and abs(lag) > CLOCK_SKEW_S:
            causes.append(Cause("pi_clock", f"{name}: its clock lags the collector's by {lag:+.1f} s: "
                                "times are shifted", name))
    return causes


def export_causes(names: list[str], nodes: dict) -> list[Cause]:
    """What makes the pulled folder not a whole record of the run: a node that was not pulled, a pull that did not
    finish, a collector that no longer held the start of the run or that started its storage over.

    These say nothing about the firmware, and a verdict on such a record would be a verdict on the export (a
    missing `BOOT` is not a node that did not boot), so the folder is not judged."""
    causes = []
    for name in names:
        n = nodes.get(name)
        if n is None:
            causes.append(Cause("collector_export", f"{name}: not in this folder, pull it first", name))
        elif n.problem:
            causes.append(Cause("collector_export", f"{name}: {n.problem}", name))
        elif n.complete_to is None:
            causes.append(Cause("collector_export", f"{name}: the pull did not finish, lines are still to come", name))
    return causes


def _at_boundary(cause: Cause, boundaries: list[str]) -> bool:
    """Whether a firmware cause sits right after a place where the pull cut the collector's lines into chunks."""
    if cause.start is None:
        return False
    for b in boundaries:
        if timedelta(0) <= cause.start - datetime.fromisoformat(b.replace("Z", "+00:00")) <= timedelta(seconds=BOUNDARY_S):
            return True
    return False


def order_causes(stats: StreamStats) -> list[Cause]:
    return [Cause("collector_order", f"{node}: {n} line(s) arrive more than the replay buffer's hour out of order "
                  f"(first at {stats.first_late[node]:%H:%M:%S} UTC): the record cannot be put in time order", node)
            for node, n in sorted(stats.late.items())]


class NodeStats:
    """What the summary says of one node, gathered line by line."""

    def __init__(self, name: str, node_id: int | None, since: datetime) -> None:
        self.name = name
        self.node_id = node_id
        self.since = since
        self.lines = 0
        self.first: datetime | None = None
        self.last: datetime | None = None
        self.boots: list[str] = []              # the first CM0+ BOOTs, as "HH:MM:SS cls build"
        self.last_boot: str | None = None
        self.boot_count = 0
        self.slots: Counter[int] = Counter()

    def feed(self, line: Line) -> None:
        self.lines += 1
        t = line.host_time
        self.first = self.first or t
        self.last = t
        if line.event == "BOOT" and line.core == "0":
            self.boot_count += 1
            self.last_boot = f"{t:%H:%M:%S} {line.fields.get('cls', '')} {line.fields.get('build', '')}".strip()
            if len(self.boots) < 20:
                self.boots.append(self.last_boot)
        elif line.event == "SLOT":
            self.slots[max(0, int((t - self.since) / timedelta(hours=1)))] += 1

    def per_hour(self) -> list[int]:
        return [self.slots.get(h, 0) for h in range(max(self.slots) + 1)] if self.slots else []

    def per_hour_summary(self) -> str:
        """SLOT per hour as min / median / max over the whole hours (the arming hour and the last, partial, one
        are left out once there are enough); the list itself is in the detail."""
        hours = self.per_hour()
        if not hours:
            return "no SLOT"
        whole = hours[1:-1] if len(hours) > 3 else hours
        return f"SLOT/h min {min(whole)} med {statistics.median(whole):g} max {max(whole)}"

    def summary(self, now: datetime) -> str:
        label = self.name + (f" (Node {self.node_id})" if self.node_id is not None else "")
        if self.last is None:
            return f"{label}: no line since {format_host_time(self.since)}"
        boots = f"{self.boot_count} BOOT" + (f", last {self.last_boot}" if self.boot_count else "")
        return (f"{label}: {self.lines} lines, last {self.last:%H:%M:%S}Z ({(now - self.last).total_seconds() / 60:.0f}"
                f" min ago), {boots}, {self.per_hour_summary()}")

    def detail(self, now: datetime) -> list[str]:
        out = [self.summary(now)]
        if self.first is not None:
            more = "" if self.boot_count <= len(self.boots) else f" (first {len(self.boots)} of {self.boot_count})"
            out.append(f"    first line {format_host_time(self.first)}; CM0+ BOOT{more}: {'; '.join(self.boots) or 'none'}")
            out.append(f"    SLOT per hour from the start: {self.per_hour()}")
        return out


def _clip(text: str, n: int = CLIP) -> str:
    return text if len(text) <= n else text[:n - 1] + "..."


def judge(s: Scenario, build: str, names: dict[int, str], since: datetime, directory: Path,
          flashed: dict[int, datetime] | None = None, now: datetime | None = None, report=print,
          verbose: bool = False, until: datetime | None = None) -> int:
    """Decide the scenario from the pulled folder, report a summary of fixed size, write the detail to `judge.log`;
    the exit code. `until` ends the run when the folder holds a later one of the same nodes (it is not guessed
    from the scenario's timeout: a node that keeps running past it is still the run)."""
    now = now or datetime.now(timezone.utc)
    directory = Path(directory)
    state = load_state(directory)
    if state is None:
        raise PullError(f"{directory} is not a pulled record (no pull.json): run bench.pull first, into a new folder")
    if state["build"] != build:
        raise PullError(f"{directory} was pulled for build {state['build']}, not {build}")
    nodes = {n: names[n] for n in s.nodes}
    names_list = list(nodes.values())
    boards = {nid: SimpleNamespace(node=name) for nid, name in nodes.items()}
    detail: list[str] = [f"judge {format_host_time(now)}: build {build}, since {format_host_time(since)}, "
                         f"nodes {', '.join(f'{i}={n}' for i, n in nodes.items())}"]
    summary: list[str] = []

    causes = export_causes(names_list, state["nodes"])
    if causes:
        return _finish(directory, summary, detail, causes, s, None, [], "", state, now, report, verbose, judged=False)

    events, schema_note = _schema(directory, state)
    d = expect_spec(s, boards, build, flashed)
    d["smoke_window"] = SMOKE_WINDOW
    spec = spec_from_dict(d)

    final: list[str] = []

    def say(msg: str) -> None:
        detail.append(msg)
        if msg.startswith(("PASS", "FAIL", "TIMEOUT")):
            final.append(msg)

    end = min([now] + ([until] if until else []) +
              [datetime.fromisoformat(n.complete_to.replace("Z", "+00:00"))
               for name, n in state["nodes"].items() if name in nodes.values()])
    # A run of hours counts every slot: only the count that completes an expectation is news.
    run = Run(spec, since, report=say, events=events, progress=False)
    audit = TraceAudit(spec, since, events=events)
    silence = SilenceWatch(names_list, s.max_silence, since, end) if s.max_silence is not None else None
    ids = {name: nid for nid, name in nodes.items()}
    stats = {name: NodeStats(name, ids.get(name), since) for name in names_list}
    seq = SeqTracker()
    lost: Counter[str] = Counter()
    order = StreamStats()
    for line in merged_lines(directory, names_list, since, stats=order):
        if until is not None and line.host_time > until:
            break
        if run.verdict is None and run.tick(line.host_time) is None:
            run.feed(line)
            run.tick(line.host_time)
        audit.feed(line)
        stats[line.node].feed(line)
        lost[line.node] += seq.feed(line).lost
        if silence is not None:
            silence.feed(line)
    verdict = run.verdict or run.tick(max([end] + [st.last for st in stats.values() if st.last]))

    status = {name: n.status for name, n in state["nodes"].items() if name in names_list and n.status}
    causes = collector_causes(names_list, status, since, dict(lost))
    boundaries = {name: n.boundaries for name, n in state["nodes"].items()}
    for cause in audit.causes():
        if cause.kind == "lost_lines" and _at_boundary(cause, boundaries.get(cause.node, [])):
            cause = Cause("collector_export", f"{cause.text}, right after a chunk boundary of the pull: "
                          "the export lost it, not the firmware (pull into a new folder to see)", cause.node,
                          cause.start, cause.end)
        causes.append(cause)
    if audit.dropped:
        detail.append(f"{audit.dropped} more firmware event(s) of the kinds above were left out of the list")
    causes += order_causes(order)
    if silence is not None:
        causes += silence.causes()
    else:
        causes += [Cause("quiet", note, None, fault="note") for note in quiet_notes(names_list, status)]
    if schema_note:
        causes.append(Cause("schema", schema_note, None, fault="note"))
    for st in stats.values():
        detail += st.detail(end)
        summary.append(st.summary(end))
    pending = run.pending() if verdict is None else []
    detail += [f"waiting for {p}" for p in pending]
    waiting = f"; waiting for {pending[0]}" + (f" (+{len(pending) - 1} more)" if len(pending) > 1 else "") if pending else ""
    return _finish(directory, summary, detail, causes, s, verdict, final, waiting, state, end, report, verbose,
                   judged=True)


def _schema(directory: Path, state: dict) -> tuple[dict | None, str | None]:
    """The event table the record is checked with: that of the build (kept in the folder), else the checkout's, said."""
    record = state.get("schema", {})
    if str(record.get("source", "")).startswith("commit"):
        try:
            return events_from_dict(json.loads((directory / SCHEMA).read_text(encoding="utf-8"))), None
        except (OSError, ValueError, KeyError):
            return None, f"schema: {SCHEMA} of the build is missing or unreadable, the checkout's schema is used"
    return None, f"schema: the checkout's is used ({record.get('why', 'no schema was kept with the record')})"


def _finish(directory: Path, summary: list[str], detail: list[str], causes: list[Cause], s: Scenario, verdict,
            final: list[str], waiting: str, state: dict, end: datetime, report, verbose: bool, judged: bool) -> int:
    """The summary (fixed size), the detail file, the exit code."""
    out: list[str] = []
    code = exit_code(verdict.code if verdict else 2, causes, s.dataset)
    shown = [c for c in causes if c.fault != "note"]
    if not judged:
        out.append(f"INVALID the record is not whole: {len(shown)} cause(s), so it is not judged")
    elif verdict is not None:
        out.append(_clip(final[-1] if final else f"{verdict.label} {verdict.reason}"))
    else:
        out.append(f"NO VERDICT YET at {format_host_time(end)}: nothing has failed so far{waiting}")
    out += summary
    for cause in causes:
        detail.append(f"WARN {cause.text}")
    out += [f"WARN {_clip(c.text)}" for c in causes[:MAX_SHOWN]]
    if len(causes) > MAX_SHOWN:
        out.append(f"WARN +{len(causes) - MAX_SHOWN} more cause(s) in {JUDGE_LOG}")
    if invalidating(causes, s.dataset) and judged:
        out.append(f"INVALID {len(invalidating(causes, s.dataset))} cause(s) make the data unfit to use: a bench "
                   f"fault, or a firmware event in a dataset session (verdict: "
                   f"{verdict.label if verdict else 'none yet'})")
    nodes = state["nodes"]
    out.append(f"record: {len(nodes)} node(s), {sum(n.lines for n in nodes.values())} lines in the folder, "
               f"schema {state['schema'].get('source')}, detail in {directory / JUDGE_LOG}")
    detail.append("exit " + str(code))
    (directory / JUDGE_LOG).write_text("\n".join(detail) + "\n", encoding="utf-8")
    for line in out:
        report(line)
    if verbose:
        for line in detail:
            report(line)
    return code


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="python -m bench.collector", description=__doc__.split("\n\n")[0])
    p.add_argument("scenario", help="scenario file (TOML)")
    add_record_arguments(p)
    p.add_argument("--flashed", action="append", default=[], metavar="ID=ISO",
                   help="when a node's flash ended, to ignore its old image's lines; repeat")
    p.add_argument("--until", help="ISO UTC time the run ended, when the folder holds a later run of the same nodes")
    p.add_argument("--no-pull", action="store_true", help="judge the folder as it is, without asking the collector")
    p.add_argument("--verbose", action="store_true", help="print the detail too (it is always in judge.log)")
    args = p.parse_args(argv)
    try:
        s = load(args.scenario)
        names = node_pairs(args.node, "--node")
        missing = [nid for nid in s.nodes if nid not in names]
        if missing:
            raise ValueError(f"--node is missing for Node ID {', '.join(map(str, missing))}")
        flashed = {nid: parse_since(v) for nid, v in node_pairs(args.flashed, "--flashed").items()}
        since = parse_since(args.since)
        if not args.no_pull:
            if not args.collector:
                raise ValueError("no collector: pass --collector or set BENCH_COLLECTOR_URL, or --no-pull")
            pull(args.collector.rstrip("/"), [names[n] for n in s.nodes], args.build, since, args.dir,
                 ZoneInfo(args.pi_tz), chunk=args.chunk, schema_source=schema_from_git)
        return judge(s, args.build, names, since, args.dir, flashed or None, verbose=args.verbose,
                     until=parse_since(args.until) if args.until else None)
    except (OSError, ValueError) as exc:
        print(f"bench.collector: {exc}", file=sys.stderr)
        return 3


if __name__ == "__main__":
    sys.exit(main())
