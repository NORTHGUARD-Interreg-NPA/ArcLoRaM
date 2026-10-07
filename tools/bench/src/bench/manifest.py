"""The run manifest: what a session was (boards, build, clocks, notes), as TOML, next to its record.

A year later, a record must say what ran and whether its data can be used without anyone reading the
capture. `report.md` is for a person; `manifest.toml` is the structured copy, and `bench validate` reads it.
"""

from __future__ import annotations

import json
import re
import subprocess
import sys
import time
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Callable

from arclog.expect import parse_duration
from arclog.model import format_host_time, parse_host_time

from bench.boards import Board, format_uid
from bench.scenario import WATCH, Scenario, fmt

if sys.version_info >= (3, 11):
    import tomllib
else:  # pragma: no cover
    import tomli as tomllib

#: A session that never wrote its end (a crash) stops being "the running one" this long after its timeout.
RUNNING_SLACK = timedelta(minutes=10)


def _scalar(v) -> str:
    if isinstance(v, bool):
        return "true" if v else "false"
    if isinstance(v, (int, float)):
        return repr(v)
    if isinstance(v, str):
        return json.dumps(v)  # a TOML basic string takes JSON's escapes
    if isinstance(v, list):
        return "[" + ", ".join(_scalar(x) for x in v) + "]"
    raise TypeError(f"not a TOML value: {v!r}")


def _is_tables(v) -> bool:
    return isinstance(v, list) and bool(v) and all(isinstance(x, dict) for x in v)


def _body(d: dict, path: str) -> list[str]:
    body = dumps(d, path).strip("\n")
    return [body] if body else []


def dumps(d: dict, _path: str = "") -> str:
    """A dict of scalars, lists of scalars, tables and arrays of tables as TOML (nothing else is needed here)."""
    out = [f"{k} = {_scalar(v)}" for k, v in d.items() if not isinstance(v, dict) and not _is_tables(v)]
    for k, v in d.items():
        if isinstance(v, dict):
            out += ["", f"[{_path}{k}]", *_body(v, f"{_path}{k}.")]
        elif _is_tables(v):
            for item in v:
                out += ["", f"[[{_path}{k}]]", *_body(item, f"{_path}{k}.")]
    return "\n".join(out).strip("\n") + "\n"


def parse_time_service(text: str) -> dict:
    """Source, last sync, stratum and poll of `w32tm /query /status` (English labels).

    The ReferenceId line, which holds a server's address, is left out. Output with none of the labels (another
    language) is kept whole under `raw`, so the manifest still says what the time service said.
    """
    def field(label: str) -> str | None:
        m = re.search(rf"^{label}:\s*(.+?)\s*$", text, flags=re.MULTILINE)
        return m[1] if m else None

    source, last_sync, stratum, poll = (field("Source"), field("Last Successful Sync Time"), field("Stratum"),
                                        field("Poll Interval"))
    if source is None and last_sync is None and stratum is None:
        return {"error": "not understood", "raw": text.strip()}
    state: dict = {}
    if source is not None:
        state["source"] = source
    if last_sync is not None:
        state["last_sync"] = last_sync
    if stratum is not None and (m := re.match(r"\d+", stratum)):
        state["stratum"] = int(m[0])
    if poll is not None and (m := re.search(r"\((\d+s)\)", poll)):
        state["poll"] = m[1]
    return state


@dataclass
class Host:
    """One reading of the controller: UTC, the monotonic clock beside it, and what the time service says."""

    utc: datetime
    mono: float
    time_service: dict


_BUILD_PART = re.compile(r"^(?P<dirty>d[0-9a-f]{6})$|^(?P<overrides>o[0-9a-f]{6})$")


def _firmware_commit(build_id: str) -> tuple[str, bool]:
    """(commit, dirty) of a Build ID `<hash>[-d<hash>][-o<hash>]`."""
    commit, *rest = build_id.split("-")
    return commit, any(_BUILD_PART.match(part) and part.startswith("d") for part in rest)


def start_manifest(*, command: str, worktree: str, bench_commit: str, build_id: str, scenario: Scenario,
                   boards: dict[int, Board], others: list[Board], started: datetime,
                   flashed: dict[int, datetime], host: Host) -> dict:
    """What is known when a run is armed. Never a Pi Node's address: a manifest is committed with its dataset."""
    commit, dirty = _firmware_commit(build_id)
    run: dict = {"command": command, "worktree": worktree, "bench_commit": bench_commit, "build_id": build_id,
                 "firmware_commit": commit, "firmware_dirty": dirty, "overrides": dict(scenario.overrides),
                 "scenario": "scenario.toml", "dataset": scenario.dataset}
    if scenario.max_silence is not None:
        run["max_silence"] = fmt(scenario.max_silence)
    run["timeout"] = fmt(scenario.timeout)
    run["start"] = format_host_time(started)

    entries = []
    for nid, cls in sorted(scenario.nodes.items()):
        b = boards[nid]
        entry: dict = {"node_id": nid, "role": "watched" if cls == WATCH else "flashed"}
        if cls != WATCH:
            entry["class"] = cls
        entry["kind"] = "pinode" if b.remote else "stlink"
        if b.uid is not None:
            entry["uid"] = format_uid(b.uid)
        entry["probe"] = b.sn
        if not b.remote and b.port:
            entry["port"] = b.port
        entry["log"] = b.node
        if nid in flashed:
            entry["flashed_at"] = format_host_time(flashed[nid])
        entries.append(entry)
    for b in others:
        entry = {}
        if b.node_id is not None:
            entry["node_id"] = b.node_id
        entry.update({"role": "not in the scenario", "kind": "pinode" if b.remote else "stlink"})
        if b.uid is not None:
            entry["uid"] = format_uid(b.uid)
        entry["probe"] = b.sn
        if not b.remote and b.port:
            entry["port"] = b.port
        if b.node:
            entry["log"] = b.node
        if b.build:
            entry["build"] = b.build
        entries.append(entry)

    return {"run": run, "board": entries,
            "controller": {"start_utc": format_host_time(host.utc), "start_mono": host.mono,
                           "time_service_start": host.time_service}}


def finish_manifest(m: dict, *, ended: datetime, verdict: str, exit_code: int, host: Host,
                    boots: dict[str, list[str]], notes: list[dict],
                    pi_clocks: dict[str, tuple[dict, dict]]) -> None:
    """Complete a manifest at the end of the run, in place.

    `boots` are the BOOT lines of each log as captured, `pi_clocks` the collector's view of each Pi Node's clock
    at the start and at the end (`pi_ntp`, `clock_lag_s`), `notes` the ones made by hand during the session.
    """
    m["run"]["end"] = format_host_time(ended)
    m["run"]["verdict"] = verdict
    m["run"]["exit_code"] = exit_code
    m["controller"]["end_utc"] = format_host_time(host.utc)
    m["controller"]["end_mono"] = host.mono
    m["controller"]["time_service_end"] = host.time_service
    for board in m["board"]:
        if board.get("log") in boots:
            board["boot"] = boots[board["log"]]
    clocks = []
    for name, (start, end) in pi_clocks.items():
        entry: dict = {"name": name}
        for label, state in (("start", start), ("end", end)):
            if "pi_ntp" in state:
                entry[f"{label}_ntp"] = state["pi_ntp"]
            if "clock_lag_s" in state:
                entry[f"{label}_lag_s"] = state["clock_lag_s"]
        clocks.append(entry)
    if clocks:
        m["pi_clock"] = clocks
    if notes:
        m["note"] = notes


def append_note(run_dir: Path, text: str, now: datetime) -> None:
    """One timestamped line in the running session's notes (a board moved, the ambient temperature, a replug)."""
    run_dir.mkdir(parents=True, exist_ok=True)
    with (run_dir / "notes.log").open("a", encoding="utf-8", newline="\n") as f:
        f.write(f"{format_host_time(now)}\t{' '.join(text.split())}\n")


def read_notes(run_dir: Path) -> list[dict]:
    path = run_dir / "notes.log"
    if not path.is_file():
        return []
    notes = []
    for line in path.read_text(encoding="utf-8").splitlines():
        when, _, text = line.partition("\t")
        notes.append({"time": when, "text": text})
    return notes


def find_running(runs_root: Path, now: datetime) -> Path | None:
    """The newest record folder whose manifest has no end yet and whose timeout has not long passed."""
    if not runs_root.is_dir():
        return None
    for folder in sorted((d for d in runs_root.iterdir() if d.is_dir()), reverse=True):
        manifest = folder / "manifest.toml"
        if not manifest.is_file():
            continue
        run = tomllib.loads(manifest.read_text(encoding="utf-8")).get("run", {})
        if "end" in run or "start" not in run:
            continue
        deadline = parse_host_time(run["start"]) + parse_duration(run.get("timeout", "10m")) + RUNNING_SLACK
        if now <= deadline:
            return folder
    return None


def _w32tm() -> str:
    return subprocess.run(["w32tm.exe", "/query", "/status"], capture_output=True, text=True, check=True,
                          timeout=15, cwd="/mnt/c").stdout.replace("\r", "")


def read_host(now: Callable[[], datetime] = lambda: datetime.now(timezone.utc),
              mono: Callable[[], float] = time.monotonic, query: Callable[[], str] = _w32tm) -> Host:
    """The controller now: UTC, the monotonic clock beside it, and the time service's state.

    A UTC step between two readings that the monotonic clock does not follow is the time service correcting the
    host. A time service that cannot be asked is recorded as such: it never fails the run.
    """
    try:
        service = parse_time_service(query())
    except (OSError, subprocess.SubprocessError) as exc:
        service = {"error": str(exc)}
    return Host(now(), mono(), service)
