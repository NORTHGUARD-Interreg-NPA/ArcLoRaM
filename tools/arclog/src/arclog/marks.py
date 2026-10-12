"""Capture marks: a sidecar that says when each port was up and whether the host was running.

`arclog capture` writes <out>/marks-<YYYYMMDD>.log next to the daily files, one line per mark:

    <UTC>\\tmono=<host monotonic s>\\t<node>=<up|down>@<UTC of its last change>\\t...

A sidecar, because every tool reads '<UTC>\\t<line>' in the daily files and a third column would break them.
The marks show a port down (a capture gap, which a quiet node cannot be told from), a step of the host's
UTC against its monotonic clock (the time service), and a host that was not running (no marks).
Every 10 s one mark is written, and so is one at every change of a port: a link dropped and recovered between two
ticks would otherwise leave no trace.
"""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, TextIO

from arclog.model import format_host_time, parse_host_time

#: Seconds of the host's monotonic clock between two marks.
MARK_INTERVAL_S = 10.0


@dataclass
class PortMark:
    up: bool
    since: datetime        # UTC time of the port's last change


@dataclass
class Mark:
    utc: datetime
    mono: float
    ports: dict[str, PortMark]


def format_mark(mark: Mark) -> str:
    """One line of a marks file, without its newline."""
    ports = "\t".join(f"{node}={'up' if p.up else 'down'}@{format_host_time(p.since)}"
                      for node, p in mark.ports.items())
    return f"{format_host_time(mark.utc)}\tmono={mark.mono:.3f}\t{ports}"


class Marks:
    """Writes the marks of one capture process."""

    def __init__(self, out_dir: Path, nodes: list[str],
                 now: Callable[[], datetime] = lambda: datetime.now(timezone.utc),
                 monotonic: Callable[[], float] = time.monotonic) -> None:
        self.out_dir = out_dir
        self._now = now
        self._monotonic = monotonic
        start = now()
        self._ports = {node: PortMark(False, start) for node in nodes}
        self._file: TextIO | None = None
        self._date = ""
        self._last_mono: float | None = None
        self._lock = threading.Lock()  # the readers report from their threads, the capture ticks from its own
        out_dir.mkdir(parents=True, exist_ok=True)

    def port_up(self, node: str) -> None:
        self._set(node, True, None)

    def port_down(self, node: str, since: datetime | None = None) -> None:
        """`since` dates the outage to when the link was last known alive, which a reader finding it dead after
        some silence knows and `now` does not."""
        self._set(node, False, since)

    def _set(self, node: str, up: bool, since: datetime | None) -> None:
        """Only a change moves a port's `since`: a reader repeats its state at every retry. A change is a mark."""
        with self._lock:
            if self._ports[node].up != up:
                self._ports[node] = PortMark(up, since or self._now())
                self._write(self._monotonic())

    def tick(self) -> None:
        """Write a mark when one is due; the capture calls it often."""
        with self._lock:
            mono = self._monotonic()
            if self._last_mono is None or mono - self._last_mono >= MARK_INTERVAL_S:
                self._last_mono = mono
                self._write(mono)

    def _write(self, mono: float) -> None:
        t = self._now()
        date = t.strftime("%Y%m%d")
        if date != self._date:
            self._close()
            self._file = (self.out_dir / f"marks-{date}.log").open("a", encoding="utf-8", newline="\n")
            self._date = date
        self._file.write(format_mark(Mark(t, mono, dict(self._ports))) + "\n")
        self._file.flush()

    def close(self) -> None:
        with self._lock:
            self._close()

    def _close(self) -> None:
        if self._file is not None:
            self._file.close()
            self._file = None
            self._date = ""


def read_marks(path: Path) -> list[Mark]:
    """The marks of one file. A last line without its newline was cut by a killed capture: it is not a mark."""
    marks = []
    for text in path.read_text(encoding="utf-8").splitlines(keepends=True):
        if not text.endswith("\n"):
            break
        text = text.rstrip("\n")
        utc, mono, *ports = text.split("\t")
        parsed = {}
        for field in ports:
            node, _, rest = field.partition("=")
            up, _, since = rest.partition("@")
            parsed[node] = PortMark(up == "up", parse_host_time(since))
        marks.append(Mark(parse_host_time(utc), float(mono.removeprefix("mono=")), parsed))
    return marks
