"""ArcLog line model and parser.

A device line (see Common/Log/arclog.h):

    260925T123456.7890 0Y M #41 SYNC_RX ph=0 ce=1 ep=45120000 st=45123004 err=4

A capture file line prefixes it with the host UTC receive time and a tab:

    2026-09-25T12:34:56.789012Z<TAB>260925T123456.7890 0Y M #41 SYNC_RX ...

Every line is classified as one of:
  ARCLOG  - full ArcLog header, event and key=value fields (also recovered
            from the end of a line when a reset cut the line before it)
  LEGACY  - device timestamp but free text (ST-generated trace lines that
            live outside CubeMX USER CODE regions and cannot be converted)
  RAW     - anything else (boot noise, TS_OFF lines, garbage)
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Iterator

DEV_TS = r"\d{6}T\d{6}\.\d{4}"

ARCLOG_RE = re.compile(
    rf"^(?P<ts>{DEV_TS}) (?P<core>[04])(?P<mod>[A-Z]) (?P<lvl>[ALMH]) "
    r"#(?P<seq>[0-9a-f]{2}) (?P<event>[A-Z][A-Z0-9_]*)(?: (?P<body>.*))?$"
)
LEGACY_RE = re.compile(rf"^(?P<ts>{DEV_TS}) (?P<body>.*)$")

#: Start of an ArcLog line inside another one: a line cut by a reset, glued
#: to the first line of the new boot ("...0S A \ufffd000000T000000.9997 4S A #00 BOOT").
EMBEDDED_ARCLOG_RE = re.compile(rf"{DEV_TS} [04][A-Z] [ALMH] #[0-9a-f]{{2}} [A-Z]")

#: Verbosity letters in increasing verbosity (VLEVEL_ALWAYS, L, M, H).
LEVELS = "ALMH"

MODULES = {
    "T": "TDMA",
    "M": "MAC",
    "Y": "SYNC",
    "R": "RADIO",
    "X": "MBMUX",
    "S": "SYS",
    "P": "POWER",
}

CORES = {"0": "CM0+", "4": "CM4"}


class Kind(str, Enum):
    ARCLOG = "ARCLOG"
    LEGACY = "LEGACY"
    RAW = "RAW"


@dataclass
class Line:
    """One trace line, classified and split into fields."""

    raw: str
    kind: Kind
    node: str = ""
    lineno: int = 0
    host_time: datetime | None = None
    dev_time: datetime | None = None
    core: str = ""
    mod: str = ""
    level: str = ""
    seq: int = -1
    event: str = ""
    fields: dict[str, str] = field(default_factory=dict)
    text: str = ""  # free text of LEGACY / RAW lines

    def int(self, key: str, default: int | None = None) -> int | None:
        """Field as an integer (decimal, signed), or default when absent/invalid."""
        value = self.fields.get(key)
        if value is None:
            return default
        try:
            return int(value, 10)
        except ValueError:
            return default

    def get(self, key: str, default: str | None = None) -> str | None:
        return self.fields.get(key, default)

    @property
    def time(self) -> datetime | None:
        """Best available timeline position: host time, else device time."""
        return self.host_time or self.dev_time


def parse_dev_time(ts: str) -> datetime | None:
    """Parse YYMMDDTHHMMSS.ssss (two-digit RTC year, 2000-based).

    Returns None for impossible calendar values (for example month 0 after
    an RTC write with an invalid date).
    """
    try:
        return datetime(
            2000 + int(ts[0:2]),
            int(ts[2:4]),
            int(ts[4:6]),
            int(ts[7:9]),
            int(ts[9:11]),
            int(ts[11:13]),
            int(ts[14:18]) * 100,
        )
    except ValueError:
        return None


def parse_host_time(text: str) -> datetime | None:
    if len(text) == 27 and text[26] == "Z":
        # What `format_host_time` writes, which is every line of a capture: strptime costs 30 us a line, which a
        # record of half a million lines feels.
        try:
            return datetime.fromisoformat(text[:26] + "+00:00")
        except ValueError:
            pass
    try:
        return datetime.strptime(text, "%Y-%m-%dT%H:%M:%S.%fZ").replace(tzinfo=timezone.utc)
    except ValueError:
        return None


def format_host_time(t: datetime) -> str:
    return t.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


def parse_fields(body: str) -> dict[str, str]:
    fields: dict[str, str] = {}
    for token in body.split():
        key, sep, value = token.partition("=")
        if sep:
            fields[key] = value
    return fields


def parse_line(
    text: str,
    node: str = "",
    host_time: datetime | None = None,
    lineno: int = 0,
) -> Line:
    """Classify and parse one device line (no capture prefix)."""
    raw = text.rstrip("\r\n")
    stripped = raw.strip()

    # A line cut by a reset, then the new boot's first line: keep the latter. The cut line
    # may itself parse ("... CORE_SYNC stage=cm<junk>000000T000000.9997 4S A #00 BOOT"), so
    # look inside every line; field values have no spaces, so a full header inside one is a glue.
    embedded = [m.start() for m in EMBEDDED_ARCLOG_RE.finditer(stripped) if m.start() > 0]
    if embedded:
        line = parse_line(stripped[embedded[-1]:], node=node, host_time=host_time, lineno=lineno)
        if line.kind is Kind.ARCLOG:
            line.raw = raw
            return line

    m = ARCLOG_RE.match(stripped)
    if m:
        return Line(
            raw=raw,
            kind=Kind.ARCLOG,
            node=node,
            lineno=lineno,
            host_time=host_time,
            dev_time=parse_dev_time(m["ts"]),
            core=m["core"],
            mod=m["mod"],
            level=m["lvl"],
            seq=int(m["seq"], 16),
            event=m["event"],
            fields=parse_fields(m["body"] or ""),
        )

    m = LEGACY_RE.match(stripped)
    if m:
        return Line(
            raw=raw,
            kind=Kind.LEGACY,
            node=node,
            lineno=lineno,
            host_time=host_time,
            dev_time=parse_dev_time(m["ts"]),
            text=m["body"],
        )

    return Line(raw=raw, kind=Kind.RAW, node=node, lineno=lineno, host_time=host_time, text=stripped)


def parse_capture_line(text: str, node: str = "", lineno: int = 0) -> Line:
    """Parse a capture-file line: optional '<host UTC>\\t' prefix, then the device line."""
    text = text.rstrip("\r\n")
    head, sep, rest = text.partition("\t")
    if sep:
        host_time = parse_host_time(head)
        if host_time is not None:
            return parse_line(rest, node=node, host_time=host_time, lineno=lineno)
    return parse_line(text, node=node, lineno=lineno)


_DAY_SUFFIX_RE = re.compile(r"^(.+)-\d{8}$")


def node_from_path(path: Path) -> str:
    """Node name from a capture file name: 'c3-20260925.log' -> 'c3', 'nuna-node-04-20261012.log' ->
    'nuna-node-04' (the UTC day is cut off; a name with no day stops at its first dash)."""
    m = _DAY_SUFFIX_RE.match(path.stem)
    return m[1] if m else path.stem.split("-", 1)[0]


def read_lines(path: str | Path, node: str | None = None) -> Iterator[Line]:
    """Iterate the lines of a capture file (or a plain serial dump)."""
    path = Path(path)
    node = node or node_from_path(path)
    with path.open("r", encoding="utf-8", errors="replace", newline="") as f:
        for lineno, text in enumerate(f, start=1):
            if text.strip():
                yield parse_capture_line(text, node=node, lineno=lineno)


def level_allows(line_level: str, max_level: str) -> bool:
    """True when a line of verbosity line_level passes a max_level filter."""
    return LEVELS.index(line_level) <= LEVELS.index(max_level)

