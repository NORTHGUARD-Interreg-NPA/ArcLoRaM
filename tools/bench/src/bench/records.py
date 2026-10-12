"""bench records check: an issue, its Test Record and its scenario agree.

A Test Record (`docs/test-tracker/`) is the evidence of an issue, a scenario (`tools/bench/scenarios/`) is its
executable; this module reads both as text and reports where they disagree, one finding per line, naming the file.
Nothing here touches a board or the network, except `gh_issue`, the one function that asks GitHub (`--online`).

The checks are small functions over parsed text, so a test needs a few lines of fixture to cover each:
  - `parse_record`, `parse_scenario_header`, `split_row`, `parse_issue_criteria` read the text;
  - the `check_*` functions return the findings of one rule;
  - `check_repo` reads a repo and runs them all.
"""

from __future__ import annotations

import json
import re
import subprocess
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Callable

TRACKER = "docs/test-tracker"
SCENARIOS = "tools/bench/scenarios"
NOT_RECORDS = frozenset({"README.md", "node-silences.md"})

ACCEPTANCE, PERFORMANCE, SPONTANEOUS = "acceptance", "performance", "spontaneous"
_KINDS = (
    (re.compile(r"^(\d+)-(.+)\.md$"), ACCEPTANCE),
    (re.compile(r"^perf-(.+)\.md$"), PERFORMANCE),
    (re.compile(r"^spot-\d{8}-(.+)\.md$"), SPONTANEOUS),
)

Fetch = Callable[[int], dict]     # issue number -> {"number", "state", "body"}, as `gh issue view --json` gives it


class IssueUnavailable(Exception):
    """An issue could not be read from GitHub (no `gh`, not logged in, no such issue)."""


@dataclass(frozen=True)
class Finding:
    file: str          # repo-relative path
    message: str

    def __str__(self) -> str:
        return f"{self.file}: {self.message}"


# --- reading a Test Record -----------------------------------------------------------------------------


@dataclass(frozen=True)
class Kind:
    kind: str
    issue: int | None      # acceptance records only: the prefix of the file name
    slug: str


def classify(name: str) -> Kind | None:
    """The purpose a Test Record's file name gives it, None for a name that is none of the three."""
    for pattern, kind in _KINDS:
        m = pattern.match(name)
        if m:
            if kind == ACCEPTANCE:
                return Kind(kind, int(m.group(1)), m.group(2))
            return Kind(kind, None, m.group(1))
    return None


@dataclass(frozen=True)
class ScenarioCell:
    """A row of the Scenarios table."""
    text: str                         # the first cell
    files: tuple[str, ...]            # backticked `*.toml` names in the first cell, as written
    to_write: bool                    # the cell says "to write": the file may be missing
    shared_with: tuple[str, ...]      # records the row names when it says "shared with"
    shared: bool


@dataclass
class Criterion:
    ticked: bool
    text: str


@dataclass
class Record:
    name: str
    kind: Kind | None
    scenarios: list[ScenarioCell] | None = None        # None: no `## Scenarios` section
    criteria: list[Criterion] | None = None            # None: no `## Criteria` section
    runs: list[tuple[int, list[str]]] | None = None    # (line number, cells) of every row; None: no `## Runs` table
    runs_header: list[str] | None = None

    @property
    def path(self) -> str:
        return f"{TRACKER}/{self.name}"

    @property
    def listed(self) -> set[str]:
        """Scenario file names (without directory) the Scenarios table names."""
        return {basename(f) for c in self.scenarios or [] for f in c.files}


def basename(ref: str) -> str:
    return ref.rsplit("/", 1)[-1]


def sections(text: str) -> dict[str, list[tuple[int, str]]]:
    """Second-level sections of a Markdown file: lower-case heading -> its (line number, line) pairs.
    Fenced code (the record template shown in a README) is not part of any section."""
    found: dict[str, list[tuple[int, str]]] = {}
    current = None
    fenced = False
    for n, line in enumerate(text.splitlines(), 1):
        if line.startswith("```"):
            fenced = not fenced
        elif fenced:
            continue
        elif line.startswith("## "):
            current = line[3:].strip().lower()
            found.setdefault(current, [])
        elif current is not None:
            found[current].append((n, line))
    return found


_BACKTICKED = re.compile(r"`([^`]+)`")


def split_row(line: str) -> list[str]:
    """The cells of a table row, split on pipes that are not escaped, outer pipes dropped."""
    body = line.strip()
    body = body[1:] if body.startswith("|") else body
    body = body[:-1] if body.endswith("|") and not body.endswith("\\|") else body
    return [c.strip().replace("\\|", "|") for c in re.split(r"(?<!\\)\|", body)]


def _scenario_cell(cells: list[str]) -> ScenarioCell:
    """A row of the Scenarios table. "to write" is said in the first cell; "shared with" in either of them."""
    text, row = cells[0], " | ".join(cells)
    files = tuple(t for t in _BACKTICKED.findall(text) if t.endswith(".toml"))
    shared = "shared with" in row.lower()
    named = tuple(t for t in _BACKTICKED.findall(row) if t.endswith(".md")) if shared else ()
    return ScenarioCell(text, files, "to write" in text.lower(), named, shared)


def _table_rows(lines: list[tuple[int, str]]) -> list[tuple[int, list[str]]]:
    return [(n, split_row(line)) for n, line in lines if line.lstrip().startswith("|")
            and not re.fullmatch(r"[\s|:-]+", line)]


def parse_record(name: str, text: str) -> Record:
    rec = Record(name, classify(name))
    secs = sections(text)
    if "scenarios" in secs:
        rows = _table_rows(secs["scenarios"])[1:]          # the first row is the header
        rec.scenarios = [_scenario_cell(cells) for _, cells in rows if cells]
    if "criteria" in secs:
        rec.criteria = []
        for _, line in secs["criteria"]:
            m = re.match(r"^- \[([ xX])\] ?(.*)$", line)
            if m:
                rec.criteria.append(Criterion(m.group(1) != " ", m.group(2).strip()))
            elif rec.criteria and line.startswith((" ", "\t")) and line.strip():
                rec.criteria[-1].text += " " + line.strip()      # a wrapped item
    if "runs" in secs:
        rows = _table_rows(secs["runs"])
        if rows:
            rec.runs_header = rows[0][1]
            rec.runs = rows[1:]
    return rec


# --- reading a scenario's header -----------------------------------------------------------------------


@dataclass(frozen=True)
class Header:
    issues: tuple[int, ...]       # every "Issue #n" the header names
    record: str | None            # the "Test Record docs/test-tracker/<file>.md" it names


_ISSUE = re.compile(r"[Ii]ssues? #(\d+)")
_TEST_RECORD = re.compile(r"Test Record\s+(docs/test-tracker/[\w.-]+\.md)")


def parse_scenario_header(text: str) -> Header:
    """The issue and the Test Record named by the leading block of `#` comment lines of a scenario file.
    The block is joined with spaces, so a path wrapped onto the next comment line is still read."""
    lines: list[str] = []
    for line in text.splitlines():
        if not line.strip() and not lines:
            continue
        if not line.startswith("#"):
            break
        lines.append(line.lstrip("#").strip())
    joined = " ".join(lines)
    record = _TEST_RECORD.search(joined)
    return Header(tuple(int(n) for n in _ISSUE.findall(joined)), record.group(1) if record else None)


# --- the rules -----------------------------------------------------------------------------------------


def check_sections(rec: Record) -> list[Finding]:
    missing = [(rec.scenarios, "Scenarios"), (rec.criteria, "Criteria"), (rec.runs_header, "Runs")]
    return [Finding(rec.path, f"no `## {title}` section" + (" with a table" if title == "Runs" else ""))
            for value, title in missing if value is None]


def check_scenario_refs(rec: Record, existing: set[str]) -> list[Finding]:
    """Every scenario file the record names exists, or the cell says "to write"."""
    out = []
    for cell in rec.scenarios or []:
        for ref in cell.files:
            if basename(ref) not in existing and not cell.to_write:
                out.append(Finding(rec.path, f"Scenarios: {ref} does not exist (write it, or mark the cell \"to write\")"))
    return out


def check_name(rec: Record) -> list[Finding]:
    if rec.kind is None:
        return [Finding(rec.path, "not a Test Record by its name (expected <issue>-<slug>.md, perf-<slug>.md "
                                  "or spot-<YYYYMMDD>-<slug>.md)")]
    return []


RUNS_COLUMNS = ["Date (UTC)", "Scenario", "Build ID", "Verdict", "Record", "Notes"]
_DATE = re.compile(r"(\d{4}-\d{2}-\d{2}) (\d{2}):[0-5][\dx](?=\s|$)")       # the minute may be imprecise: 17:4x
_BUILD = re.compile(r"[0-9a-f]{7,}(-d[0-9a-f]{6,})?(-o[0-9a-f]{6,})?")
_VERDICT = re.compile(r"(pass|fail|invalid|measurement|red|running|outage)\b", re.I)
_COUNT = re.compile(r"\b\d+ of \d+ (pass|fail)", re.I)          # "48 of 48 pass", "A: 10 of 10 pass. B: 9 of 10 pass"
_COUNTED_PASS = re.compile(r"(\d+) of \1 pass\b", re.I)


def _quote(text: str, width: int = 70) -> str:
    text = " ".join(text.split())
    return '"' + (text if len(text) <= width else text[:width] + "...") + '"'


def _plain(cell: str) -> str:
    return cell.replace("`", "").replace("*", "").strip()


def is_pass(verdict: str) -> bool:
    """A verdict that starts with PASS, or counts only passes ("48 of 48 pass")."""
    text = _plain(verdict)
    return bool(re.match(r"pass\b", text, re.I) or _COUNTED_PASS.match(text))


def _date_ok(cell: str) -> bool:
    m = _DATE.match(cell.strip())
    if not m or int(m.group(2)) > 23:
        return False
    try:
        date.fromisoformat(m.group(1))
    except ValueError:
        return False
    return True


def _build_ok(cell: str) -> bool:
    text = _plain(cell)
    if re.match(r"(none|n/a)\b", text, re.I) or text.startswith("("):      # "none (nothing was built)", "(several)"
        return True
    return bool(text) and all(_BUILD.fullmatch(part.strip()) for part in text.split(","))


def _record_ok(cell: str) -> bool:
    if re.match(r"none\b", cell.strip(), re.I):
        return True
    return any(t.startswith("tools/arclog/runs/") for t in _BACKTICKED.findall(cell))


def check_runs(rec: Record, existing: set[str]) -> list[Finding]:
    """The Runs table is the six documented columns, and each row is well formed.
    Only a clear rule is strict: free text stays free where the records use it (flags forms, host tests, notes)."""
    if rec.runs_header is None:
        return []
    out: list[Finding] = []

    def bad(n: int, column: str, why: str, cell: str) -> None:
        out.append(Finding(rec.path, f"Runs line {n}: {column} {_quote(cell, 50)} {why}"))

    if [c.lower() for c in rec.runs_header] != [c.lower() for c in RUNS_COLUMNS]:
        out.append(Finding(rec.path, f"Runs header is not | {' | '.join(RUNS_COLUMNS)} |"))
        return out
    for n, cells in rec.runs or []:
        if len(cells) != len(RUNS_COLUMNS):
            out.append(Finding(rec.path, f"Runs line {n}: {len(cells)} columns, expected {len(RUNS_COLUMNS)}"))
            continue
        when, scenario, build, verdict, record, _ = cells
        if not _date_ok(when):
            bad(n, "Date", "is not YYYY-MM-DD HH:MM (a minute may be imprecise: 17:4x)", when)
        if not scenario.strip():
            bad(n, "Scenario", "is empty (a scenario file, flags, or none)", scenario)
        for ref in (t for t in _BACKTICKED.findall(scenario) if t.endswith(".toml")):
            if basename(ref) not in existing:
                out.append(Finding(rec.path, f"Runs line {n}: Scenario {ref} does not exist"))
        if not _build_ok(build):
            bad(n, "Build ID", "is not a Build ID (<commit>[-d<hash>][-o<hash>]) or none", build)
        if not (_VERDICT.match(_plain(verdict)) or _COUNT.search(verdict)):
            bad(n, "Verdict", "is not a known verdict (PASS, FAIL, invalid, measurement, red, running, outage, "
                              "\"N of N pass\")", verdict)
        if not _record_ok(record):
            bad(n, "Record", "is neither a tools/arclog/runs/ path nor none", record)
    return out


def check_ticked_has_pass(rec: Record) -> list[Finding]:
    """A record with every criterion ticked has at least one PASS run."""
    if not rec.criteria or not all(c.ticked for c in rec.criteria):
        return []
    if any(is_pass(cells[3]) for _, cells in rec.runs or [] if len(cells) > 3):
        return []
    return [Finding(rec.path, f"all {len(rec.criteria)} criteria are ticked, but no Runs row has a PASS verdict")]


def _words(text: str) -> str:
    return " ".join(text.split())


def parse_issue_criteria(body: str) -> list[str]:
    """The acceptance criteria of an issue: the checklist under its `## Acceptance criteria` heading, whitespace
    normalised. The "> Copied from ..." lines a copied issue starts with are ignored with everything outside it."""
    items: list[str] = []
    inside = False
    for line in body.splitlines():
        if line.startswith("#"):
            inside = re.match(r"#{2,}\s+acceptance( criteria)?\s*$", line, re.I) is not None
            continue
        if not inside:
            continue
        m = re.match(r"^\s*- \[[ xX]\] ?(.*)$", line)
        if m:
            items.append(m.group(1))
        elif items and line.startswith((" ", "\t")) and line.strip():
            items[-1] += " " + line.strip()
    return [_words(i) for i in items]


def _copies(record_criterion: str, issue_criterion: str) -> bool:
    """The record copies the issue's criterion word for word; what follows is the evidence added after ticking."""
    return _words(record_criterion).startswith(issue_criterion)


def check_issue(rec: Record, issue: dict) -> list[Finding]:
    """The record's criteria are the issue's acceptance criteria, and a closed issue has them all ticked."""
    n = rec.kind.issue
    out: list[Finding] = []
    wanted = parse_issue_criteria(issue.get("body") or "")
    have = [c.text for c in rec.criteria or []]
    if not wanted:
        out.append(Finding(rec.path, f"issue #{n} has no checklist under `## Acceptance criteria`: "
                                     "its criteria cannot be compared with the record's"))
    else:
        for w in wanted:
            if not any(_copies(h, w) for h in have):
                out.append(Finding(rec.path, f"criterion of issue #{n} missing from the record: {_quote(w)}"))
        for h in have:
            if not any(_copies(h, w) for w in wanted):
                out.append(Finding(rec.path, f"criterion has no counterpart in issue #{n}: {_quote(h)}"))
    if str(issue.get("state", "")).upper() == "CLOSED":
        for c in rec.criteria or []:
            if not c.ticked:
                out.append(Finding(rec.path, f"issue #{n} is closed, but criterion is not ticked: {_quote(c.text)}"))
    return out


def gh_issue(number: int, cwd: Path | None = None) -> dict:
    """`gh issue view <n> --json number,state,body` in `cwd` (the tracker is the clone's default repo)."""
    cmd = ["gh", "issue", "view", str(number), "--json", "number,state,body"]
    try:
        done = subprocess.run(cmd, cwd=cwd, check=True, capture_output=True, text=True)
    except FileNotFoundError as exc:
        raise IssueUnavailable("gh is not installed") from exc
    except subprocess.CalledProcessError as exc:
        detail = (exc.stderr or exc.stdout or "").strip().splitlines()
        raise IssueUnavailable(detail[0] if detail else f"gh failed (exit {exc.returncode})") from exc
    try:
        return json.loads(done.stdout)
    except ValueError as exc:
        raise IssueUnavailable("gh printed something that is not JSON") from exc


def _slug_matches(scenario: str, slug: str) -> bool:
    stem = scenario[: -len(".toml")]
    return stem == slug or stem.startswith(slug + "-")


def check_pairing(records: list[Record], headers: dict[str, Header]) -> list[Finding]:
    """The round trip between scenarios and records, and the slug they share.
    `headers` maps each scenario file name to its parsed header."""
    by_name = {r.name: r for r in records}
    out: list[Finding] = []
    owned: set[tuple[str, str]] = set()      # (record, scenario): the scenario's header names the record

    for sname, h in sorted(headers.items()):
        path = f"{SCENARIOS}/{sname}"
        if h.record is None:
            out.append(Finding(path, "header names no Test Record (add \"Test Record docs/test-tracker/<file>.md\")"
                               if h.issues else "header names neither an issue nor a Test Record"))
            continue
        rec = by_name.get(basename(h.record))
        if rec is None or rec.kind is None:
            why = "is not a Test Record" if basename(h.record) in NOT_RECORDS or rec else "does not exist"
            out.append(Finding(path, f"header names Test Record {h.record}, which {why}"))
            continue
        if rec.kind.kind == ACCEPTANCE:
            if not h.issues:
                out.append(Finding(path, f"header names no issue, but {rec.name} is for #{rec.kind.issue}"))
            elif rec.kind.issue not in h.issues:
                out.append(Finding(path, f"header names issue #{h.issues[0]}, but {rec.name} is for #{rec.kind.issue}"))
        if sname not in rec.listed:
            out.append(Finding(path, f"header names Test Record {rec.name}, which does not list this scenario"))
        owned.add((rec.name, sname))

    for rec in records:
        if rec.kind is None:
            continue
        for cell in rec.scenarios or []:
            for ref in cell.files:
                sname = basename(ref)
                if sname not in headers:
                    continue                      # missing file: reported by check_scenario_refs
                if cell.shared:
                    for partner in cell.shared_with:
                        if partner not in by_name:
                            out.append(Finding(rec.path, f"Scenarios: {sname} is shared with {partner}, which does not exist"))
                        elif sname not in by_name[partner].listed:
                            out.append(Finding(rec.path, f"Scenarios: {sname} is shared with {partner}, "
                                                         f"which does not list {sname}"))
                    continue
                named = headers[sname].record
                named_rec = by_name.get(basename(named)) if named else None
                if named_rec is not None and named_rec.kind is not None and named_rec.name != rec.name:
                    out.append(Finding(rec.path, f"Scenarios: {sname}'s header names Test Record {named}, not this one "
                                                 f"(say \"shared with `{basename(named)}`\" if it serves both)"))
                # no (or no real) record in the header is reported on the scenario itself, once

    for rname, sname in sorted(owned):
        rec = by_name[rname]
        if rec.kind.kind == ACCEPTANCE and not _slug_matches(sname, rec.kind.slug):
            out.append(Finding(f"{SCENARIOS}/{sname}", f"name does not carry the slug `{rec.kind.slug}` of {rec.name} "
                                                       f"(expected {rec.kind.slug}.toml or {rec.kind.slug}-<variant>.toml)"))
    return out


# --- a repo --------------------------------------------------------------------------------------------


def check_repo(root: Path, fetch: Fetch | None = None) -> list[Finding]:
    """Every finding of the records and scenarios under `root`, sorted by file (stable within a file)."""
    tracker = root / TRACKER
    existing = {p.name for p in (root / SCENARIOS).glob("*.toml")}
    records = [parse_record(p.name, p.read_text(encoding="utf-8"))
               for p in sorted(tracker.glob("*.md")) if p.name not in NOT_RECORDS]
    headers = {p.name: parse_scenario_header(p.read_text(encoding="utf-8")) for p in sorted((root / SCENARIOS).glob("*.toml"))}
    out: list[Finding] = []
    for rec in records:
        out += check_name(rec)
        if rec.kind is None:
            continue
        out += check_sections(rec)
        out += check_scenario_refs(rec, existing)
        out += check_runs(rec, existing)
        out += check_ticked_has_pass(rec)
    if fetch is not None:
        for rec in records:
            if rec.kind is not None and rec.kind.kind == ACCEPTANCE:
                try:
                    out += check_issue(rec, fetch(rec.kind.issue))
                except IssueUnavailable as exc:
                    out.append(Finding(rec.path, f"cannot read issue #{rec.kind.issue}: {exc}"))
    out += check_pairing(records, headers)
    return sorted(out, key=lambda f: f.file)


def format_report(root: Path, found: list[Finding]) -> list[str]:
    """The lines `bench records check` prints: one per finding, then a summary."""
    records = [p for p in (root / TRACKER).glob("*.md") if p.name not in NOT_RECORDS]
    scenarios = list((root / SCENARIOS).glob("*.toml"))
    if not found:
        return [f"ok: {len(records)} records and {len(scenarios)} scenarios checked"]
    files = len({f.file for f in found})
    return [*map(str, found), f"{len(found)} finding{'s' * (len(found) != 1)} in {files} file{'s' * (files != 1)}"]
