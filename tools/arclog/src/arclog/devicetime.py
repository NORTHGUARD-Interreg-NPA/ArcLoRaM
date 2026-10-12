"""The lines of all nodes on one time axis: the C3's device time (issue #85).

Three clocks stamp a line:

- **controller time**: when the host received it. It follows the 9600-baud transmission of the line and the trace
  queue, so the same event differs by a median of 75 ms (69 to 216 ms) between two nodes (2026-10-04, eight Sync
  packets of a C3 and a locked C2). Good for "roughly when"; never for the order of two nodes' events.
- **device time**: the RTC reading the node logged with the event, taken when the event is logged. Of a locked C2 it
  is the C3's within a few milliseconds (2.4 to 4.4 ms on the same eight packets).
- **protocol time**: the epoch, phase and cell of the Sync schedule (`ep`, `ph`, `ce`), counted from the Sync Phase
  Epoch the C3 captured. It names a packet, and a slot, not a moment of the lab.

The reference axis is the C3's device time. A node is on it only while it is locked to the C3:

- the **C3 itself** is on it from its BOOT on;
- any other node from its `CLK to=WARM` on, until it leaves WARM, reboots, or the C3 reboots (the C3's axis
  restarts at every boot: a new axis, a new `segment`);
- before that, a node's clock is its own, and its lines are placed by controller time, **marked**
  (`basis == "host"`).

A clock that jumps (`RTC_SET`, `d=` the jump in ms, reported modulo a day) is bridged: the lines logged before
the jump, in the same interval, are moved by the jump, so the interval reads as one continuous clock, the one the
node was set to.
"""

from __future__ import annotations

import statistics
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Mapping

from arclog.model import Kind, Line

#: A locked line this far (s) from where the controller saw it is not trusted: it is placed by controller time.
SANITY_S = 10.0
_DAY = timedelta(days=1)
_MIN = datetime.min


@dataclass
class Placed:
    """A line and its place on the reference axis."""

    line: Line
    time: datetime | None       #: on the C3's device axis (naive, the device calendar), None before any time is known
    basis: str                  #: "device": the node's own clock, locked (bridged over jumps); "host": controller time
    segment: int                #: which boot of the reference the axis belongs to
    order: tuple[int, int] = (0, 0)     #: (node order, position in its stream): breaks ties


def _core0(line: Line, event: str) -> bool:
    return line.kind is Kind.ARCLOG and line.core == "0" and line.event == event


def reference_node(streams: Mapping[str, list[Line]]) -> str:
    """The first node whose CM0+ BOOT says it is a C3."""
    for node, lines in streams.items():
        if any(_core0(ln, "BOOT") and ln.get("cls") == "C3" for ln in lines):
            return node
    raise ValueError("no C3 BOOT in the capture: name the reference node with --ref")


def _boots(lines: list[Line]) -> list[list[Line]]:
    """The stream cut at every BOOT; a boot has at most one BOOT line per core (CM4's comes first)."""
    out: list[list[Line]] = [[]]
    seen: set[str] = set()
    for ln in lines:
        if ln.kind is Kind.ARCLOG and ln.event == "BOOT":
            if ln.core in seen:
                out.append([])
                seen = set()
            seen.add(ln.core)
        out[-1].append(ln)
    return [seg for seg in out if seg]


def _host_naive(line: Line) -> datetime | None:
    return line.host_time.replace(tzinfo=None) if line.host_time else None


def _jump(rtc: Line, previous: Line | None) -> timedelta:
    """The step an RTC_SET made, from its `d` (ms, reported modulo a day): the whole days are told by where
    the clock was just before, which is the line before it plus the controller time between the two."""
    d = timedelta(milliseconds=rtc.int("d", 0) or 0)
    if previous is None or previous.dev_time is None or rtc.dev_time is None or not (previous.host_time and rtc.host_time):
        return d
    expected_old = previous.dev_time + (rtc.host_time - previous.host_time)
    assumed_old = rtc.dev_time - d
    return d + round((assumed_old - expected_old) / _DAY) * _DAY


def _bridge(interval: list[Line]) -> list[timedelta]:
    """For each line of a continuous interval, what to add to its device time: the sum of the jumps after it."""
    add = [timedelta(0)] * len(interval)
    last = timedelta(0)
    for i in range(len(interval) - 1, -1, -1):
        add[i] = last
        ln = interval[i]
        if _core0(ln, "RTC_SET"):
            previous = next((interval[j] for j in range(i - 1, -1, -1) if interval[j].dev_time is not None), None)
            last += _jump(ln, previous)
    return add


@dataclass
class _Axis:
    """One boot of the reference: its place in time, and the offset between its device axis and controller time."""

    start: datetime | None      #: host time of its first line
    offset: timedelta | None    #: device axis minus controller time, median over the boot


def _axes(lines_by_boot: list[list[Line]]) -> list[_Axis]:
    axes = []
    for boot in lines_by_boot:
        add = _bridge(boot)
        diffs = [(ln.dev_time + a - _host_naive(ln)).total_seconds()
                 for ln, a in zip(boot, add) if ln.dev_time is not None and ln.host_time is not None]
        start = next((ln.host_time for ln in boot if ln.host_time), None)
        axes.append(_Axis(start, timedelta(seconds=statistics.median(diffs)) if diffs else None))
    return axes


def _segment_at(axes: list[_Axis], host: datetime | None) -> int:
    seg = 0
    for i, ax in enumerate(axes):
        if ax.start is not None and host is not None and host >= ax.start:
            seg = i
    return seg


def _by_host(axes: list[_Axis], line: Line, hint: int) -> tuple[datetime | None, int]:
    """Controller time on the axis of the reference boot the line falls in."""
    host = line.host_time
    seg = _segment_at(axes, host) if host else hint
    offset = axes[seg].offset
    if host is None or offset is None:
        return None, seg
    return _host_naive(line) + offset, seg


def align(streams: Mapping[str, list[Line]], ref: str | None = None) -> list[Placed]:
    """Every line of every node placed on the C3's device time, ordered; see the module doc.

    `streams` maps a node name to its lines in capture order. `ref` names the C3 (default: the first node that
    booted as one)."""
    if ref is None:
        ref = reference_node(streams)
    elif ref not in streams:
        raise ValueError(f"reference node {ref!r} is not in the capture ({', '.join(streams)})")
    axes = _axes(_boots(streams[ref]))
    boot_ends = [ax.start for ax in axes[1:]]               # host time at which the next boot of the reference starts
    placed: list[Placed] = []
    for n, node in enumerate(streams):
        lines = streams[node]
        if node == ref:
            placed += _place_reference(lines, axes, n)
        else:
            placed += _place_follower(lines, axes, boot_ends, n)
    placed.sort(key=lambda p: (p.segment, p.time or _MIN, p.order))
    return placed


def _place_reference(lines: list[Line], axes: list[_Axis], n: int) -> list[Placed]:
    out: list[Placed] = []
    index = 0
    for boot_no, boot in enumerate(_boots(lines)):
        add = _bridge(boot)
        for ln, a in zip(boot, add):
            if ln.dev_time is not None:
                out.append(Placed(ln, ln.dev_time + a, "device", boot_no, (n, index)))
            else:
                t, _ = _by_host(axes, ln, boot_no)
                out.append(Placed(ln, t, "host", boot_no, (n, index)))
            index += 1
    return _inherit(out)


def _place_follower(lines: list[Line], axes: list[_Axis], boot_ends: list[datetime | None], n: int) -> list[Placed]:
    out: list[Placed] = []
    index = 0
    for boot in _boots(lines):
        for interval, locked in _intervals(boot):
            segment = _segment_at(axes, next((ln.host_time for ln in interval if ln.host_time), None))
            add = _bridge(interval) if locked else [timedelta(0)] * len(interval)
            for ln, a in zip(interval, add):
                placed = _place_one(ln, a, locked, segment, axes, boot_ends, n, index)
                out.append(placed)
                index += 1
    return _inherit(out)


def _place_one(ln: Line, add: timedelta, locked: bool, segment: int, axes: list[_Axis],
               boot_ends: list[datetime | None], n: int, index: int) -> Placed:
    host_t, host_seg = _by_host(axes, ln, segment)
    if locked and ln.dev_time is not None and host_t is not None:
        # The reference rebooted since the lock: this clock is not on the new axis.
        stale = any(end is not None and ln.host_time is not None and ln.host_time >= end and i >= segment
                    for i, end in enumerate(boot_ends))
        t = ln.dev_time + add
        if not stale and abs((t - host_t).total_seconds()) <= SANITY_S:
            return Placed(ln, t, "device", segment, (n, index))
    return Placed(ln, host_t, "host", host_seg, (n, index))


def _intervals(boot: list[Line]) -> list[tuple[list[Line], bool]]:
    """A boot cut into (lines, locked): unlocked up to CLK to=WARM, locked to the CLK that leaves WARM (inclusive)."""
    out: list[tuple[list[Line], bool]] = []
    current: list[Line] = []
    locked = False
    for ln in boot:
        if _core0(ln, "CLK") and not locked and ln.get("to") == "WARM":
            if current:
                out.append((current, False))
            current, locked = [], True
        current.append(ln)
        if _core0(ln, "CLK") and locked and ln.get("to") != "WARM":
            out.append((current, True))
            current, locked = [], False
    if current:
        out.append((current, locked))
    return out


def _inherit(placed: list[Placed]) -> list[Placed]:
    """A line with no time at all (garbage after a reset) takes the place of the line before it, and sorts after it."""
    last: Placed | None = None
    for p in placed:
        if p.time is None and last is not None:
            p.time, p.segment, p.basis = last.time, last.segment, "host"
        if p.time is not None:
            last = p
    return placed
