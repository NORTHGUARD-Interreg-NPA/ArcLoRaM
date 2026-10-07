"""Validity of a capture session: what makes its data unfit to use, from the capture's own marks.

A cause is something the bench did wrong (a port down, a host that was not running, a clock step), not a
verdict on the firmware: a node that is silent while its port is up is not found here.
Other evidence (the log collector) produces causes of the same shape; the caller joins them.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta

from arclog.expect import Run, Spec
from arclog.marks import Mark
from arclog.model import Kind, Line

#: Exit code of a run whose data cannot be used (`bench run`, `bench validate`, the collector judge), apart from
#: pass (0), fail (1), timeout or no verdict yet (2) and an invalid scenario or argument (3).
EXIT_INVALID = 4

#: A port down for longer than this is a capture gap: a running node logs a line every few seconds.
PORT_DOWN_S = 10.0

#: UTC against the monotonic clock between two marks: a time service slews well below this, a step does not.
UTC_STEP_S = 1.0

#: Marks come every 10 s: none for longer than this means the capture, or the host, was not running.
NO_MARKS_S = 30.0


@dataclass(frozen=True)
class Cause:
    kind: str                       # port_down, utc_step, no_marks
    text: str                       # one line for a person, naming the node, interval or size
    node: str | None = None
    start: datetime | None = None
    end: datetime | None = None
    #: Whose fault it is: `bench` (the capture, the host, a clock, the collector: invalidates the run), `node` (a
    #: node silent while its port is up: a verdict on the firmware, it fails the run and does not invalidate it)
    #: or `firmware` (an unplanned reboot, lost lines: fails an acceptance run, invalidates a dataset session).
    fault: str = "bench"


def check_marks(marks: list[Mark], start: datetime, end: datetime) -> list[Cause]:
    """The causes that make the session between `start` and `end` (UTC) unfit, from the marks of its capture."""
    marks = [m for m in marks if start <= m.utc <= end]
    if not marks:
        return _uncovered(start, end)
    causes = _uncovered(start, marks[0].utc) + _uncovered(marks[-1].utc, end)
    for node, down_from, down_to in port_outages(marks):
        down_from = max(down_from, start)
        seconds = (down_to - down_from).total_seconds()
        if seconds > PORT_DOWN_S:
            causes.append(Cause("port_down", f"{node}: port down for {seconds:.0f} s "
                                f"({down_from:%H:%M:%S} - {down_to:%H:%M:%S} UTC)", node, down_from, down_to))
    for before, after in zip(marks, marks[1:]):
        elapsed = (after.utc - before.utc).total_seconds()
        silent = max(elapsed, after.mono - before.mono)  # a suspended host's monotonic clock may not count its sleep
        if silent > NO_MARKS_S:
            causes.append(_no_marks(before.utc, after.utc, silent))
            continue
        step = elapsed - (after.mono - before.mono)
        if abs(step) > UTC_STEP_S:
            causes.append(Cause("utc_step", f"the host's UTC was stepped {step:+.1f} s "
                                f"({before.utc:%H:%M:%S} - {after.utc:%H:%M:%S} UTC)", None, before.utc, after.utc))
    return causes


def exit_code(code: int, causes: list[Cause], dataset: bool) -> int:
    """The exit code of a run: invalid (4) if a cause invalidates it, else fail (1) if a node stopped while the
    engine saw nothing wrong yet or at its timeout, else the verdict's own code (0 pass, 1 fail, 2 timeout)."""
    if invalidating(causes, dataset):
        return EXIT_INVALID
    if code in (0, 2) and any(c.fault == "node" for c in causes):
        return 1
    return code


def _uncovered(before: datetime, after: datetime) -> list[Cause]:
    """A stretch with no mark at all (the edges of the window, or the whole of it)."""
    seconds = (after - before).total_seconds()
    return [_no_marks(before, after, seconds)] if seconds > NO_MARKS_S else []


def invalidating(causes: list[Cause], dataset: bool) -> list[Cause]:
    """The causes that make the data unfit: a bench fault always, a firmware event only in a dataset session
    (an acceptance run fails on it instead), and a node fault never (it is a verdict on the firmware)."""
    return [c for c in causes if c.fault == "bench" or (dataset and c.fault == "firmware")]


def _no_marks(before: datetime, after: datetime, seconds: float) -> Cause:
    return Cause("no_marks", f"no marks for {seconds:.0f} s: the capture or the host was not running "
                 f"({before:%H:%M:%S} - {after:%H:%M:%S} UTC)", None, before, after)


def port_outages(marks: list[Mark]) -> list[tuple[str, datetime, datetime]]:
    """(node, down since, up again) for every outage the marks show; one still on at the last mark ends there."""
    down: dict[str, datetime] = {}
    outages = []
    for m in marks:
        for node, port in m.ports.items():
            if not port.up:
                down.setdefault(node, port.since)
            elif node in down:
                outages.append((node, down.pop(node), port.since))
    outages += [(node, since, marks[-1].utc) for node, since in down.items()]
    return outages


#: The failures of the engine that are events of the firmware, not of the scenario: they are listed as causes.
_FIRMWARE_EVENTS = ("lost_lines", "unplanned_reboot", "wrong_build")


def trace_causes(spec: Spec, since: datetime, lines: list[Line]) -> list[Cause]:
    """Every firmware event that would fail the run (a reboot nobody planned, lost lines, a wrong Build ID), not
    only the first: the engine's own rules, read to the end."""
    run = Run(spec, since, keep_going=True)
    for line in sorted(lines, key=lambda ln: ln.host_time):
        run.feed(line)
    causes = [Cause(f.kind, f.reason, f.node, f.at, f.at, fault="firmware")
              for f in run.failures if f.kind in _FIRMWARE_EVENTS]
    return causes + _never_booted(spec, since, lines)


def _never_booted(spec: Spec, since: datetime, lines: list[Line]) -> list[Cause]:
    """A flashed board that never logged a BOOT of the expected build on both cores: the flash did not take."""
    if spec.build is None:
        return []
    causes = []
    for name, node in spec.nodes.items():
        if not node.flashed:
            continue
        start = max(since, node.since or since)
        boots = [ln for ln in sorted(lines, key=lambda ln: ln.host_time)
                 if ln.node == name and ln.kind is Kind.ARCLOG and ln.event == "BOOT"
                 and ln.host_time is not None and ln.host_time >= start]
        if {ln.core for ln in boots if ln.get("build") == spec.build} >= {"0", "4"}:
            continue
        last = f"last booted build={boots[-1].get('build') or '(none)'}" if boots else "no BOOT line"
        causes.append(Cause("wrong_build", f"{name} never booted build={spec.build} ({last})", name,
                            fault="firmware"))
    return causes


def silence_causes(lines: list[Line], nodes: list[str], max_silence: timedelta, start: datetime, end: datetime,
                   outages: list[tuple[str, datetime, datetime]] = ()) -> list[Cause]:
    """A node that logged nothing for longer than `max_silence` with no capture gap in it has stopped: a node fault.

    Gaps are read between a node's lines, from its last line to `end`, and over the whole window for a node that
    never logged. `outages` (node, from, to) are the capture's own (`port_outages`): a gap they touch is not the
    node's.
    """
    causes = []
    for node in nodes:
        times = sorted(ln.host_time for ln in lines
                       if ln.node == node and ln.host_time is not None and start <= ln.host_time <= end)
        gaps = list(zip(times, times[1:])) + [(times[-1], end)] if times else [(start, end)]
        for before, after in gaps:
            if after - before > max_silence and not any(
                    n == node and down_from < after and down_to > before for n, down_from, down_to in outages):
                causes.append(Cause("node_silent", f"{node}: no line for {(after - before).total_seconds():.0f} s "
                                    f"({before:%H:%M:%S} - {after:%H:%M:%S} UTC) with no capture gap in it",
                                    node, before, after, fault="node"))
    return causes
