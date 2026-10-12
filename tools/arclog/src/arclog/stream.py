"""A record folder read as a stream: its lines in time order, with memory bounded by a window and not by the run.

`DirFollower` returns every new line as a list, which is right for a capture that grows, and costs the whole run
in memory for a record of days (10 nodes for 3 days are about 500000 lines). A judge reads the record once, in
order, and keeps only what its expectations need: this module gives it the lines one by one.

The files of a node (<node>-YYYYMMDD.log) are read in date order. A pull appends what the collector sends in the
order it stored it, and the collector stores a line it recovered after a reconnect after the lines that came
live, so a recovered line can sit up to the Pi's replay buffer (an hour) before the lines beside it in the file.
A line is held back until it is older than the newest one by `window`, which puts such a line in place; one
older than a line already passed on is too late for that (a clock step, a larger gap), is passed on in the
order it comes, and is counted in `StreamStats`.
"""

from __future__ import annotations

import heapq
from collections import Counter
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path
from typing import Iterator

from arclog.expect import capture_files
from arclog.model import Line, parse_host_time, parse_line

#: How far a recovered line can sit before its neighbours: the Pi replays at most an hour of what it buffered.
REORDER = timedelta(minutes=70)


@dataclass
class StreamStats:
    """What the reader found wrong with the order of a record, per node."""

    late: Counter = field(default_factory=Counter)             # node -> lines older than one already passed on
    first_late: dict[str, datetime] = field(default_factory=dict)  # node -> time of its first such line


def node_lines(directory: str | Path, node: str, since: datetime, window: timedelta = REORDER,
               stats: StreamStats | None = None) -> Iterator[Line]:
    """The lines of one node at or after `since`, in time order, as long as none is more than `window` out of place."""
    stats = stats if stats is not None else StreamStats()
    held: list[tuple[datetime, int, str]] = []   # (host time, order of arrival, the device line)
    count = 0
    newest = passed = None
    for path in capture_files(directory, node, since):
        with path.open("r", encoding="utf-8", errors="replace", newline="\n") as f:
            for raw in f:
                if not raw.endswith("\n"):
                    continue  # half written: the next read has it whole
                head, tab, device = raw.partition("\t")
                t = parse_host_time(head) if tab else None
                if t is None or t < since:
                    continue
                if passed is not None and t < passed:
                    stats.late[node] += 1
                    stats.first_late.setdefault(node, t)
                    yield parse_line(device, node=node, host_time=t)
                    continue
                heapq.heappush(held, (t, count, device))
                count += 1
                if newest is None or t > newest:
                    newest = t
                while held and held[0][0] <= newest - window:
                    passed, _, text = heapq.heappop(held)
                    yield parse_line(text, node=node, host_time=passed)
    while held:
        t, _, text = heapq.heappop(held)
        yield parse_line(text, node=node, host_time=t)


def merged_lines(directory: str | Path, nodes: list[str], since: datetime, window: timedelta = REORDER,
                 stats: StreamStats | None = None) -> Iterator[Line]:
    """The lines of all `nodes` at or after `since` in one stream, in time order (`node_lines` for each)."""
    stats = stats if stats is not None else StreamStats()
    streams = [node_lines(directory, node, since, window, stats) for node in nodes]
    return heapq.merge(*streams, key=lambda line: line.host_time)

