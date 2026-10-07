"""bench validate: read a run record again and say whether its data can be used.

The record is self-contained (scenario, manifest, capture slices, marks slices), so the verdict and the causes
are computed again from it, with no board, no capture and no network; the end of `bench run` does the same
thing on the record it has just written, which is why the two agree.
"""

from __future__ import annotations

import sys
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace

from arclog.expect import DirFollower, Run, replay, spec_from_dict
from arclog.marks import Mark, format_mark, read_marks
from arclog.model import Kind, format_host_time, parse_host_time
from arclog.validity import (Cause, check_marks, exit_code, invalidating, port_outages, silence_causes,
                             trace_causes)

from bench.boards import Board
from bench.collector import collector_causes
from bench.manifest import Host, dumps, finish_manifest, read_notes
from bench.run import RunOutcome, expect_spec, slice_capture, write_record
from bench.scenario import Scenario, load

if sys.version_info >= (3, 11):
    import tomllib
else:  # pragma: no cover
    import tomli as tomllib


@dataclass
class Assessment:
    code: int                  # 0 pass, 1 fail, 2 timeout, 4 invalid: the exit code of the run
    verdict_code: int          # what the engine decided on the trace alone
    causes: list[Cause]
    dataset: bool
    notes: list[str] = field(default_factory=list)   # what could not be checked, and why


def assess(record: Path) -> Assessment:
    manifest = tomllib.loads((record / "manifest.toml").read_text(encoding="utf-8"))
    run = manifest["run"]
    s = load(record / "scenario.toml")
    in_scenario = [b for b in manifest["board"] if b["role"] != "not in the scenario"]
    boards = {b["node_id"]: SimpleNamespace(node=b["log"]) for b in in_scenario}
    flashed = {b["node_id"]: parse_host_time(b["flashed_at"]) for b in in_scenario if "flashed_at" in b}
    since, end = parse_host_time(run["start"]), parse_host_time(run["end"])
    spec = spec_from_dict(expect_spec(s, boards, run["build_id"], flashed))
    lines = DirFollower(record, [b["log"] for b in in_scenario], since).poll()

    engine = Run(spec, since)
    verdict = replay(engine, lines) or engine.tick(end)
    verdict_code = verdict.code if verdict else 2
    causes = trace_causes(spec, since, lines)
    notes: list[str] = []
    logs = [b["log"] for b in in_scenario]
    marks = _marks(record, logs)
    if marks is None:
        notes.append("no marks in the record: the capture predates them, so its gaps and the host's clock were not checked")
    else:
        causes += check_marks(marks, since, end)
    if s.max_silence is not None:
        outages = port_outages([m for m in marks or [] if since <= m.utc <= end])
        causes += silence_causes(lines, logs, s.max_silence, since, end, outages)
    causes += _pi_clock_causes(manifest.get("pi_clock", []), since)
    return Assessment(exit_code(verdict_code, causes, s.dataset), verdict_code, causes, s.dataset, notes)


def _marks(record: Path, logs: list[str]) -> list[Mark] | None:
    """The record's marks, with only the ports of the run's boards (another board's cable is not this run's
    fault); None when the record has none."""
    files = sorted(record.glob("marks-*.log"))
    if not files:
        return None
    marks = [m for f in files for m in read_marks(f)]
    return [Mark(m.utc, m.mono, {n: p for n, p in m.ports.items() if n in logs}) for m in marks]


def _pi_clock_causes(entries: list[dict], since) -> list[Cause]:
    """A Pi whose clock was not synchronised or lagged the collector's, at the start or at the end."""
    causes: dict[str, Cause] = {}
    for e in entries:
        for label in ("start", "end"):
            state = {k: e[f"{label}_{k2}"] for k, k2 in (("pi_ntp", "ntp"), ("clock_lag_s", "lag_s"))
                     if f"{label}_{k2}" in e}
            for cause in collector_causes([e["name"]], {e["name"]: {"connected": True, **state}}, since, {}):
                causes.setdefault(cause.text, cause)
    return list(causes.values())


def format_validity(a: Assessment) -> list[str]:
    """The lines of a report's Validity section."""
    if invalidating(a.causes, a.dataset):
        lines = ["- INVALID (exit 4): the data of this run cannot be used, whatever its verdict says."]
    else:
        lines = ["- Valid: nothing the bench did makes the data unfit."]
    lines += [f"- {c.fault} fault, {c.kind}: {c.text}" for c in a.causes]
    lines += [f"- note: {n}" for n in a.notes]
    return lines


def validity_table(a: Assessment) -> dict:
    """The [validity] table of the manifest."""
    table: dict = {"valid": not invalidating(a.causes, a.dataset), "exit_code": a.code}
    causes = []
    for c in a.causes:
        entry = {"kind": c.kind, "fault": c.fault, "text": c.text}
        if c.node:
            entry["node"] = c.node
        if c.start:
            entry["start"] = format_host_time(c.start)
        if c.end:
            entry["end"] = format_host_time(c.end)
        causes.append(entry)
    if causes:
        table["cause"] = causes
    if a.notes:
        table["notes"] = a.notes
    return table


def slice_marks(capture_dir: Path, out_dir: Path, nodes: list[str], start: datetime, end: datetime) -> None:
    """Copy the marks of the run window, for the run's own ports only, so the record can be judged alone."""
    for path in sorted(capture_dir.glob("marks-*.log")):
        kept = [Mark(m.utc, m.mono, {n: p for n, p in m.ports.items() if n in nodes})
                for m in read_marks(path) if start <= m.utc <= end]
        if kept:
            out_dir.mkdir(parents=True, exist_ok=True)
            (out_dir / path.name).write_text("".join(format_mark(m) + "\n" for m in kept), encoding="utf-8")


def _boots(record: Path, nodes: list[str], since: datetime) -> dict[str, list[str]]:
    """The first BOOT line of each core of each node, as logged."""
    boots: dict[str, list[str]] = {n: [] for n in nodes}
    seen: set[tuple[str, str]] = set()
    for line in sorted(DirFollower(record, nodes, since).poll(), key=lambda ln: ln.host_time):
        if line.kind is Kind.ARCLOG and line.event == "BOOT" and (line.node, line.core) not in seen:
            seen.add((line.node, line.core))
            boots[line.node].append(line.raw.strip())
    return boots


def conclude(out_dir: Path, capture_dir: Path, scenario_text: str, s: Scenario, build_id: str, outcome: RunOutcome,
             boards: dict[int, Board], others: list[Board], manifest: dict, host_end: Host,
             pi_clocks: dict[str, tuple[dict, dict]]) -> Assessment:
    """The end of `bench run`: keep the capture and the marks of the window, complete the manifest, judge the
    record exactly as `bench validate` will, and write the report. Returns the assessment (its code is the
    run's exit code)."""
    out_dir.mkdir(parents=True, exist_ok=True)
    nodes = [b.node for b in boards.values()]
    slice_capture(capture_dir, out_dir, nodes, outcome.started, outcome.ended)
    slice_marks(capture_dir, out_dir, nodes, outcome.started, outcome.ended)
    finish_manifest(manifest, ended=outcome.ended, verdict=outcome.verdict, exit_code=outcome.code, host=host_end,
                    boots=_boots(out_dir, nodes, outcome.started), notes=read_notes(out_dir), pi_clocks=pi_clocks)
    (out_dir / "scenario.toml").write_text(scenario_text, encoding="utf-8")
    (out_dir / "manifest.toml").write_text(dumps(manifest), encoding="utf-8")
    a = assess(out_dir)
    manifest["run"]["exit_code"] = a.code
    manifest["validity"] = validity_table(a)
    (out_dir / "manifest.toml").write_text(dumps(manifest), encoding="utf-8")
    write_record(out_dir, scenario_text, s, build_id, outcome, boards, others, validity=format_validity(a),
                 invalid=bool(invalidating(a.causes, a.dataset)))
    return a
