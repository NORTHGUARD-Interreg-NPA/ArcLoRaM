# Issue #79: check that an issue, its Test Record and its scenario agree

Purpose: acceptance. Issue: #79 (check that an issue, its Test Record and its scenario agree).

`bench records check` reads the Test Records and the scenarios as text and prints where they disagree with each other.
`--online` also reads each acceptance record's issue with `gh`, read-only.
Nothing here needs a board: the rules are covered by host tests, and the command is run on the repo itself.
This record is one of the records the command reads, so running it with `--online` proves that the wording below matches the issue.

## Scenarios

| Scenario | Proves or measures |
|---|---|
| none | Host tests only (`tools/bench/tests/test_records.py`) and the run of the command on the repo. There is nothing for a board to show |

## Hardware

| Item | Needed |
|---|---|
| none: the check reads text, and `--online` runs `gh issue view` read-only | no |

Hands on the bench: none.

## Criteria

- [x] Host tests with small fixture records and scenarios: one passing set, and one failing case per check above.
  `tools/bench/tests/test_records.py` builds a passing repo in a temporary directory and breaks it one rule at a time (table below).
- [x] `bench records check` runs on the repo as it is and prints its findings (the old records will have some).
  Run on 2026-10-12 it printed 16 findings in 15 files offline and 101 in 28 files with `--online`, all on records and scenarios written before the check (section Findings on the old records).
  The records touched in the days before (`spot-20261010-onboard-board.md`, this one) come out clean.
- [x] `--online` works against a recorded `gh` output in the tests.
  `tools/bench/tests/data/gh-issue-7.json` is the shape `gh issue view --json number,state,body` prints, with an invented issue.
  `test_bench_records_check_online_reads_the_issues_with_gh` runs the command on it, and `test_gh_issue_*` cover the call and its three failures (no `gh`, `gh` fails, not JSON).
- [x] Documented in `tools/bench/README.md` and referenced from the test-tracker README.
  `tools/bench/README.md`, section Checking the Test Records, and one line in the command table; `docs/test-tracker/README.md`, section Issue, Test Record, scenario.

## Host tests

Each bullet of "What to build" in the issue and the tests that break it:

| Rule of the issue | Tests (`test_records.py`) |
|---|---|
| a Test Record names scenarios that exist, or marks them "to write" | `test_a_record_naming_a_scenario_file_that_does_not_exist_is_a_finding`, `test_a_scenario_marked_to_write_may_be_missing_but_a_scenario_not_marked_may_not`, `test_cells_that_are_not_scenario_files_are_left_alone`, `test_a_record_without_a_scenarios_section_is_a_finding` |
| a scenario's header names the issue and the Test Record, and the record names the scenario back | `test_a_scenario_whose_header_names_neither_issue_nor_record_is_a_finding`, `..._an_issue_but_no_record_...`, `..._a_record_that_does_not_exist_...`, `test_a_scenario_must_name_the_issue_of_its_acceptance_record`, `test_a_scenario_whose_record_does_not_list_it_back_is_a_finding`, `test_a_record_listing_a_scenario_that_belongs_to_another_record_is_a_finding_unless_it_says_shared`, `test_a_shared_scenario_names_records_that_exist_and_list_it`, and the header reading (`test_the_header_*`) |
| record and scenario share the slug | `test_an_acceptance_record_and_its_scenarios_share_the_slug`, `test_the_slug_rule_is_for_acceptance_records_only` |
| Runs rows are well formed: date, scenario that exists, Build ID, verdict, record path | `test_the_date_of_a_run_*`, `test_a_run_whose_date_*`, `test_a_run_of_a_scenario_that_does_not_exist_*`, `test_the_build_id_of_a_run_*`, `test_the_verdict_of_a_run_*`, `test_the_record_of_a_run_*`, `test_a_run_with_too_few_or_too_many_columns_*`, `test_a_runs_table_with_other_columns_*`, `test_a_finding_in_a_run_names_the_line_of_the_row` |
| a record with every criterion ticked has a PASS row in Runs | `test_a_record_with_every_criterion_ticked_needs_a_pass_run`, `test_a_count_of_passes_is_a_pass_*` |
| `--online`: the criteria match the issue word for word, and a closed issue has all its criteria ticked | `test_the_acceptance_criteria_of_an_issue_*`, `test_a_record_that_copies_the_issue_word_for_word_*`, `test_a_criterion_that_differs_between_issue_and_record_is_a_finding_both_ways`, `test_whitespace_is_not_a_difference`, `test_an_issue_without_an_acceptance_checklist_*`, `test_a_closed_issue_needs_every_criterion_ticked_in_the_record`, `test_an_issue_that_cannot_be_read_*`, `test_offline_never_asks_github` |
| exit non-zero on any finding, one line per finding naming the file | `test_bench_records_check_exits_0_and_says_ok_*`, `test_bench_records_check_exits_1_with_one_line_per_finding_naming_the_file`, `test_bench_records_check_refuses_a_directory_without_records` (exit 3) |
| a passing set | `test_a_repo_whose_records_and_scenarios_agree_has_no_finding` |

## Findings on the old records

The issue says the old records are not rewritten for the check: a finding is fixed when its record or scenario is next touched.
This is the list of 2026-10-12, on `main` at `6199b22`; the command gives the live one.

Offline (16 findings in 15 files):

| File | Finding |
|---|---|
| `70-c3-first-sync-packet.md` | written before the template: no `## Scenarios` and no `## Criteria` section (2 findings) |
| `spot-20261005-build-alternation.md` | Runs line 69: the Build ID cell is free text ("`df5bd6e` and its dirty variants") |
| `c2-packet1-set-offset.toml`, `c2-sync-silence.toml`, `c3-first-sync-packet.toml`, `midnight-c3-c2.toml`, `midnight-date-c3-c2.toml` | the header names an issue but no Test Record |
| `c2-rejoin-after-reset.toml`, `c3-c2-sync.toml`, `smoke-c2.toml` | the header names neither an issue nor a Test Record (generic scenarios with no issue: a record is still due, or the rule gets an exception when one is next touched) |
| `calr-boot.toml`, `sync-budget-c3.toml` | the name does not carry the slug `runtime-calibration` of `34-runtime-calibration.md` |
| `guard-warm-window.toml` | the name does not carry the slug `guard-from-drift` of `36-guard-from-drift.md` |
| `sync-profile-dev.toml`, `sync-profile-dev-burst.toml` | the name does not carry the slug `sync-schedule` of `45-sync-schedule.md` |

With `--online`, 85 more findings in 14 records:

| Record | Findings | Cause |
|---|---|---|
| `54-midnight-c3-c2.md`, `55-packet1-set-offset.md`, `60-multi-port-capture-24h.md`, `69-zero-warning-build.md`, `70-c3-first-sync-packet.md` | 1 each | the issue has no `- [ ]` checklist under `## Acceptance criteria` (plain bullets under `## Acceptance` or `## Done when`, or none), so there is nothing to compare |
| `28-sync-date-midnight.md` | 11 | criteria reworded in the record, or changed in the issue since |
| `33-sync-silence.md` | 20 | the same |
| `34-runtime-calibration.md` | 12 | the same |
| `36-guard-from-drift.md` | 4 | the same |
| `45-sync-schedule.md` | 2 | the same |
| `75-alarm-a-write-race.md` | 9 | the same |
| `76-phase1-baseline.md` | 18 | the same |
| `82-fine-stamps.md` | 2 | the same |
| `86-run-validity.md` | 2 | the same |

The fix is on the issue side or the record side, whichever is right, and "the issue changes first" (`README.md`, Issue, Test Record, scenario).

## Runs

| Date (UTC) | Scenario | Build ID | Verdict | Record | Notes |
|---|---|---|---|---|---|
| 2026-10-12 12:10 | host tests: `uv run --project tools/bench pytest tools/bench/tests -q` | none | PASS | none | 496 passed, 97 of them in `test_records.py`; 399 on `main` before. Branch `feat/79-records-check` rebased on `main` at `6199b22`, no conflict |
| 2026-10-12 12:14 | `bench records check` and `bench records check --online` on the repo | none | PASS | none | The command ran and printed its findings: exit 1 with 16 findings in 15 files offline, exit 1 with 101 in 28 files online, all on old records (section Findings on the old records). Exit 1 is the designed answer to a finding. `spot-20261010-onboard-board.md` and this record have none |
