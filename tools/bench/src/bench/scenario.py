"""Scenarios: what a run flashes, does and expects, with boards named by Node ID.

    description = "C2 rejoins after a reset"
    timeout = "15m"
    max_silence = "5m"   # optional: a node quiet this long with its port up fails the run (default: not checked)
    dataset = true    # a session whose data is kept: a firmware event (lost lines, an unplanned
                      # reboot, a wrong Build ID) invalidates it instead of only failing it

    [nodes]
    1 = "C3"          # flashed as C3
    2 = "C2"          # flashed as C2
    3 = "watch"       # not flashed: its trace is checked, it keeps its image

    [overrides]       # Build Overrides for this run only
    TX_RAMP_MS = "5u"

    [[action]]
    reset = 2         # reset Node ID 2...
    after = { node = 2, event = "CLK", where = { to = "WARM" } }
    delay = "10s"     # ...10 s after it logs CLK to=WARM (or: at = "+90s" after arming)

    [[expect]]
    node = 2
    event = "CLK"
    where = { to = "WARM" }   # field values; a number range: { err = { min = -1, max = 1 } }
    count = 2         # WARM before and after the reset
    within = "12m"    # from arming

    [[forbid]]
    event = "TX_LATE"

The Smoke Check (both cores BOOT the build as their class, linked, no lost
line, no unexpected reboot) is always on; each reset action allows one
reboot of its node. Event and field names are checked against arclog's
event table, with a suggestion for a typo.
"""

from __future__ import annotations

import difflib
import re
import shlex
import sys
from dataclasses import dataclass, field
from datetime import timedelta
from pathlib import Path

from arclog.expect import Condition, Range, parse_condition, parse_duration
from arclog.schema import EVENTS

if sys.version_info >= (3, 11):
    import tomllib
else:  # pragma: no cover
    import tomli as tomllib

CLASSES = ("C1", "C2", "C3")
WATCH = "watch"


@dataclass
class Pattern:
    event: str
    node: int | None = None
    where: dict[str, Condition] = field(default_factory=dict)

    def describe(self) -> str:
        where = "".join(f" {k}={v}" for k, v in self.where.items())
        return f"{self.node if self.node is not None else 'any'} {self.event}{where}"


@dataclass
class Expect(Pattern):
    within: timedelta | None = None
    count: int = 1


@dataclass
class Action:
    reset: int
    at: timedelta | None = None           # after arming
    after: Pattern | None = None          # or after a trace event...
    delay: timedelta = timedelta(0)       # ...plus this delay

    def describe(self) -> str:
        if self.after is not None:
            when = f"{fmt(self.delay)} after {self.after.describe()}"
        else:
            when = f"at +{fmt(self.at)} after arming"
        return f"reset {self.reset} {when}"


@dataclass
class Scenario:
    nodes: dict[int, str]                    # Node ID -> C1/C2/C3 (flashed) or "watch"
    timeout: timedelta = timedelta(minutes=10)
    description: str = ""
    overrides: dict[str, str] = field(default_factory=dict)
    actions: list[Action] = field(default_factory=list)
    expects: list[Expect] = field(default_factory=list)
    forbids: list[Pattern] = field(default_factory=list)
    dataset: bool = False                    # a firmware event invalidates the run, instead of only failing it
    max_silence: timedelta | None = None     # a node quiet for longer (its port up) fails the run; None: not checked

    @property
    def flashed(self) -> dict[int, str]:
        return {nid: cls for nid, cls in self.nodes.items() if cls != WATCH}

    def reboots(self, nid: int) -> int:
        return sum(1 for a in self.actions if a.reset == nid)


# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------


def _suggest(word: str, choices) -> str:
    close = difflib.get_close_matches(word, list(choices), n=1)
    return f" (did you mean {close[0]}?)" if close else ""


def _check_keys(where: str, table: dict, allowed: set[str]) -> None:
    for key in table:
        if key not in allowed:
            raise ValueError(f"{where}: unknown key {key!r}{_suggest(key, allowed)}")


def _node_id(where: str, value, nodes: dict[int, str]) -> int:
    try:
        nid = int(value)
    except (TypeError, ValueError):
        raise ValueError(f"{where}: node must be a Node ID, got {value!r}") from None
    if nid not in nodes:
        raise ValueError(f"{where}: Node ID {nid} is not in [nodes]")
    return nid


def _pattern(where: str, t: dict, nodes: dict[int, str], allowed: set[str]) -> dict:
    _check_keys(where, t, allowed)
    event = t.get("event")
    if not event:
        raise ValueError(f"{where}: 'event' is required")
    if event not in EVENTS:
        raise ValueError(f"{where}: unknown event {event!r}{_suggest(event, EVENTS)}")
    fields = t.get("where", {})
    if not isinstance(fields, dict):
        raise ValueError(f"{where}: 'where' must be a table of field values")
    known = set(EVENTS[event].keys) | set(EVENTS[event].optional)
    for key in fields:
        if key not in known:
            raise ValueError(f"{where}: {event} has no field {key!r}{_suggest(key, known)} "
                             f"(fields: {', '.join(sorted(known))})")
    node = _node_id(where, t["node"], nodes) if "node" in t else None
    return {"event": event, "node": node,
            "where": {str(k): parse_condition(f"{where}.where.{k}", v) for k, v in fields.items()}}


def from_dict(d: dict) -> Scenario:
    _check_keys("scenario", d, {"description", "timeout", "dataset", "max_silence", "nodes", "overrides", "action", "expect",
                                "forbid"})
    if not isinstance(d.get("dataset", False), bool):
        raise ValueError("scenario: dataset must be true or false")
    try:
        max_silence = parse_duration(d["max_silence"]) if "max_silence" in d else None
    except ValueError as exc:
        raise ValueError(f"scenario: max_silence: {exc}") from None
    raw_nodes = d.get("nodes") or {}
    if not raw_nodes:
        raise ValueError("scenario: [nodes] must name at least one board (e.g. 2 = \"C2\")")
    nodes = {}
    for key, cls in raw_nodes.items():
        if not str(key).isdigit():
            raise ValueError(f"nodes: {key!r} is not a Node ID")
        if cls not in (*CLASSES, WATCH):
            raise ValueError(f"nodes.{key}: {cls!r} is not C1, C2, C3 or \"watch\"")
        nodes[int(key)] = cls
    if not any(c != WATCH for c in nodes.values()):
        raise ValueError("scenario: no node is flashed (all are \"watch\")")

    actions = []
    for i, t in enumerate(d.get("action", [])):
        where = f"action[{i}]"
        _check_keys(where, t, {"reset", "at", "after", "delay"})
        if "reset" not in t:
            raise ValueError(f"{where}: 'reset = <Node ID>' is required (reset is the only action)")
        nid = _node_id(where, t["reset"], nodes)
        if nodes[nid] == WATCH:
            raise ValueError(f"{where}: Node ID {nid} is only watched; reset a flashed node")
        if ("at" in t) == ("after" in t):
            raise ValueError(f"{where}: give either at = \"+90s\" or after = {{ node, event }}")
        if "at" in t:
            actions.append(Action(nid, at=parse_duration(str(t["at"]).lstrip("+"))))
        else:
            after = Pattern(**_pattern(f"{where}.after", t["after"], nodes, {"node", "event", "where"}))
            actions.append(Action(nid, after=after, delay=parse_duration(t.get("delay", 0))))

    expects = []
    for i, t in enumerate(d.get("expect", [])):
        args = _pattern(f"expect[{i}]", t, nodes, {"node", "event", "where", "within", "count"})
        expects.append(Expect(**args, within=parse_duration(t["within"]) if "within" in t else None,
                              count=int(t.get("count", 1))))
    forbids = [Pattern(**_pattern(f"forbid[{i}]", t, nodes, {"node", "event", "where"}))
               for i, t in enumerate(d.get("forbid", []))]

    overrides = {str(k): str(v) for k, v in (d.get("overrides") or {}).items()}
    from bench.buildid import check_overrides
    check_overrides(overrides)
    return Scenario(nodes=nodes, timeout=parse_duration(d.get("timeout", "10m")),
                    description=str(d.get("description", "")), overrides=overrides,
                    actions=actions, expects=expects, forbids=forbids, dataset=d.get("dataset", False),
                    max_silence=max_silence)


def load(path: str | Path) -> Scenario:
    with open(path, "rb") as f:
        try:
            return from_dict(tomllib.load(f))
        except tomllib.TOMLDecodeError as exc:
            raise ValueError(f"{path}: {exc}") from None


# ---------------------------------------------------------------------------
# Command line shorthand and saving
# ---------------------------------------------------------------------------

_KV_RE = re.compile(r"^([A-Za-z_][A-Za-z0-9_]*)=(.+)$")
_NUM = r"-?\d+(?:\.\d+)?"
_RANGE_RE = re.compile(rf"^({_NUM})?\.\.({_NUM})?$")


def _number(text: str) -> int | float:
    return float(text) if "." in text else int(text)


def _field_value(text: str):
    """'-1..1', '..-2' or '2..' -> a range table; anything else stays an exact value."""
    m = _RANGE_RE.match(text)
    if not m or (m[1] is None and m[2] is None):
        return text
    return {k: _number(v) for k, v in (("min", m[1]), ("max", m[2])) if v is not None}


def parse_expect_text(text: str) -> dict:
    """'2 CLK to=WARM within=7m count=2' (node or 'any', event, fields, within/count) -> expect table.

    A field value 'lo..hi', '..hi' or 'lo..' is a number range, bounds inclusive."""
    words = shlex.split(text)
    if len(words) < 2:
        raise ValueError(f"--expect {text!r}: expected '<Node ID|any> <EVENT> [field=value ...] "
                         f"[within=7m] [count=2]'")
    t: dict = {"event": words[1]}
    if words[0] != "any":
        t["node"] = words[0]
    for w in words[2:]:
        m = _KV_RE.match(w)
        if not m:
            raise ValueError(f"--expect {text!r}: {w!r} is not field=value")
        if m[1] in ("within", "count"):
            t[m[1]] = int(m[2]) if m[1] == "count" else m[2]
        else:
            t.setdefault("where", {})[m[1]] = _field_value(m[2])
    return t


def _toml_value(v) -> str:
    if isinstance(v, Range):
        v = v.as_table()
    if isinstance(v, dict):
        return "{ " + ", ".join(f"{k} = {_toml_value(x)}" for k, x in v.items()) + " }"
    if isinstance(v, (int, float)) and not isinstance(v, bool):
        return str(v)
    return '"' + str(v).replace("\\", "\\\\").replace('"', '\\"') + '"'


def to_toml(d: dict) -> str:
    """A scenario dict (as from_dict reads it) as a TOML file."""
    out = []
    for key in ("description", "timeout"):
        if key in d:
            out.append(f"{key} = {_toml_value(d[key])}")
    if d.get("dataset"):
        out.append("dataset = true")
    if "max_silence" in d:
        out.append(f"max_silence = {_toml_value(d['max_silence'])}")
    out += ["", "[nodes]"] + [f"{k} = {_toml_value(v)}" for k, v in d["nodes"].items()]
    if d.get("overrides"):
        out += ["", "[overrides]"] + [f"{k} = {_toml_value(v)}" for k, v in d["overrides"].items()]
    for section in ("action", "expect", "forbid"):
        for t in d.get(section, []):
            out += ["", f"[[{section}]]"]
            for k, v in t.items():
                out.append(f"{k} = {_toml_value(int(v) if k in ('node', 'reset') else v)}")
    return "\n".join(out) + "\n"


def fmt(d: timedelta) -> str:
    """90 s -> '90s', 1080 s -> '18m', 3600 s -> '1h'."""
    sec = d.total_seconds()
    for unit, size in (("h", 3600), ("m", 60)):
        if sec >= size and sec % size == 0:
            return f"{sec / size:g}{unit}"
    return f"{sec:g}s"


def plan(s: Scenario) -> str:
    """What a run of this scenario does, for a dry check."""
    lines = [s.description] if s.description else []
    for nid, cls in sorted(s.nodes.items()):
        lines.append(f"node {nid}: " + ("watched, not flashed" if cls == WATCH else f"flash as {cls}"))
    if s.overrides:
        lines.append("overrides: " + ", ".join(f"{k}={v}" for k, v in s.overrides.items()))
    lines.append("smoke check: both cores BOOT the build as their class, linked, no lost line")
    lines += [f"action: {a.describe()}" for a in s.actions]
    for e in s.expects:
        within = f" within {fmt(e.within)} of arming" if e.within else ""
        lines.append(f"expect: {e.describe()} x{e.count}{within}")
    for nid in sorted(s.flashed):
        if s.reboots(nid):
            lines.append(f"expect: {nid} reboots x{s.reboots(nid)} (one per reset; no other reboot allowed)")
    lines += [f"forbid: {f.describe()}" for f in s.forbids]
    if s.max_silence is not None:
        lines.append(f"max_silence: a node silent for more than {fmt(s.max_silence)} with its port up fails the run")
    if s.dataset:
        lines.append("dataset session: lost lines, an unplanned reboot or a wrong Build ID invalidate the run")
    lines.append(f"timeout: {fmt(s.timeout)}")
    return "\n".join(lines)
