"""Run verdict: expectations checked against capture files, live or offline.

A run starts at `since` (the flash). It is decided by an expect file (TOML):

    timeout = "10m"          # overall; undecided at timeout -> exit 2
    build = "a1b2c3d-dirty"  # Build ID every flashed node must boot
    smoke = true             # default; the Smoke Check below
    smoke_window = "30s"     # default
    min_duration = "30s"     # no pass before since + this (default: smoke_window)

    [nodes.com9]             # capture node names (file prefix)
    cls = "C2"               # optional: class the node must boot as
    reboots = 1              # optional: reboots allowed after arming (scheduled resets)
    since = "2026-09-29T10:00:05Z"  # optional: ignore this node's lines before (its flash ended then)
    [nodes.com8]
    flashed = false          # not flashed: no arming, no smoke, expectations only

    [[expect]]
    node = "com9"            # optional: any node
    event = "CLK"
    where = { to = "WARM" }  # optional: field values; a number range:
                             #   { err = { min = -1, max = 1 } } (inclusive, either bound optional;
                             #   a field that is not a number never matches a range)
    within = "5m"            # optional: from the run being armed (default: timeout)
    count = 1                # optional: at least this many

    [[forbid]]
    node = "com9"            # optional: any node
    event = "TX_LATE"
    where = {}

Arming: a flashed node is armed once both cores have logged BOOT with the
expected Build ID after `since`; the run is armed when every flashed node is.
Lines before a node's first BOOT are the old image and are ignored.

A BOOT with another Build ID before arming is noted and waited through (the
old image rebooting before the flash took effect).

Failures (exit 1), from a node's first BOOT on: a BOOT with another Build
ID once armed, a reboot beyond `reboots`, a CM0+ rebooting alone, lost
lines (sequence gaps), a schema problem (unknown event, missing field,
unregistered board), a forbidden event, an expectation missing its
`within`, or the Smoke Check failing.

Smoke Check, per flashed node within `smoke_window` of `since`: armed, CM4
`CORE_SYNC stage=linked` after its BOOT, and `cls` as specified.

Pass (exit 0): no failure, every expectation met, smoke passed, and
`min_duration` elapsed. Timeout (exit 2): `timeout` elapsed first.
"""

from __future__ import annotations

import re
import sys
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Callable

from arclog.health import SeqTracker
from arclog.model import Kind, Line, format_host_time, parse_capture_line
from arclog.schema import validate

if sys.version_info >= (3, 11):
    import tomllib
else:  # pragma: no cover
    import tomli as tomllib

PASS, FAIL, TIMEOUT = 0, 1, 2

_DURATION_RE = re.compile(r"^\s*(\d+(?:\.\d+)?)\s*(ms|s|m|h)?\s*$")
_UNITS = {"ms": 0.001, "s": 1.0, "m": 60.0, "h": 3600.0, None: 1.0}


def parse_duration(value: object) -> timedelta:
    """'30s', '5m', '1h', '500ms', or a number of seconds."""
    if isinstance(value, bool):
        raise ValueError(f"not a duration: {value!r}")
    if isinstance(value, (int, float)):
        return timedelta(seconds=float(value))
    m = _DURATION_RE.match(str(value))
    if not m:
        raise ValueError(f"not a duration: {value!r} (use 30s, 5m, 1h, 500ms)")
    return timedelta(seconds=float(m[1]) * _UNITS[m[2]])


def parse_since(text: str) -> datetime:
    """ISO UTC time ('2026-09-28T23:24:00Z', fractional seconds allowed) or 'now'."""
    if text == "now":
        return datetime.now(timezone.utc)
    t = datetime.fromisoformat(text.replace("Z", "+00:00"))
    return t.replace(tzinfo=timezone.utc) if t.tzinfo is None else t.astimezone(timezone.utc)


# ---------------------------------------------------------------------------
# Spec
# ---------------------------------------------------------------------------


@dataclass
class NodeSpec:
    name: str
    flashed: bool = True
    cls: str | None = None
    reboots: int = 0
    since: datetime | None = None   # this node's lines before it are ignored (its flash ended then)


@dataclass(frozen=True)
class Range:
    """A numeric field condition, both bounds inclusive and optional: { min = -1, max = 1 }."""

    min: float | None = None
    max: float | None = None

    def matches(self, value: str | None) -> bool:
        try:
            x = float(value)
        except (TypeError, ValueError):
            return False
        return (self.min is None or x >= self.min) and (self.max is None or x <= self.max)

    def as_table(self) -> dict:
        return {k: v for k, v in (("min", self.min), ("max", self.max)) if v is not None}

    def __str__(self) -> str:
        return f"{_num(self.min)}..{_num(self.max)}"


def _num(x: float | None) -> str:
    return "" if x is None else f"{x:g}"


Condition = str | Range


def parse_condition(where: str, value: object) -> Condition:
    """A `where` value: a table { min, max } is a Range, anything else an exact value."""
    if isinstance(value, Range):
        return value
    if not isinstance(value, dict):
        return str(value)
    unknown = set(value) - {"min", "max"}
    if unknown:
        raise ValueError(f"{where}: unknown key(s) {', '.join(sorted(unknown))} (a range takes min, max)")
    if not value:
        raise ValueError(f"{where}: a range needs min, max or both")
    bounds = {}
    for key in ("min", "max"):
        if key in value:
            v = value[key]
            if isinstance(v, bool) or not isinstance(v, (int, float)):
                raise ValueError(f"{where}: {key} must be a number, got {v!r}")
            bounds[key] = v
    r = Range(**bounds)
    if r.min is not None and r.max is not None and r.min > r.max:
        raise ValueError(f"{where}: min {_num(r.min)} is above max {_num(r.max)}")
    return r


def condition_matches(cond: Condition, value: str | None) -> bool:
    return cond.matches(value) if isinstance(cond, Range) else value == cond


@dataclass
class Match:
    """A line pattern: event, optional node, optional field conditions (exact value or Range)."""

    event: str
    node: str | None = None
    where: dict[str, Condition] = field(default_factory=dict)

    def matches(self, line: Line) -> bool:
        return (line.kind is Kind.ARCLOG and line.event == self.event
                and (self.node is None or line.node == self.node)
                and all(condition_matches(c, line.fields.get(k)) for k, c in self.where.items()))

    def describe(self) -> str:
        where = "".join(f" {k}={v}" for k, v in self.where.items())
        return f"{self.node or 'any'} {self.event}{where}"


@dataclass
class Expectation(Match):
    within: timedelta | None = None
    count: int = 1


@dataclass
class Spec:
    nodes: dict[str, NodeSpec]
    timeout: timedelta
    build: str | None = None
    smoke: bool = True
    smoke_window: timedelta = timedelta(seconds=30)
    min_duration: timedelta | None = None
    expects: list[Expectation] = field(default_factory=list)
    forbids: list[Match] = field(default_factory=list)

    @property
    def hold(self) -> timedelta:
        if self.min_duration is not None:
            return self.min_duration
        return self.smoke_window if self.smoke else timedelta(0)


def _check_keys(where: str, table: dict, allowed: set[str]) -> None:
    unknown = set(table) - allowed
    if unknown:
        raise ValueError(f"{where}: unknown key(s) {', '.join(sorted(unknown))}")


def _match_args(where: str, t: dict, nodes: dict[str, NodeSpec]) -> dict:
    if "event" not in t:
        raise ValueError(f"{where}: 'event' is required")
    node = t.get("node")
    if node is not None and node not in nodes:
        raise ValueError(f"{where}: node {node!r} is not in [nodes]")
    fields = t.get("where", {})
    if not isinstance(fields, dict):
        raise ValueError(f"{where}: 'where' must be a table of field values")
    return {"event": str(t["event"]), "node": node,
            "where": {str(k): parse_condition(f"{where}.where.{k}", v) for k, v in fields.items()}}


def spec_from_dict(d: dict) -> Spec:
    _check_keys("expect file", d, {"timeout", "build", "smoke", "smoke_window",
                                   "min_duration", "nodes", "expect", "forbid"})
    if "timeout" not in d:
        raise ValueError("expect file: 'timeout' is required")
    if not d.get("nodes"):
        raise ValueError("expect file: [nodes] must name at least one node")
    nodes = {}
    for name, t in d["nodes"].items():
        _check_keys(f"nodes.{name}", t, {"flashed", "cls", "reboots", "since"})
        nodes[name] = NodeSpec(name, flashed=bool(t.get("flashed", True)),
                               cls=t.get("cls"), reboots=int(t.get("reboots", 0)),
                               since=parse_since(str(t["since"])) if "since" in t else None)
    expects = []
    for i, t in enumerate(d.get("expect", [])):
        where = f"expect[{i}]"
        _check_keys(where, t, {"node", "event", "where", "within", "count"})
        within = parse_duration(t["within"]) if "within" in t else None
        expects.append(Expectation(**_match_args(where, t, nodes), within=within,
                                   count=int(t.get("count", 1))))
    forbids = []
    for i, t in enumerate(d.get("forbid", [])):
        where = f"forbid[{i}]"
        _check_keys(where, t, {"node", "event", "where"})
        forbids.append(Match(**_match_args(where, t, nodes)))
    return Spec(
        nodes=nodes,
        timeout=parse_duration(d["timeout"]),
        build=d.get("build"),
        smoke=bool(d.get("smoke", True)),
        smoke_window=parse_duration(d.get("smoke_window", "30s")),
        min_duration=parse_duration(d["min_duration"]) if "min_duration" in d else None,
        expects=expects,
        forbids=forbids,
    )


def load_spec(path: str | Path) -> Spec:
    with open(path, "rb") as f:
        return spec_from_dict(tomllib.load(f))


# ---------------------------------------------------------------------------
# Engine
# ---------------------------------------------------------------------------


@dataclass
class Verdict:
    code: int
    reason: str

    @property
    def label(self) -> str:
        return {PASS: "PASS", FAIL: "FAIL", TIMEOUT: "TIMEOUT"}[self.code]


@dataclass
class Failure:
    """One thing that failed the run: what, on which node and when (`kind` names it for a caller that sorts them)."""

    reason: str
    kind: str = "other"
    node: str | None = None
    at: datetime | None = None


@dataclass
class _NodeState:
    spec: NodeSpec
    booted: bool = False            # first BOOT after since seen: checks are on
    boot_cores: set[str] = field(default_factory=set)
    armed_at: datetime | None = None
    linked: bool = False
    cls_ok: bool | None = None
    reboots: int = 0
    cm4_rebooted: bool = False      # a CM4 BOOT since the last CM0+ BOOT
    last_build: str | None = None   # a BOOT with another Build ID before arming


class Run:
    """Decides a run from lines (fed in time order per node) and clock ticks.

    The first failure is the verdict. With `keep_going` a failure only goes to `failures` and the lines keep
    being read, so a caller can list every failure of a finished run.
    """

    def __init__(self, spec: Spec, since: datetime,
                 report: Callable[[str], None] = lambda msg: None, keep_going: bool = False) -> None:
        self.spec = spec
        self.since = since
        self.report = report
        self.keep_going = keep_going
        self.verdict: Verdict | None = None
        self.failures: list[Failure] = []
        self.armed_at: datetime | None = None
        self._nodes = {n: _NodeState(s) for n, s in spec.nodes.items()}
        self._seq = SeqTracker()
        self._counts = [0] * len(spec.expects)
        self._smoke_done = not spec.smoke
        if not any(s.flashed for s in spec.nodes.values()):
            self.armed_at = since

    # -- helpers ------------------------------------------------------------

    def _at(self, t: datetime) -> str:
        return f"+{(t - self.since).total_seconds():.1f}s"

    def _fail(self, reason: str, kind: str = "other", node: str | None = None,
              at: datetime | None = None) -> None:
        if self.verdict is None:
            self.failures.append(Failure(reason, kind, node, at))
            if not self.keep_going:
                self.verdict = Verdict(FAIL, reason)
            self.report(f"FAIL {reason}")

    def _checking(self, st: _NodeState) -> bool:
        """Health checks run on flashed nodes from their first BOOT, on the others always."""
        return st.booted or not st.spec.flashed

    # -- lines --------------------------------------------------------------

    def feed(self, line: Line) -> None:
        if self.verdict is not None or line.host_time is None or line.host_time < self.since:
            return
        st = self._nodes.get(line.node)
        if st is None or (st.spec.since is not None and line.host_time < st.spec.since):
            return
        t = line.host_time
        armed_before = self.armed_at is not None  # the line that arms the run counts for nothing
        if line.kind is Kind.ARCLOG and line.event == "BOOT":
            self._boot(st, line, t)
            if self.verdict is not None:
                return
        if not self._checking(st):
            return  # the old image, before the flash took effect

        lost = self._seq.feed(line).lost
        if lost:
            self._fail(f"{line.node} {lost} line(s) lost before core {line.core} #{line.seq:02x} "
                       f"{line.event} {self._at(t)}", "lost_lines", line.node, t)
            return
        for problem in validate(line):
            self._fail(f"{line.node} {problem} {self._at(t)}")
            return

        if line.kind is not Kind.ARCLOG:
            return
        if line.event == "CORE_SYNC" and line.get("stage") == "linked" and st.booted:
            if not st.linked:
                st.linked = True
                self.report(f"ok   {line.node} CORE_SYNC stage=linked {self._at(t)}")
        for m in self.spec.forbids:
            if m.matches(line):
                self._fail(f"{line.node} forbidden {m.describe()}: {line.raw.strip()} {self._at(t)}")
                return
        if not armed_before and st.spec.flashed:
            return  # expectations count once the run is armed
        for i, e in enumerate(self.spec.expects):
            if e.matches(line) and self._counts[i] < e.count:
                self._counts[i] += 1
                self.report(f"ok   {e.describe()} ({self._counts[i]}/{e.count}) {self._at(t)}")

    def _boot(self, st: _NodeState, line: Line, t: datetime) -> None:
        name, core = line.node, line.core
        if not st.spec.flashed:
            return
        build = line.get("build")
        if self.spec.build is not None and build != self.spec.build:
            seen = build or "(none: firmware without the bench hook)"
            if st.armed_at is None:
                # Still the old image (a reset before the flash took effect):
                # noted, and its lines stay unchecked until the expected BOOT.
                st.last_build = seen
                st.booted = False
                st.boot_cores.clear()
                self.report(f"note {name} core {core} booted build={seen}, "
                            f"waiting for {self.spec.build} {self._at(t)}")
                return
            self._fail(f"{name} core {core} booted build={seen}, expected {self.spec.build} "
                       f"{self._at(t)}", "wrong_build", name, t)
            return
        if st.armed_at is not None:
            # A reboot: counted on the CM4, which starts the CM0+.
            if core == "4":
                st.reboots += 1
                st.cm4_rebooted = True
                st.linked = False
                if st.reboots > st.spec.reboots:
                    self._fail(f"{name} unexpected reboot ({st.reboots}, {st.spec.reboots} allowed) "
                               f"{self._at(t)}", "unplanned_reboot", name, t)
                    return
                self.report(f"ok   {name} reboot {st.reboots}/{st.spec.reboots} {self._at(t)}")
            elif not st.cm4_rebooted:
                self._fail(f"{name} CM0+ rebooted alone {self._at(t)}", "unplanned_reboot", name, t)
                return
            else:
                st.cm4_rebooted = False
        st.booted = True
        if core == "4" and st.armed_at is None:
            st.linked = False  # linked must follow this image's BOOT
        if core == "0" and st.spec.cls is not None:
            st.cls_ok = line.get("cls") == st.spec.cls
            if not st.cls_ok:
                self._fail(f"{name} booted cls={line.get('cls')}, expected {st.spec.cls} {self._at(t)}")
                return
        st.boot_cores.add(core)
        if st.armed_at is None and st.boot_cores >= {"0", "4"}:
            st.armed_at = t
            self.report(f"ok   {name} armed (both cores booted"
                        f"{' build=' + self.spec.build if self.spec.build else ''}) {self._at(t)}")
            flashed = [s for s in self._nodes.values() if s.spec.flashed]
            if all(s.armed_at is not None for s in flashed):
                self.armed_at = max(s.armed_at for s in flashed)
                self.report(f"ok   run armed {self._at(self.armed_at)}")

    # -- clock --------------------------------------------------------------

    def _smoke_missing(self) -> list[str]:
        missing = []
        for st in self._nodes.values():
            if not st.spec.flashed:
                continue
            if st.armed_at is None:
                cores = sorted({"0", "4"} - st.boot_cores)
                missing.append(f"{st.spec.name} no BOOT on core {','.join(cores)}"
                               + (f" with build={self.spec.build}" if self.spec.build else "")
                               + (f" (last booted build={st.last_build})" if st.last_build else ""))
            elif not st.linked:
                missing.append(f"{st.spec.name} no CORE_SYNC stage=linked")
        return missing

    def _expects_missing(self) -> list[str]:
        return [f"{e.describe()} ({self._counts[i]}/{e.count})"
                for i, e in enumerate(self.spec.expects) if self._counts[i] < e.count]

    def tick(self, now: datetime) -> Verdict | None:
        """Advance the clock; returns the verdict once there is one."""
        if self.verdict is not None:
            return self.verdict
        if not self._smoke_done:
            missing = self._smoke_missing()
            if not missing:
                self._smoke_done = True
                self.report(f"ok   smoke check {self._at(now)}")
            elif now >= self.since + self.spec.smoke_window:
                self._fail("smoke check: " + "; ".join(missing))
                return self.verdict
        if self.armed_at is not None:
            for i, e in enumerate(self.spec.expects):
                if (e.within is not None and self._counts[i] < e.count
                        and now >= self.armed_at + e.within):
                    self._fail(f"{e.describe()} not within {e.within.total_seconds():g}s of arming "
                               f"({self._counts[i]}/{e.count})")
                    return self.verdict
        if (self._smoke_done and self.armed_at is not None and not self._expects_missing()
                and now >= self.since + self.spec.hold):
            self.verdict = Verdict(PASS, "every expectation met")
            self.report(f"PASS {self._at(now)}")
            return self.verdict
        if now >= self.since + self.spec.timeout:
            missing = self._smoke_missing() if not self._smoke_done else []
            if self.armed_at is None and not missing:
                missing = ["run not armed"]
            missing += self._expects_missing()
            self.verdict = Verdict(TIMEOUT, "missing: " + "; ".join(missing))
            self.report(f"TIMEOUT {self.verdict.reason}")
        return self.verdict


# ---------------------------------------------------------------------------
# Capture files
# ---------------------------------------------------------------------------


class DirFollower:
    """Reads new lines of each node's daily capture files (<node>-YYYYMMDD.log).

    Polling, not file-change notifications: files written from Windows over
    the WSL share notify late and in bursts. Only complete lines are read;
    a half-written last line waits for the next poll.
    """

    def __init__(self, directory: str | Path, nodes: list[str], since: datetime) -> None:
        self.directory = Path(directory)
        self.nodes = nodes
        self.since = since
        self._offsets: dict[Path, int] = {}

    def _files(self, node: str) -> list[Path]:
        first = self.since.strftime("%Y%m%d")
        files = []
        for p in self.directory.glob(f"{node}-*.log"):
            date = p.stem[len(node) + 1:]
            if len(date) == 8 and date.isdigit() and date >= first:
                files.append(p)
        return sorted(files)

    def poll(self) -> list[Line]:
        """New complete lines at or after `since`, per node in file order."""
        out: list[Line] = []
        for node in self.nodes:
            for path in self._files(node):
                offset = self._offsets.get(path, 0)
                with path.open("rb") as f:
                    f.seek(offset)
                    data = f.read()
                end = data.rfind(b"\n")
                if end < 0:
                    continue
                self._offsets[path] = offset + end + 1
                for raw in data[: end + 1].decode("utf-8", errors="replace").split("\n"):
                    if raw.strip():
                        line = parse_capture_line(raw, node=node)
                        if line.host_time is not None and line.host_time >= self.since:
                            out.append(line)
        return out


def replay(run: Run, lines: list[Line]) -> Verdict | None:
    """Feed lines in host-time order, ticking the clock before and after each line."""
    for line in sorted(lines, key=lambda ln: ln.host_time):
        if run.tick(line.host_time) is not None:
            break
        run.feed(line)
        if run.tick(line.host_time) is not None:
            break
    return run.verdict


def describe_start(spec: Spec, since: datetime) -> str:
    nodes = ", ".join(f"{n}{'' if s.flashed else ' (not flashed)'}" for n, s in spec.nodes.items())
    return (f"arclog expect: since {format_host_time(since)}, nodes {nodes}, "
            f"timeout {spec.timeout.total_seconds():g}s"
            + (f", build {spec.build}" if spec.build else ""))
