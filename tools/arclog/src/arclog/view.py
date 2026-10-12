"""Filtering and human-readable rendering of ArcLog lines."""

from __future__ import annotations

import os
import sys
from dataclasses import dataclass, field
from typing import Iterable, Iterator, TextIO

from arclog.health import SeqTracker
from arclog.model import Kind, Line, level_allows
from arclog.schema import validate

RESET = "\033[0m"
DIM = "\033[2m"
BOLD = "\033[1m"
RED = "\033[31m"
YELLOW = "\033[33m"

MODULE_COLORS = {
    "T": "\033[36m",  # TDMA   cyan
    "M": "\033[35m",  # MAC    magenta
    "Y": "\033[32m",  # SYNC   green
    "R": "\033[34m",  # RADIO  blue
    "X": "\033[37m",  # MBMUX  white
    "S": "\033[1;37m",  # SYS  bold white
    "P": "\033[33m",  # POWER  yellow
}

#: Events worth highlighting whatever their module.
ALERT_EVENTS = {
    "SLOT_SUSPECT",
    "CURSOR_CORRUPT",
    "SYNC_SILENCE",
    "SYNC_REJ",
    "TX_TIMEOUT",
    "TX_DENIED",
    "RX_LATE",
    "RX_CAP",
}


def enable_windows_ansi() -> None:
    """Turn on ANSI escape processing in legacy Windows consoles."""
    if os.name == "nt":
        os.system("")


def use_color(stream: TextIO, requested: bool | None) -> bool:
    if requested is not None:
        return requested
    return stream.isatty() and os.environ.get("NO_COLOR") is None


@dataclass
class Filter:
    cores: set[str] = field(default_factory=set)      # {"0", "4"}
    mods: set[str] = field(default_factory=set)       # {"Y", "M"}
    max_level: str = "H"
    events: set[str] = field(default_factory=set)
    nodes: set[str] = field(default_factory=set)
    grep: str = ""
    show_legacy: bool = True
    show_raw: bool = True

    def accepts(self, line: Line) -> bool:
        if self.nodes and line.node not in self.nodes:
            return False
        if self.grep and self.grep not in line.raw:
            return False
        if line.kind is Kind.LEGACY:
            return self.show_legacy and not (self.mods or self.events)
        if line.kind is Kind.RAW:
            return self.show_raw and not (self.mods or self.events)
        if self.cores and line.core not in self.cores:
            return False
        if self.mods and line.mod not in self.mods:
            return False
        if self.events and line.event not in self.events:
            return False
        return level_allows(line.level, self.max_level)


def format_line(line: Line, color: bool = False, show_node: bool = True, note: str = "") -> str:
    """One display line: [node] time core+mod level #seq EVENT fields."""
    node = f"{line.node:<4} " if show_node and line.node else ""
    t = line.host_time.strftime("%H:%M:%S.%f")[:-3] if line.host_time else "--:--:--.---"

    if line.kind is Kind.ARCLOG:
        dev = line.dev_time.strftime("%y%m%d %H:%M:%S.%f")[:-2] if line.dev_time else "?"
        head = f"{line.core}{line.mod} {line.level} #{line.seq:02x}"
        body = " ".join(f"{k}={v}" for k, v in line.fields.items())
        text = f"{node}{t} {dev}  {head} {line.event:<14} {body}"
        if note:
            text += f"  {note}"
        if not color:
            return text
        mcol = MODULE_COLORS.get(line.mod, "")
        ev = line.event
        evcol = RED + BOLD if ev in ALERT_EVENTS or _is_loss(line) else BOLD
        return (
            f"{DIM}{node}{t} {dev}{RESET}  {mcol}{head}{RESET} "
            f"{evcol}{ev:<14}{RESET} {body}" + (f"  {YELLOW}{note}{RESET}" if note else "")
        )

    tag = "LEGACY" if line.kind is Kind.LEGACY else "RAW"
    text = f"{node}{t} {tag:<6} {line.text}"
    if note:
        text += f"  {note}"
    return f"{DIM}{text}{RESET}" if color else text


def format_placed(placed, color: bool = False, show_node: bool = True, note: str = "") -> str:
    """One line of `merge --by device`: [node] the time on the C3's device axis, a mark, then the line.

    The mark is blank when the node is locked (its own clock, on the axis) and `~` when the line is placed by
    controller time (before the lock, after a reboot, no usable device time)."""
    line = placed.line
    node = f"{line.node:<4} " if show_node and line.node else ""
    t = placed.time.strftime("%y%m%d %H:%M:%S.%f")[:-2] if placed.time else "------ --:--:--.----"
    mark = "~" if placed.basis == "host" else " "
    if line.kind is Kind.ARCLOG:
        head = f"{line.core}{line.mod} {line.level} #{line.seq:02x}"
        body = " ".join(f"{k}={v}" for k, v in line.fields.items())
        text = f"{node}{t} {mark} {head} {line.event:<14} {body}" + (f"  {note}" if note else "")
        return text if not color else f"{DIM}{node}{t} {mark}{RESET} {MODULE_COLORS.get(line.mod, '')}{head}{RESET} " \
            f"{(RED + BOLD) if line.event in ALERT_EVENTS or _is_loss(line) else BOLD}{line.event:<14}{RESET} {body}" \
            + (f"  {YELLOW}{note}{RESET}" if note else "")
    tag = "LEGACY" if line.kind is Kind.LEGACY else "RAW"
    text = f"{node}{t} {mark} {tag:<6} {line.text}" + (f"  {note}" if note else "")
    return f"{DIM}{text}{RESET}" if color else text


def _is_loss(line: Line) -> bool:
    return (line.event == "CLK" and line.get("to") == "COLD") or (
        line.event == "SYNC_RX" and line.get("act") == "t3"
    )


def annotate(lines: Iterable[Line]) -> Iterator[tuple[Line, str]]:
    """Attach health notes (lost lines, schema problems) to each line."""
    seq = SeqTracker()
    for line in lines:
        notes = []
        r = seq.feed(line)
        if r.lost:
            notes.append(f"!! {r.lost} line(s) lost before this one")
        if r.reordered:
            notes.append("(reordered)")
        notes.extend(f"!! {p}" for p in validate(line))
        yield line, " ".join(notes)


def render(
    lines: Iterable[Line],
    flt: Filter,
    out: TextIO | None = None,
    color: bool | None = None,
    show_node: bool = True,
) -> None:
    out = out or sys.stdout
    color = use_color(out, color)
    if color:
        enable_windows_ansi()
    hidden_notes: list[str] = []
    for line, note in annotate(lines):
        if not flt.accepts(line):
            # A loss detected on a hidden line is still reported, on the
            # next displayed one.
            if note.startswith("!!"):
                hidden_notes.append(f"{note} [on hidden {line.event or line.kind.value}]")
            continue
        note = " ".join(hidden_notes + ([note] if note else []))
        hidden_notes.clear()
        out.write(format_line(line, color=color, show_node=show_node, note=note) + "\n")
        out.flush()
