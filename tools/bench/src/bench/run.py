"""bench run: flash a scenario's boards, fire its actions, decide it from the capture, keep a record."""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Callable

from arclog.capture import DailyWriter
from arclog.expect import DirFollower, Run, condition_matches, spec_from_dict
from arclog.model import Kind, Line

from bench.boards import Board
from bench.scenario import WATCH, Action, Pattern, Scenario, plan


def expect_spec(s: Scenario, boards: dict[int, Board], build_id: str,
                flashed: dict[int, datetime] | None = None) -> dict:
    """The arclog expect file of a scenario, with Node IDs turned into capture node names."""
    node = {nid: boards[nid].node for nid in s.nodes}
    d: dict = {
        "timeout": s.timeout.total_seconds(),
        "build": build_id,
        "nodes": {},
        "expect": [],
        "forbid": [],
    }
    for nid, cls in s.nodes.items():
        d["nodes"][node[nid]] = ({"flashed": False} if cls == WATCH
                                 else {"cls": cls, "reboots": s.reboots(nid)})
        if flashed and nid in flashed:
            d["nodes"][node[nid]]["since"] = flashed[nid].isoformat()
    for e in s.expects:
        t = {"event": e.event, "where": e.where, "count": e.count}
        if e.node is not None:
            t["node"] = node[e.node]
        if e.within is not None:
            t["within"] = e.within.total_seconds()
        d["expect"].append(t)
    for nid, cls in s.flashed.items():
        # A run cannot pass before its resets: each one must reboot its node (one CM0+ BOOT each).
        if s.reboots(nid):
            d["expect"].append({"node": node[nid], "event": "BOOT", "where": {"cls": cls},
                                "count": s.reboots(nid)})
    for f in s.forbids:
        t = {"event": f.event, "where": f.where}
        if f.node is not None:
            t["node"] = node[f.node]
        d["forbid"].append(t)
    return d


def _matches(p: Pattern, line: Line, capture_node: dict[int, str]) -> bool:
    return (line.kind is Kind.ARCLOG and line.event == p.event
            and (p.node is None or line.node == capture_node[p.node])
            and all(condition_matches(c, line.fields.get(k)) for k, c in p.where.items()))


@dataclass
class Scheduler:
    """Decides when each action fires: at an offset from arming, or a delay after a trace event."""

    actions: list[Action]
    capture_node: dict[int, str]
    due_at: dict[int, datetime] = field(default_factory=dict)   # action index -> fire time
    fired: dict[int, datetime] = field(default_factory=dict)

    def observe(self, line: Line, armed_at: datetime | None) -> None:
        if armed_at is None or line.host_time is None or line.host_time < armed_at:
            return
        for i, a in enumerate(self.actions):
            if a.after is not None and i not in self.due_at and _matches(a.after, line, self.capture_node):
                self.due_at[i] = line.host_time + a.delay

    def due(self, now: datetime, armed_at: datetime | None) -> list[int]:
        if armed_at is None:
            return []
        for i, a in enumerate(self.actions):
            if a.at is not None and i not in self.due_at:
                self.due_at[i] = armed_at + a.at
        return [i for i, t in sorted(self.due_at.items()) if i not in self.fired and now >= t]


@dataclass
class RunOutcome:
    code: int                  # 0 pass, 1 fail, 2 timeout
    verdict: str
    messages: list[str]
    fired: dict[int, datetime]
    started: datetime
    ended: datetime


def follow(s: Scenario, boards: dict[int, Board], build_id: str, capture_dir: Path, since: datetime,
           reset: Callable[[str], None], report: Callable[[str], None] = print,
           flashed: dict[int, datetime] | None = None,
           now: Callable[[], datetime] = lambda: datetime.now(timezone.utc),
           sleep: Callable[[float], None] = time.sleep, poll_s: float = 1.0) -> RunOutcome:
    """Follow the capture from `since` (the flash): fire the actions, return the verdict."""
    messages: list[str] = []

    def say(msg: str) -> None:
        messages.append(msg)
        report(msg)

    capture_node = {nid: boards[nid].node for nid in s.nodes}
    run = Run(spec_from_dict(expect_spec(s, boards, build_id, flashed)), since, report=say)
    sched = Scheduler(s.actions, capture_node)
    follower = DirFollower(capture_dir, list(capture_node.values()), since)
    while True:
        for line in sorted(follower.poll(), key=lambda ln: ln.host_time):
            if run.tick(line.host_time) is not None:
                break
            run.feed(line)
            sched.observe(line, run.armed_at)
            run.tick(line.host_time)
        t = now()
        for i in sched.due(t, run.armed_at):
            a = s.actions[i]
            say(f"act  {a.describe()}: reset Node ID {a.reset} ({boards[a.reset].port})")
            reset(boards[a.reset].sn)
            sched.fired[i] = t
        verdict = run.tick(t)
        if verdict is not None:
            for i, a in enumerate(s.actions):
                if i not in sched.fired:
                    say(f"note action not fired: {a.describe()}")
            return RunOutcome(verdict.code, f"{verdict.label} {verdict.reason}", messages,
                              sched.fired, since, t)
        sleep(poll_s)


# ---------------------------------------------------------------------------
# Record
# ---------------------------------------------------------------------------


def slice_capture(capture_dir: Path, out_dir: Path, nodes: list[str], start: datetime, end: datetime) -> None:
    """Copy each node's capture lines of the run window, in the capture file format (arclog report reads them)."""
    follower = DirFollower(capture_dir, nodes, start)
    writers: dict[str, DailyWriter] = {}
    for line in follower.poll():
        if line.host_time <= end:
            writers.setdefault(line.node, DailyWriter(out_dir, line.node)).write(line.host_time, line.raw)
    for w in writers.values():
        w.close()


def write_record(out_dir: Path, scenario_text: str, s: Scenario, build_id: str, outcome: RunOutcome,
                 boards: dict[int, Board], others: list[Board], validity: list[str] | None = None,
                 invalid: bool = False) -> Path:
    """`validity` is the lines of the Validity section (see validate.format_validity); `invalid` opens the report
    with the cause and not with a verdict the data cannot back."""
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "scenario.toml").write_text(scenario_text, encoding="utf-8")
    (out_dir / "run.log").write_text("\n".join(outcome.messages) + "\n", encoding="utf-8")
    label = outcome.verdict.split(" ", 1)[0]
    if invalid:
        label = f"INVALID (verdict {label})"
    lines = [
        f"# bench run {outcome.started:%Y-%m-%d %H:%M:%S}Z: {label}",
        "",
        f"- Verdict: {outcome.verdict}",
        f"- Build ID: `{build_id}`",
        f"- Window: {outcome.started:%H:%M:%S} - {outcome.ended:%H:%M:%S} UTC "
        f"({(outcome.ended - outcome.started).total_seconds():.0f} s)",
        "",
        "## Boards",
        "",
        "| Node ID | Role | Port | Probe | Build |",
        "|---|---|---|---|---|",
    ]
    for nid, cls in sorted(s.nodes.items()):
        b = boards[nid]
        role, build = ("watched", b.build or "?") if cls == WATCH else (f"flashed as {cls}", build_id)
        lines.append(f"| {nid} | {role} | {b.port} | `{b.sn}` | `{build}` |")
    for b in others:
        lines.append(f"| {b.node_id if b.node_id is not None else '?'} | not in the scenario | {b.port} | "
                     f"`{b.sn}` | `{b.build or '?'}` |")
    if validity:
        lines += ["", "## Validity", "", *validity]
    lines += ["", "## Actions", ""]
    for i, a in enumerate(s.actions):
        when = f"fired {outcome.fired[i]:%H:%M:%S}" if i in outcome.fired else "not fired"
        lines.append(f"- {a.describe()}: {when}")
    if not s.actions:
        lines.append("- none")
    lines += ["", "## Plan", "", "```", plan(s), "```", "",
              "## Log", "", "```", *outcome.messages, "```", ""]
    report = out_dir / "report.md"
    report.write_text("\n".join(lines), encoding="utf-8")
    return report


def run_dir(runs_root: Path, started: datetime, build_id: str) -> Path:
    return runs_root / f"{started:%Y%m%dT%H%M%SZ}-{build_id}"


def elapsed(outcome: RunOutcome) -> timedelta:
    return outcome.ended - outcome.started
