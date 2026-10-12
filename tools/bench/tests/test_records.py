"""bench records check: an issue, its Test Record and its scenario agree.

The tests build a small repo in tmp_path (records under docs/test-tracker/, scenarios under tools/bench/scenarios/)
that passes every check, then break one thing at a time and read the findings."""

import json
import subprocess
from pathlib import Path

import pytest

from bench.records import (Finding, IssueUnavailable, check_repo, gh_issue, parse_issue_criteria, parse_scenario_header,
                           split_row)

TRACKER = "docs/test-tracker"
SCENARIOS = "tools/bench/scenarios"
DATA = Path(__file__).parent / "data"

ACCEPTANCE = """# Issue #7: the widget locks on hardware

Purpose: acceptance. Issue: #7.

## Scenarios

| Scenario | Proves or measures |
|---|---|
| `tools/bench/scenarios/widget-sync.toml` | The widget locks within 5 min |
| `tools/bench/scenarios/widget-sync-slow.toml` | The same on the slow profile |
| `tools/bench/scenarios/widget-soak.toml` (to write once the profile is chosen) | Several hours |
| `tools/bench/scenarios/shared-soak.toml` (shared with `spot-20261001-poke.md`) | The soak half of the criteria |
| `tools/bench/tests/test_widget.py::test_lock` | The lock logic, on the host |
| none (flags form, `-D WIDGET_SLOW=1`) | A one-off check |

## Hardware

| Item | Needed |
|---|---|
| Node 1 flashed as C3 | yes |

Hands on the bench: none.

## Criteria

- [x] The widget locks within 5 min.
- [ ] The slow variant locks within 20 min.

## Runs

| Date (UTC) | Scenario | Build ID | Verdict | Record | Notes |
|---|---|---|---|---|---|
| 2026-10-01 21:38 | `widget-sync.toml` | `2421333-o8f3c06` | PASS | `tools/arclog/runs/20261001T213849Z-2421333-o8f3c06/` | Locked at +40 s. |
| 2026-10-01 22:00 | flags: `--node 1=C3 -D WIDGET_SLOW=1` | `2421333` | FAIL (finding) | none, numbers here | Did not lock. |
| 2026-10-02 08:1x (2 attempts) | `widget-sync-slow.toml` | `a419c9b-dc1bd27-o954ffd` | invalid | none (the build failed) | A link error. |
"""

SPONTANEOUS = """# Spontaneous: poking the widget

Purpose: spontaneous. Issue: none.

## Scenarios

| Scenario | Proves or measures |
|---|---|
| `tools/bench/scenarios/shared-soak.toml` | The soak |

## Hardware

| Item | Needed |
|---|---|
| Node 2 | yes |

## Criteria

- [x] It soaks.

## Runs

| Date (UTC) | Scenario | Build ID | Verdict | Record | Notes |
|---|---|---|---|---|---|
| 2026-10-03 10:00 | `shared-soak.toml` | `e34092f-o954ffd` | PASS | `tools/arclog/runs/20261003T100000Z-e34092f-o954ffd/` | Soaked. |
"""

PERFORMANCE = """# Performance: widget jitter

Purpose: performance. Issue: none.

## Scenarios

| Scenario | Proves or measures |
|---|---|
| `tools/bench/scenarios/jitter.toml` (to write) | The jitter |

## Criteria

- [ ] The jitter is measured.

## Runs

| Date (UTC) | Scenario | Build ID | Verdict | Record | Notes |
|---|---|---|---|---|---|
"""


def scenario(purpose_line: str) -> str:
    return f"{purpose_line}\n# A second line of the header.\n\n[nodes]\n1 = \"C3\"\n"


WIDGET_SYNC = scenario("# Purpose: acceptance. Issue #7, Test Record docs/test-tracker/7-widget-sync.md.")
WIDGET_SYNC_SLOW = scenario("# Purpose: acceptance. Issue #7, Test Record\n# docs/test-tracker/7-widget-sync.md.")
SHARED_SOAK = scenario("# Purpose: spontaneous. Test Record docs/test-tracker/spot-20261001-poke.md, issue #9 (item 3).")

GOOD = {
    f"{TRACKER}/7-widget-sync.md": ACCEPTANCE,
    f"{TRACKER}/spot-20261001-poke.md": SPONTANEOUS,
    f"{TRACKER}/perf-jitter.md": PERFORMANCE,
    f"{TRACKER}/README.md": "# Test tracker\n",
    f"{TRACKER}/node-silences.md": "# Node silences\n",
    f"{SCENARIOS}/widget-sync.toml": WIDGET_SYNC,
    f"{SCENARIOS}/widget-sync-slow.toml": WIDGET_SYNC_SLOW,
    f"{SCENARIOS}/shared-soak.toml": SHARED_SOAK,
}


def edit(text: str, old: str, new: str) -> str:
    assert old in text, f"{old!r} is not in the fixture"
    return text.replace(old, new, 1)


def make_repo(tmp_path, **changes):
    """The passing repo with `changes` ({repo-relative path: new text, or None to delete the file}) applied."""
    files = {**GOOD, **changes}
    for rel, text in files.items():
        if text is not None:
            path = tmp_path / rel
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(text, encoding="utf-8")
    return tmp_path


def findings(tmp_path, fetch=None, **changes):
    return [str(f) for f in check_repo(make_repo(tmp_path, **changes), fetch)]


def only(found: list[str], file: str, fragment: str) -> None:
    """Exactly one finding, on `file`, whose message holds `fragment`."""
    assert len(found) == 1, found
    assert found[0].startswith(f"{file}: "), found
    assert fragment in found[0], found


ACC = f"{TRACKER}/7-widget-sync.md"
SPOT = f"{TRACKER}/spot-20261001-poke.md"
PERF = f"{TRACKER}/perf-jitter.md"


# --- the passing set ----------------------------------------------------------------------------


def test_a_repo_whose_records_and_scenarios_agree_has_no_finding(tmp_path):
    assert findings(tmp_path) == []


def test_a_finding_prints_as_the_file_then_the_message():
    assert str(Finding("docs/test-tracker/x.md", "no Runs table")) == "docs/test-tracker/x.md: no Runs table"


def test_a_markdown_file_that_is_no_kind_of_record_is_a_finding(tmp_path):
    found = findings(tmp_path, **{f"{TRACKER}/notes.md": "# Notes\n"})
    only(found, f"{TRACKER}/notes.md", "not a Test Record")


def test_the_tracker_readme_and_node_silences_are_not_records(tmp_path):
    assert findings(tmp_path, **{f"{TRACKER}/README.md": "# anything\n", f"{TRACKER}/node-silences.md": "# anything\n"}) == []


# --- Scenarios table ------------------------------------------------------------------------------


def test_a_record_naming_a_scenario_file_that_does_not_exist_is_a_finding(tmp_path):
    found = findings(tmp_path, **{ACC: edit(ACCEPTANCE, "widget-sync-slow.toml` | The same", "widget-gone.toml` | The same")})
    assert any(f.startswith(f"{ACC}: ") and "widget-gone.toml" in f and "does not exist" in f for f in found), found


def test_a_scenario_marked_to_write_may_be_missing_but_a_scenario_not_marked_may_not(tmp_path):
    # widget-soak.toml is missing in the passing set and marked "(to write ...)"; without the mark it is a finding
    found = findings(tmp_path, **{ACC: edit(ACCEPTANCE, " (to write once the profile is chosen)", "")})
    only(found, ACC, "widget-soak.toml")


def test_cells_that_are_not_scenario_files_are_left_alone(tmp_path):
    # a host test, a flags form and "none" sit in the passing set; so does a build command
    text = edit(ACCEPTANCE, "| none (flags form", "| `bench build --class C2 --clean` | zero warnings |\n| none: build only | host tests cover it |\n| none (flags form")
    assert findings(tmp_path, **{ACC: text}) == []


def test_a_record_without_a_scenarios_section_is_a_finding(tmp_path):
    text = ACCEPTANCE.replace("## Scenarios", "## Bench")
    found = findings(tmp_path, **{ACC: text})
    assert any(f.startswith(f"{ACC}: ") and "## Scenarios" in f for f in found), found


# --- the scenario header ----------------------------------------------------------------------------------


def test_the_header_names_the_issue_and_the_test_record_even_when_the_path_wraps():
    h = parse_scenario_header("# Purpose: acceptance. Issue #36, Test Record\n# docs/test-tracker/36-guard-from-drift.md.\n\n[nodes]\n")
    assert h.issues == (36,) and h.record == "docs/test-tracker/36-guard-from-drift.md"


def test_the_header_is_the_leading_block_of_comments_and_nothing_below_it():
    text = "# Written against the 30 s grid.\n# Issue #54: midnight.\n# Test Record docs/test-tracker/54-m.md\n[nodes]\n# Issue #99 later\n"
    h = parse_scenario_header(text)
    assert h.issues == (54,) and h.record == "docs/test-tracker/54-m.md"
    assert parse_scenario_header("[nodes]\n# Issue #99, Test Record docs/test-tracker/99-x.md\n").record is None


def test_a_spontaneous_header_may_name_its_issue_after_the_record():
    h = parse_scenario_header("# Purpose: spontaneous. Test Record docs/test-tracker/spot-20261004-pinode-c2.md, issue #78 (item 3).\n")
    assert h.issues == (78,) and h.record == "docs/test-tracker/spot-20261004-pinode-c2.md"


SMOKE = '# The smallest scenario: flash one board.\n\n[nodes]\n1 = "C3"\n'


def test_a_scenario_whose_header_names_neither_issue_nor_record_is_a_finding(tmp_path):
    found = findings(tmp_path, **{f"{SCENARIOS}/smoke.toml": SMOKE})
    only(found, f"{SCENARIOS}/smoke.toml", "neither an issue nor a Test Record")


def test_a_scenario_whose_header_names_an_issue_but_no_record_is_a_finding(tmp_path):
    text = scenario("# Issue #7: the widget locks.")
    found = findings(tmp_path, **{f"{SCENARIOS}/widget-sync.toml": text})
    only(found, f"{SCENARIOS}/widget-sync.toml", "names no Test Record")


def test_a_scenario_whose_header_names_a_record_that_does_not_exist_is_a_finding(tmp_path):
    text = edit(WIDGET_SYNC, "7-widget-sync.md", "7-widget-gone.md")
    found = findings(tmp_path, **{f"{SCENARIOS}/widget-sync.toml": text})
    only(found, f"{SCENARIOS}/widget-sync.toml", "docs/test-tracker/7-widget-gone.md, which does not exist")


def test_a_scenario_must_name_the_issue_of_its_acceptance_record(tmp_path):
    text = edit(WIDGET_SYNC, "Issue #7,", "Issue #8,")
    found = findings(tmp_path, **{f"{SCENARIOS}/widget-sync.toml": text})
    only(found, f"{SCENARIOS}/widget-sync.toml", "names issue #8, but")
    text = edit(WIDGET_SYNC, "Issue #7, ", "")
    found = findings(tmp_path, **{f"{SCENARIOS}/widget-sync.toml": text})
    only(found, f"{SCENARIOS}/widget-sync.toml", "names no issue")


def test_a_scenario_whose_record_does_not_list_it_back_is_a_finding(tmp_path):
    text = edit(ACCEPTANCE, "| `tools/bench/scenarios/widget-sync-slow.toml` | The same on the slow profile |\n", "")
    found = findings(tmp_path, **{ACC: text})
    only(found, f"{SCENARIOS}/widget-sync-slow.toml", "7-widget-sync.md, which does not list this scenario")


def test_a_record_listing_a_scenario_that_belongs_to_another_record_is_a_finding_unless_it_says_shared(tmp_path):
    text = edit(PERFORMANCE, "`tools/bench/scenarios/jitter.toml` (to write)", "`tools/bench/scenarios/widget-sync.toml`")
    found = findings(tmp_path, **{PERF: text})
    only(found, PERF, "widget-sync.toml's header names Test Record docs/test-tracker/7-widget-sync.md")
    text = edit(PERFORMANCE, "`tools/bench/scenarios/jitter.toml` (to write)",
                "`tools/bench/scenarios/widget-sync.toml` (shared with `7-widget-sync.md`)")
    assert findings(tmp_path, **{PERF: text}) == []


def test_a_scenario_without_a_record_in_its_header_is_reported_once_not_also_on_the_record_that_lists_it(tmp_path):
    found = findings(tmp_path, **{f"{SCENARIOS}/widget-sync.toml": scenario("# Issue #7: the widget locks.")})
    assert [f.split(":")[0] for f in found] == [f"{SCENARIOS}/widget-sync.toml"]


def test_a_shared_scenario_names_records_that_exist_and_list_it(tmp_path):
    text = edit(ACCEPTANCE, "(shared with `spot-20261001-poke.md`)", "(shared with `spot-20261001-gone.md`)")
    found = findings(tmp_path, **{ACC: text})
    only(found, ACC, "shared-soak.toml is shared with spot-20261001-gone.md, which does not exist")
    text = edit(SPONTANEOUS, "| `tools/bench/scenarios/shared-soak.toml` | The soak |\n", "")
    found = findings(tmp_path, **{SPOT: text})
    assert any(f.startswith(f"{ACC}: ") and "spot-20261001-poke.md, which does not list shared-soak.toml" in f for f in found), found


# --- the slug -----------------------------------------------------------------------------------------------


def test_an_acceptance_record_and_its_scenarios_share_the_slug(tmp_path):
    # the variant widget-sync-slow.toml is in the passing set; a scenario with another name is not
    renamed = {f"{SCENARIOS}/widget-sync-slow.toml": None, f"{SCENARIOS}/slow-widget.toml": WIDGET_SYNC_SLOW,
               ACC: ACCEPTANCE.replace("widget-sync-slow.toml", "slow-widget.toml")}
    found = findings(tmp_path, **renamed)
    only(found, f"{SCENARIOS}/slow-widget.toml", "carry the slug `widget-sync` of 7-widget-sync.md")


def test_the_slug_rule_is_for_acceptance_records_only(tmp_path):
    # shared-soak.toml belongs to a spontaneous record and is shared with an acceptance one: no finding either way
    assert findings(tmp_path) == []


# --- the Runs table -----------------------------------------------------------------------------------------

ROW = "| 2026-10-01 21:38 | `widget-sync.toml` | `2421333-o8f3c06` | PASS | `tools/arclog/runs/20261001T213849Z-2421333-o8f3c06/` | Locked at +40 s. |"


def with_row(row: str, text: str = ACCEPTANCE) -> str:
    return edit(text, ROW, row)


def test_the_cells_of_a_row_are_split_on_pipes_that_are_not_escaped():
    assert split_row("| a | `b` | c \\| d | |") == ["a", "`b`", "c | d", ""]


@pytest.mark.parametrize("cell", ["2026-10-01 21:38", "2026-10-05 17:4x (2 attempts)", "2026-10-05 22:3x"])
def test_the_date_of_a_run_may_be_imprecise(tmp_path, cell):
    assert findings(tmp_path, **{ACC: with_row(ROW.replace("2026-10-01 21:38", cell))}) == []


@pytest.mark.parametrize("cell", ["yesterday", "2026-10-01", "2026-13-01 21:38", "2026-10-01 25:38", "01/10/2026 21:38"])
def test_a_run_whose_date_is_not_a_date_and_time_is_a_finding(tmp_path, cell):
    found = findings(tmp_path, **{ACC: with_row(ROW.replace("2026-10-01 21:38", cell))})
    only(found, ACC, "Date")


@pytest.mark.parametrize("cell", ["`widget-sync.toml` (first version)", "flags: `--node 1=C3`", "flags, `-D X=1`", "same",
                                  "(trace of the always-on capture)", "`bench boards --probe-uid x` (no scenario)"])
def test_the_scenario_of_a_run_may_be_a_file_or_free_text_for_a_flags_form_or_a_host_test(tmp_path, cell):
    assert findings(tmp_path, **{ACC: with_row(ROW.replace("`widget-sync.toml`", cell))}) == []


def test_a_run_of_a_scenario_that_does_not_exist_or_with_no_scenario_cell_is_a_finding(tmp_path):
    found = findings(tmp_path, **{ACC: with_row(ROW.replace("`widget-sync.toml`", "`widget-gone.toml`"))})
    only(found, ACC, "widget-gone.toml")
    found = findings(tmp_path, **{ACC: with_row(ROW.replace("| `widget-sync.toml` |", "|  |"))})
    only(found, ACC, "Scenario")


@pytest.mark.parametrize("cell", ["`2421333`", "`2421333-o8f3c06`", "`a419c9b-dc1bd27-o954ffd`", "`a419c9b`, `b1759a2`",
                                  "n/a", "none (nothing was built)", "(several)"])
def test_the_build_id_of_a_run_is_a_build_id_or_says_there_was_none(tmp_path, cell):
    assert findings(tmp_path, **{ACC: with_row(ROW.replace("`2421333-o8f3c06`", cell))}) == []


@pytest.mark.parametrize("cell", ["`abc`", "2421333-x8f3c06", "`2421333-o8f3c0`", "latest", ""])
def test_a_run_whose_build_id_is_not_one_is_a_finding(tmp_path, cell):
    found = findings(tmp_path, **{ACC: with_row(ROW.replace("`2421333-o8f3c06`", cell))})
    only(found, ACC, "Build ID")


@pytest.mark.parametrize("cell", ["PASS", "FAIL (finding)", "FAIL (bench)", "invalid (bench bug)", "measurement",
                                  "red (side check)", "outage, recovered", "running", "48 of 48 pass", "pass, pass, FAIL",
                                  "**FAIL**", "PASS (criterion 1)", "A: 10 of 10 pass. B: 9 of 10 pass"])
def test_the_verdict_of_a_run_may_be_any_word_the_records_use(tmp_path, cell):
    assert findings(tmp_path, **{ACC: with_row(ROW.replace("| PASS |", f"| {cell} |"))}) == []


@pytest.mark.parametrize("cell", ["maybe", "ok", "", "2 of 3"])
def test_a_run_with_a_verdict_nobody_uses_is_a_finding(tmp_path, cell):
    found = findings(tmp_path, **{ACC: with_row(ROW.replace("| PASS |", f"| {cell} |"))})
    only(found, ACC, "Verdict")


@pytest.mark.parametrize("cell", ["`tools/arclog/runs/20261001T213849Z-x/`", "none", "none (the build failed)",
                                  "`tools/arclog/runs/bench/com8-20260929.log`, `com9-20260929.log`",
                                  "`tools/arclog/runs/` (aborted at 21:37)"])
def test_the_record_of_a_run_is_a_path_under_the_runs_folder_or_says_none(tmp_path, cell):
    assert findings(tmp_path, **{ACC: with_row(ROW.replace("`tools/arclog/runs/20261001T213849Z-2421333-o8f3c06/`", cell))}) == []


@pytest.mark.parametrize("cell", ["somewhere", "`runs/20261001/`", ""])
def test_a_run_whose_record_is_neither_a_runs_path_nor_none_is_a_finding(tmp_path, cell):
    found = findings(tmp_path, **{ACC: with_row(ROW.replace("`tools/arclog/runs/20261001T213849Z-2421333-o8f3c06/`", cell))})
    only(found, ACC, "Record")


def test_a_run_with_too_few_or_too_many_columns_is_a_finding(tmp_path):
    found = findings(tmp_path, **{ACC: with_row("| 2026-10-01 21:38 | `widget-sync.toml` | `2421333` | PASS | none |")})
    only(found, ACC, "5 columns, expected 6")
    found = findings(tmp_path, **{ACC: with_row(ROW + " extra |")})
    only(found, ACC, "7 columns, expected 6")


def test_a_runs_table_with_other_columns_is_a_finding(tmp_path):
    found = findings(tmp_path, **{ACC: edit(ACCEPTANCE, "| Date (UTC) | Scenario | Build ID | Verdict | Record | Notes |",
                                            "| Date | Scenario | Build ID | Verdict | Record | Notes |")})
    only(found, ACC, "Runs header")


def test_a_finding_in_a_run_names_the_line_of_the_row(tmp_path):
    found = findings(tmp_path, **{ACC: with_row(ROW.replace("| PASS |", "| maybe |"))})
    line = 1 + next(i for i, l in enumerate(ACCEPTANCE.splitlines()) if l == ROW)
    assert f"Runs line {line}: " in found[0]


# --- every criterion ticked -----------------------------------------------------------------------------------


def all_ticked(text: str = ACCEPTANCE) -> str:
    return edit(text, "- [ ] The slow variant", "- [x] The slow variant")


def test_a_record_with_every_criterion_ticked_needs_a_pass_run(tmp_path):
    assert findings(tmp_path, **{ACC: all_ticked()}) == []
    no_pass = all_ticked(with_row(ROW.replace("| PASS |", "| FAIL |")))
    found = findings(tmp_path, **{ACC: no_pass})
    only(found, ACC, "all 2 criteria are ticked, but no Runs row has a PASS verdict")


def test_a_count_of_passes_is_a_pass_and_the_rule_is_for_a_record_with_criteria_all_ticked(tmp_path):
    counted = all_ticked(with_row(ROW.replace("| PASS |", "| 48 of 48 pass |")))
    assert findings(tmp_path, **{ACC: counted}) == []
    unticked = with_row(ROW.replace("| PASS |", "| FAIL |"))          # one criterion is still open
    assert findings(tmp_path, **{ACC: unticked}) == []
    no_criteria = edit(ACCEPTANCE, "- [x] The widget locks within 5 min.\n- [ ] The slow variant locks within 20 min.\n", "")
    assert findings(tmp_path, **{ACC: with_row(ROW.replace("| PASS |", "| FAIL |"), no_criteria)}) == []


# --- --online: the issue on GitHub ------------------------------------------------------------------------------
# tests/data/gh-issue-7.json is what `gh issue view 7 --json number,state,body` prints, trimmed to a small issue.

GH_ISSUE_7 = (DATA / "gh-issue-7.json").read_text(encoding="utf-8")


def issue_7(**changes) -> dict:
    return {**json.loads(GH_ISSUE_7), **changes}


def fetching(issue: dict | None = None, calls: list | None = None):
    def fetch(number: int) -> dict:
        if calls is not None:
            calls.append(number)
        if number != 7:
            raise AssertionError(f"issue #{number} should not be read")
        return issue if issue is not None else issue_7()
    return fetch


def test_the_acceptance_criteria_of_an_issue_are_the_checklist_under_its_heading():
    criteria = parse_issue_criteria(issue_7()["body"])
    assert criteria == ["The widget locks within 5 min.", "The slow variant locks within 20 min."]
    assert parse_issue_criteria("## Acceptance\n\n- plain bullet\n- [x] done\n") == ["done"]
    assert parse_issue_criteria("## Problem\n\n- [ ] not a criterion\n") == []
    assert parse_issue_criteria("") == []


def test_a_record_that_copies_the_issue_word_for_word_passes_even_with_the_evidence_appended(tmp_path):
    text = edit(ACCEPTANCE, "- [x] The widget locks within 5 min.", "- [x] The widget locks within 5 min. (+40 s, 2026-10-01)")
    assert findings(tmp_path, fetching(), **{ACC: text}) == []


def test_only_acceptance_records_are_compared_with_their_issue(tmp_path):
    calls: list[int] = []
    assert findings(tmp_path, fetching(calls=calls)) == []
    assert calls == [7]


def test_offline_never_asks_github(tmp_path, monkeypatch):
    def no_process(*args, **kwargs):
        raise AssertionError("offline must not run gh")
    monkeypatch.setattr(subprocess, "run", no_process)
    assert findings(tmp_path) == []


def test_a_criterion_that_differs_between_issue_and_record_is_a_finding_both_ways(tmp_path):
    text = edit(ACCEPTANCE, "locks within 20 min.", "locks within 25 min.")
    found = findings(tmp_path, fetching(), **{ACC: text})
    assert len(found) == 2 and all(f.startswith(f"{ACC}: ") for f in found), found
    assert "The slow variant locks within 20 min." in found[0] and "missing from the record" in found[0]
    assert "The slow variant locks within 25 min." in found[1] and "no counterpart in issue #7" in found[1]


def test_whitespace_is_not_a_difference(tmp_path):
    text = edit(ACCEPTANCE, "- [ ] The slow variant locks within 20 min.", "- [ ] The slow  variant\n  locks within 20 min.")
    assert findings(tmp_path, fetching(), **{ACC: text}) == []


def test_an_issue_without_an_acceptance_checklist_cannot_be_compared(tmp_path):
    found = findings(tmp_path, fetching(issue_7(body="## Acceptance\n\n- plain bullets only\n")))
    only(found, ACC, "issue #7 has no checklist under `## Acceptance criteria`")


def test_a_closed_issue_needs_every_criterion_ticked_in_the_record(tmp_path):
    found = findings(tmp_path, fetching(issue_7(state="CLOSED")))
    only(found, ACC, 'issue #7 is closed, but criterion is not ticked: "The slow variant locks within 20 min."')
    assert findings(tmp_path, fetching(issue_7(state="CLOSED")), **{ACC: all_ticked()}) == []
    assert findings(tmp_path, fetching(issue_7(state="OPEN")), **{ACC: all_ticked()}) == []


def test_an_issue_that_cannot_be_read_is_a_finding_on_the_record(tmp_path):
    def unreadable(number: int) -> dict:
        raise IssueUnavailable("GraphQL: Could not resolve to an issue")
    found = findings(tmp_path, unreadable)
    only(found, ACC, "cannot read issue #7: GraphQL: Could not resolve to an issue")


def fake_gh(monkeypatch, stdout=GH_ISSUE_7, returncode=0, stderr="", missing=False):
    calls = []

    def run(cmd, **kwargs):
        calls.append((cmd, kwargs))
        if missing:
            raise FileNotFoundError("gh")
        if returncode:
            raise subprocess.CalledProcessError(returncode, cmd, output="", stderr=stderr)
        return subprocess.CompletedProcess(cmd, 0, stdout=stdout, stderr="")
    monkeypatch.setattr(subprocess, "run", run)
    return calls


def test_gh_issue_asks_gh_for_the_number_the_state_and_the_body_in_the_repo(tmp_path, monkeypatch):
    calls = fake_gh(monkeypatch)
    issue = gh_issue(7, tmp_path)
    assert issue["state"] == "OPEN" and "## Acceptance criteria" in issue["body"]
    [(cmd, kwargs)] = calls
    assert cmd == ["gh", "issue", "view", "7", "--json", "number,state,body"]
    assert kwargs["cwd"] == tmp_path


def test_gh_issue_turns_every_failure_into_one_message(tmp_path, monkeypatch):
    fake_gh(monkeypatch, returncode=1, stderr="GraphQL: Could not resolve to an issue with the number of 7.\nmore")
    with pytest.raises(IssueUnavailable, match="Could not resolve to an issue with the number of 7"):
        gh_issue(7, tmp_path)
    fake_gh(monkeypatch, missing=True)
    with pytest.raises(IssueUnavailable, match="gh is not installed"):
        gh_issue(7, tmp_path)
    fake_gh(monkeypatch, stdout="not json")
    with pytest.raises(IssueUnavailable, match="not JSON"):
        gh_issue(7, tmp_path)


# --- the command ----------------------------------------------------------------------------------------------------


def test_bench_records_check_exits_0_and_says_ok_when_everything_agrees(tmp_path, capsys):
    from bench.cli import main

    repo = make_repo(tmp_path)
    assert main(["--repo", str(repo), "records", "check"]) == 0
    out = capsys.readouterr().out.splitlines()
    assert out == ["ok: 3 records and 3 scenarios checked"]


def test_bench_records_check_exits_1_with_one_line_per_finding_naming_the_file(tmp_path, capsys):
    from bench.cli import main

    repo = make_repo(tmp_path, **{f"{SCENARIOS}/smoke.toml": SMOKE, ACC: with_row(ROW.replace("| PASS |", "| maybe |"))})
    assert main(["--repo", str(repo), "records", "check"]) == 1
    out = capsys.readouterr().out.splitlines()
    assert out[0].startswith(f"{ACC}: Runs line ")
    assert out[1].startswith(f"{SCENARIOS}/smoke.toml: ")
    assert out[-1] == "2 findings in 2 files"


def test_bench_records_check_online_reads_the_issues_with_gh(tmp_path, capsys, monkeypatch):
    from bench.cli import main

    repo = make_repo(tmp_path)
    calls = fake_gh(monkeypatch, stdout=json.dumps(issue_7(state="CLOSED")))
    assert main(["--repo", str(repo), "records", "check", "--online"]) == 1
    assert calls[0][0][:4] == ["gh", "issue", "view", "7"]
    out = capsys.readouterr().out
    assert "issue #7 is closed, but criterion is not ticked" in out
    capsys.readouterr()
    fake_gh(monkeypatch)
    assert main(["--repo", str(repo), "records", "check", "--online"]) == 0


def test_bench_records_check_refuses_a_directory_without_records(tmp_path, capsys):
    from bench.cli import main

    assert main(["--repo", str(tmp_path), "records", "check"]) == 3
    assert "no docs/test-tracker" in capsys.readouterr().err


def test_a_scenario_whose_header_names_no_record_is_not_held_to_the_slug_of_a_record_that_lists_it(tmp_path):
    # the header finding is the one to fix first; the slug is judged once the header names the record
    text = ACCEPTANCE.replace("widget-sync-slow.toml", "other-name.toml")
    changes = {f"{SCENARIOS}/widget-sync-slow.toml": None, f"{SCENARIOS}/other-name.toml": scenario("# Issue #7: slow."), ACC: text}
    found = findings(tmp_path, **changes)
    only(found, f"{SCENARIOS}/other-name.toml", "names no Test Record")


def test_a_scenario_can_say_it_is_shared_in_the_description_column(tmp_path):
    text = edit(ACCEPTANCE, "(shared with `spot-20261001-poke.md`) | The soak half of the criteria |",
                "| The soak half of the criteria (shared with `spot-20261001-poke.md`) |")
    assert findings(tmp_path, **{ACC: text}) == []


def test_headings_inside_a_fenced_block_do_not_open_a_section(tmp_path):
    fenced = ACCEPTANCE + "\n## Notes\n\n```markdown\n## Runs\n\n| bad |\n```\n"
    assert findings(tmp_path, **{ACC: fenced}) == []
