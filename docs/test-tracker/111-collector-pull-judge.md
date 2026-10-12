# #111 "Collector export and judge for days-long runs of 5 to 10 nodes": pull and judge from the collector

Purpose: acceptance. Issue: #111 "Collector export and judge for days-long runs of 5 to 10 nodes".

The judge of #86 asked the log collector once for the whole run, so a run over 10000 lines got only the newest page and failed its smoke check, and it overwrote the run folder with that answer.
This issue copies the collector's lines into the run folder by paging (`python -m bench.pull`), judges the folder by streaming it (`python -m bench.collector`), reads the record with the schema of the build that made it, and prints a summary of fixed size.
Host-only except for one re-judge of a record that already exists, which only reads the collector.

## Scenarios

| Scenario | Proves or measures |
|---|---|
| `tools/bench/scenarios/sync-dev-soak-swapped.toml` | the swap soak of 2026-10-07 (Node 2 as C3, Node 5 as C2, 18 h), re-judged by pull plus judge from the collector alone: PASS with 1800 `SLOT` on both nodes |

## Hardware

| Item | Needed | Today |
|---|---|---|
| Log collector, read only (`GET /logs`, `GET /nodes`) | yes | answers; it holds the soak of 2026-10-07 from 04:16 UTC |
| Boards, probes, COM ports | none | Nodes 2 and 5 were running another session's run (#36): nothing here touches a board or a Pi Node |

Hands on the bench: none.

## Criteria

- [x] Host: a fake collector with a line cap and several nodes; the pull returns every line exactly once and in order whatever the cap, a second pull adds nothing, and a smaller answer never shrinks the folder. (`test_pull.py`: `test_every_line_is_pulled_exactly_once_and_in_order_whatever_the_cap` with a cap of 1, 7, 100 and 100000 lines, `test_a_second_pull_adds_nothing`, `test_a_collector_that_starts_its_storage_over_never_shrinks_the_folder_and_makes_the_node_unfit`; also an interrupted pull goes on without a duplicate or a hole, and a line the collector recovered after a reconnect is pulled once.)
- [x] Host: a pull that hits the cap before the start of the run makes the judge report an unfit record, not a FAIL on a missing `BOOT`. (`test_collector.py`: `test_a_pull_that_hit_the_collector_s_retention_before_the_start_is_an_unfit_record_not_a_missing_boot`. "The cap" is read as the collector's store no longer holding the start of the run (its oldest line for the node is after it); a cap on one answer (`limit`) is what the paging is for. The judge says `INVALID the record is not whole`, exit 4, and prints no verdict.)
- [x] Host: two runs of the same build in one folder do not lose each other's first lines. (`test_pull.py::test_two_runs_of_the_same_build_in_one_folder_keep_each_others_first_lines`: the pull keeps the lines by the collector's id and not by their text; `test_collector.py::test_two_runs_of_the_same_build_in_one_folder_are_each_judged_with_their_first_lines`: each run is judged with its own `BOOT`, with `--until` ending the first.)
- [x] Host: a synthetic record of 10 nodes over 72 h is pulled and judged inside a stated time and memory bound. (`test_scale.py`, run with `pytest -m scale`: 520450 lines, pulled in 19 s and 23 s in two runs (the second after merging `main`), 32 requests, judged in 16 s at a peak of 27 MB; the bounds are 120 s, 90 s and 100 MB. The same 27 MB for 18 h and for 72 h of the same nodes, so the memory follows the expectations and not the run.)
- [x] Host: the printed report of that synthetic record is no longer than that of the 18 h soak (about 30 lines), with the per-stage detail in a report file in the run folder. (`test_scale.py`: 13 lines printed for 10 nodes with 720 staged expectations, more than 700 lines in `judge.log`; `test_collector.py::test_the_summary_is_a_verdict_a_line_per_node_and_the_record_and_the_detail_goes_to_a_file`, `test_verbose_prints_the_detail_too`, `test_the_causes_shown_are_the_first_five_and_the_rest_are_counted`.)
- [x] Bench: the swap soak of 2026-10-07 judged by pull plus judge from the collector alone, with no host capture, gives PASS with the same counts as the rebuilt record (1800 `SLOT` on both nodes). (Run of 2026-10-08 12:23 UTC below.)
- [x] The collector's retention and storage for days of 10 nodes are written down from `/nodes`, and a pull that is `truncated` is never judged. (`tools/bench/README.md`, "What the collector keeps": 66 KB an hour for a C2, 37 KB for a C3, a store of 100 MiB per node, 48 MB for 10 C2 nodes over 72 h. `test_collector.py::test_a_pull_cut_short_is_never_judged` and `test_a_pull_that_is_still_truncated_is_never_judged`.)
- [x] The record names the build and schema it was made with, so the judge runs with that schema and not the checkout's. (`pull.json` names the build, the firmware commit and the schema source, `schema.json` keeps the table; `test_collector.py::test_the_record_is_judged_with_the_schema_of_its_build_and_not_the_checkouts`, `test_pull.py::test_the_schema_of_the_build_is_kept_in_the_folder`. The fixture of `test_collector.py` stays the log of a build from before #36 (no `g` and `win` on `RX_WIN`), judged with the schema of its build; `e8ae58f` on `main` had repaired the same five failing tests by adding `g` and `win` to the sample lines, and this change keeps one fix, the build's schema.)
- [x] The bench skill tells an agent to decide a run from the summary only, never from the raw record or the stage lines. (`.claude/skills/bench/SKILL.md`, "Runs decided from the log collector".)

## Runs

| Date (UTC) | Scenario | Build ID | Verdict | Record | Notes |
|---|---|---|---|---|---|
| 2026-10-08 12:23 | `sync-dev-soak-swapped.toml` | `e34092f-o954ffd` | PASS | `tools/arclog/runs/20261007T140655Z-e34092f-o954ffd-pulled/` | Not a new run: the soak of 2026-10-07 14:06 (`spot-20261007-node2-as-c3.md`) judged again from the collector alone, 18 h later, by the pull and the judge of this issue, into a new folder; no host capture, no `limit` by hand. Pull and judge took 6 s: `PASS +36115.2s`, `SLOT (1800/1800)` on Node 2 (+36095.2 s) and Node 5 (+36115.2 s), the C2 `CLK to=WARM` at +155.5 s, schema `commit e34092f` (the checkout's has `g` and `win` on `RX_WIN`, this build does not print them). Four lines printed for two nodes. Compared with the hand-rebuilt record (`...-collector/`): none of its lines is missing from the pull; the pull holds 6 (Node 2) and 16 (Node 5) more, the last ones, from 08:43:31 to 08:45 UTC. Without `--until` the judge also reads the reflash of both nodes at 08:51 UTC (another session's) as four `WARN` lines and exits 0; with `--until 2026-10-08T08:45:00Z` it prints none. |
| 2026-10-12 12:16 | `sync-dev-soak-swapped.toml` | `e34092f-o954ffd` | PASS (exit 4, see Notes) | `tools/arclog/runs/20261007T140655Z-e34092f-o954ffd-pulled-20261012/` | The same soak pulled again into a new folder, after merging `main` into the branch, with `--until 2026-10-08T08:45:00Z`; 12 s. `PASS +36115.2s`, `SLOT (1800/1800)` on Node 2 (+36095.2 s) and Node 5 (+36115.2 s), `CLK to=WARM` at +155.5 s, 180 `SLOT` an hour as minimum, median and maximum on both nodes, schema `commit e34092f`: the same verdict and counts as the run of 2026-10-08. The folder holds every line of the 2026-10-08 folder (none missing) and the later ones, 19361 lines for Node 5 and 11456 for Node 2. The judge exits 4 (`INVALID`, three `WARN` lines) for the collector's present state, not for the run: it has been disconnected from both Pis since 2026-10-09 (`TimeoutError`) and `/nodes` gives `pi_ntp: unknown` for Node 2's Pi, while the last line of both nodes inside the run is at 08:44:51 and 08:44:52 UTC. Judging a finished run days later reads the live link, which says nothing about the run; the check was already so in the judge of #86 and is left for #114 "Judge of a finished run flags the collector's current link and Pi clock, not the run's". |
