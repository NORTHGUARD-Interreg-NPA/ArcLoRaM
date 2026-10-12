"""bench.collector: the pulled record of a run decides a scenario the way a capture does."""

import json
import re
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import pytest
from fake_collector import FakeCollector, soak

from arclog.schema import EVENTS, Event
from bench import collector
from bench.collector import (collector_causes, export_causes, judge, node_number, pi_clock_states, quiet_notes)
from bench.pull import NodeState, PullError, load_state, pull
from bench.scenario import from_dict

PARIS = ZoneInfo("Europe/Paris")
UTC = timezone.utc
SINCE = datetime(2026, 10, 7, 2, 28, 0, tzinfo=UTC)

# The first lines of nuna-node-03 after its reset on 2026-10-07, as the collector holds them
# (the Pi's local time, CEST): both cores boot as a C2 of build 474afcb, then it scans, takes a Sync
# packet and logs a slot every 20 s.
LOG = """2026-10-07 04:28:49.349199 | nuna-node-03 | 000000T000000.9997 4S A #00 BOOT build=474afcb
2026-10-07 04:28:49.399184 | nuna-node-03 | 000000T000000.9997 4X M #01 CORE_SYNC stage=wait_cm0
2026-10-07 04:28:49.449023 | nuna-node-03 | 000000T000000.9997 4X M #02 CORE_SYNC stage=cm0_up
2026-10-07 04:28:49.498963 | nuna-node-03 | 000000T000000.9997 4X M #03 CORE_SYNC stage=linked
2026-10-07 04:28:49.548797 | nuna-node-03 | 000000T000000.9997 4X M #04 CORE_SYNC stage=rtc_registered
2026-10-07 04:28:49.615240 | nuna-node-03 | 000101T000000.0034 0S A #00 BOOT cls=C2 id=2 uid=0026001a3232501420383543 fw=1.5.0 build=474afcb sync=DEV
2026-10-07 04:28:49.731776 | nuna-node-03 | 000101T000000.0302 0M L #01 MAC_INIT cls=C2 st=SCAN clk=COLD
2026-10-07 04:28:49.798269 | nuna-node-03 | 000101T000000.0346 0Y L #02 CALR req=9537 calp=1 calm=502 res=boot cal0=9537 trim=0
2026-10-07 04:28:49.881370 | nuna-node-03 | 000101T000000.0393 0T M #03 SCAN freq=868300000 why=boot
2026-10-07 04:28:49.947854 | nuna-node-03 | 000101T000000.0485 0S L #04 INIT_DONE phases=2
2026-10-07 04:30:53.531372 | nuna-node-03 | 000101T000204.1555 0P M #05 STOP2_WAKES n=64
2026-10-07 04:31:16.518459 | nuna-node-03 | 000101T000227.1376 0R M #06 RX_DONE sz=10 rssi=-4 snr=8 pre=146218 hdr=146806 rxd=147133 toa=991 st=146142
2026-10-07 04:31:16.618261 | nuna-node-03 | 000101T235822.0000 0Y L #07 RTC_SET old=147145 new=86301034 d=-246111 date=000101 shift=ok adv=147
2026-10-07 04:31:16.734661 | nuna-node-03 | 000101T235822.0000 0Y M #08 SYNC_RX ph=1 ce=0 ep=86300032 st=146142 exp=86300032 err=246111 erru=246110578 clk=COLD act=set
2026-10-07 04:31:16.850983 | nuna-node-03 | 000101T235822.0000 0Y L #09 CLK from=COLD to=ACQ why=rtc_set
2026-10-07 04:31:16.917500 | nuna-node-03 | 000101T235822.0000 0T M #0a BOOTSTRAP ph=1 ce=0 nom=86300032
2026-10-07 04:31:35.423266 | nuna-node-03 | 000101T235839.9335 0T H #0b SLOT ph=1 ty=SYNC ce=1 sl=0 pos=CELL dec=RX wake=0 nom=86320032
2026-10-07 04:31:35.506469 | nuna-node-03 | 000101T235839.9387 0T H #0c RX_WIN last=86321640 cap=86322632
2026-10-07 04:31:37.389852 | nuna-node-03 | 000101T235841.9028 0R H #0d RX_TIMEOUT pre=0
2026-10-07 04:31:55.423861 | nuna-node-03 | 000101T235859.9335 0T H #0e SLOT ph=1 ty=SYNC ce=2 sl=0 pos=CELL dec=RX wake=0 nom=86340032
2026-10-07 04:31:55.507063 | nuna-node-03 | 000101T235859.9387 0T H #0f RX_WIN last=86341640 cap=86342632
2026-10-07 04:31:57.390524 | nuna-node-03 | 000101T235901.9028 0R H #10 RX_TIMEOUT pre=0
2026-10-07 04:32:15.424464 | nuna-node-03 | 000101T235919.9335 0T H #11 SLOT ph=1 ty=SYNC ce=3 sl=0 pos=CELL dec=RX wake=0 nom=86360032
2026-10-07 04:32:15.507665 | nuna-node-03 | 000101T235919.9387 0T H #12 RX_WIN last=86361640 cap=86362632
2026-10-07 04:32:17.391533 | nuna-node-03 | 000101T235921.9033 0R H #13 RX_TIMEOUT pre=0
2026-10-07 04:32:29.615269 | nuna-node-03 | 000101T235934.1264 0P M #14 STOP2_WAKES n=128
2026-10-07 04:32:35.425072 | nuna-node-03 | 000101T235939.9335 0T H #15 SLOT ph=1 ty=SYNC ce=4 sl=0 pos=CELL dec=RX wake=0 nom=86380032
2026-10-07 04:32:35.508263 | nuna-node-03 | 000101T235939.9387 0T H #16 RX_WIN last=86381640 cap=86382632
2026-10-07 04:32:37.392262 | nuna-node-03 | 000101T235941.9033 0R H #17 RX_TIMEOUT pre=0
2026-10-07 04:32:55.424710 | nuna-node-03 | 000101T235959.9335 0T H #18 SLOT ph=1 ty=SYNC ce=5 sl=0 pos=CELL dec=RX wake=0 nom=32
2026-10-07 04:32:55.507927 | nuna-node-03 | 000101T235959.9377 0T H #19 RX_WIN last=1640 cap=2632
2026-10-07 04:32:57.390468 | nuna-node-03 | 000102T000001.9011 0R H #1a RX_TIMEOUT pre=0
2026-10-07 04:33:15.425573 | nuna-node-03 | 000102T000019.9335 0T H #1b SLOT ph=1 ty=SYNC ce=6 sl=0 pos=CELL dec=RX wake=0 nom=20032
2026-10-07 04:33:15.508803 | nuna-node-03 | 000102T000019.9382 0T H #1c RX_WIN last=21640 cap=22632"""

NODE3 = "nuna-node-03"
# The build 474afcb is from before #36: its RX_WIN has no g and win, and the checkout's schema requires them.
OLD_BUILD = dict(EVENTS, RX_WIN=Event("T", ("last", "cap"), ""))


def schema_text(events: dict) -> str:
    items = ",\n".join(f"{n!r}: Event({e.mod!r}, {e.keys!r}, '', {e.optional!r})" for n, e in events.items())
    return f"EVENTS = {{\n{items}\n}}\n"


def old_schema(commit: str) -> str:
    return schema_text(OLD_BUILD)


def rows_of(text: str) -> list[tuple[datetime, str]]:
    """(the Pi's time, device line) of each line of a log in the collector's first text format."""
    out = []
    for raw in text.splitlines():
        when, _, device = raw.split(" | ", 2)
        out.append((datetime.fromisoformat(when).replace(tzinfo=PARIS), device))
    return out


ROWS = rows_of(LOG)
FIRST_SLOT = next(i for i, ln in enumerate(LOG.splitlines()) if " SLOT " in ln)
SCENARIO = {"nodes": {"2": "C2"}, "timeout": "20m",
            "expect": [{"node": 2, "event": "SLOT", "count": 3, "within": "5m"}]}
STATUS = {"nuna-node-03": {"connected": True, "connected_since": "2026-10-07T02:16:42+00:00",
                           "last_pi_timestamp": None, "last_line_received": None, "last_error": None}}




@pytest.fixture
def serve():
    started = []

    def serve(rows_by_node: dict[str, list], status: dict | None = None, **kw) -> tuple[FakeCollector, str]:
        fake = FakeCollector(list(rows_by_node), **kw)
        started.append(fake)
        for name, rows in rows_by_node.items():
            fake.add(name, SINCE - timedelta(hours=2), "[collector] connected", source="collector")
            fake.add_all(name, rows)
            fake.status[name].update((status or {}).get(name, {}))
        return fake, fake.start()

    yield serve
    for fake in started:
        fake.stop()


def run_judge(tmp_path, serve, now: datetime, rows=ROWS, scenario=SCENARIO, status: dict | None = None,
              schema=old_schema, **kw) -> tuple[int, list[str]]:
    """Pull the fake collector's record of the run into a folder and judge the folder."""
    fake, base = serve({NODE3: rows}, status={NODE3: status or {}})
    folder = tmp_path / "record"
    pull(base, [NODE3], "474afcb", SINCE, folder, PARIS, schema_source=schema)
    said: list[str] = []
    code = judge(from_dict(scenario), "474afcb", {2: NODE3}, SINCE, folder, now=now, report=said.append, **kw)
    return code, said


def detail(tmp_path) -> str:
    return (tmp_path / "record" / "judge.log").read_text()


def verdict_line(said: list[str]) -> str:
    return said[0]



def test_a_node_name_gives_its_collector_number():
    assert node_number("nuna-node-03") == 3 and node_number("nuna-node-12") == 12
    with pytest.raises(ValueError):
        node_number("collector")


def test_a_run_whose_expectations_are_in_the_collector_passes(tmp_path, serve):
    code, said = run_judge(tmp_path, serve, SINCE + timedelta(minutes=16))

    assert code == 0, said
    assert said[0].startswith("PASS")
    # the stage lines are in the detail file, not in what is printed
    assert "SLOT (3/3)" in detail(tmp_path) and "SLOT (1/3)" not in detail(tmp_path)
    assert not any("SLOT (3/3)" in m for m in said)


def test_the_pull_asks_the_collector_for_one_node_from_five_minutes_before_the_start_without_the_heartbeat(
        tmp_path, serve):
    fake, base = serve({NODE3: ROWS})
    pull(base, [NODE3], "474afcb", SINCE, tmp_path / "record", PARIS, schema_source=old_schema)

    first = next(r for r in fake.requests if r.startswith("/logs"))
    assert "nodes=nuna-node-03" in first and "since=2026-10-07T02%3A23%3A00.000000Z" in first
    assert "exclude=pi" in first and "format=json" in first


def test_a_node_that_went_silent_fails_its_expectation_at_the_wall_clock(tmp_path, serve):
    before_the_first_slot = ROWS[:FIRST_SLOT]
    code, said = run_judge(tmp_path, serve, SINCE + timedelta(minutes=16), before_the_first_slot)

    assert code == 1
    assert said[0].startswith("FAIL") and "SLOT" in said[0] and "not within" in said[0]


def test_a_run_not_yet_due_has_no_verdict_and_says_what_it_waits_for(tmp_path, serve):
    before_the_first_slot = ROWS[:FIRST_SLOT]
    code, said = run_judge(tmp_path, serve, SINCE + timedelta(minutes=2), before_the_first_slot)

    assert code == 2
    assert said[0].startswith("NO VERDICT YET") and "waiting for nuna-node-03 SLOT (0/3)" in said[0]


def test_the_record_is_judged_with_the_schema_of_its_build_and_not_the_checkouts(tmp_path, serve):
    code, said = run_judge(tmp_path, serve, SINCE + timedelta(minutes=16), schema=old_schema)
    assert code == 0, said
    assert "schema commit 474afcb" in said[-1] and not any(m.startswith("WARN schema") for m in said)

    # the checkout's schema requires g and win on RX_WIN: this build does not print them
    code, said = run_judge(tmp_path / "again", serve, SINCE + timedelta(minutes=16), schema=lambda c: None)
    assert code == 1
    assert "RX_WIN: missing g,win" in said[0]
    assert any(m.startswith("WARN schema: the checkout's is used") for m in said)


NODE = "nuna-node-01"


def test_a_pi_clock_that_is_not_synchronised_or_lags_is_a_bench_fault():
    ok = {NODE: {"connected": True, "pi_ntp": "synced", "clock_lag_s": 0.07}}
    assert collector_causes([NODE], ok, SINCE, {}) == []
    [unsynced] = collector_causes([NODE], {NODE: {"connected": True, "pi_ntp": "unsynced"}}, SINCE, {})
    [lagging] = collector_causes([NODE], {NODE: {"connected": True, "clock_lag_s": 42.0}}, SINCE, {})

    assert (unsynced.kind, unsynced.fault, unsynced.node) == ("pi_clock", "bench", NODE)
    assert (lagging.kind, lagging.fault) == ("pi_clock", "bench")
    assert "+42.0 s" in lagging.text
    [six] = collector_causes([NODE], {NODE: {"connected": True, "clock_lag_s": 6.0}}, SINCE, {})
    assert (six.kind, six.node) == ("pi_clock", NODE) and "+6.0 s" in six.text
    assert collector_causes([NODE], {NODE: {"connected": True, "clock_lag_s": -4.9}}, SINCE, {}) == []


def test_the_clock_of_a_pi_the_run_does_not_use_is_not_its_cause():
    status = {NODE: {"connected": True}, "nuna-node-09": {"connected": True, "pi_ntp": "unsynced"}}
    assert collector_causes([NODE], status, SINCE, {}) == []


def test_a_pi_node_the_collector_does_not_know_or_cannot_reach_is_a_bench_fault():
    [unreachable] = collector_causes([NODE], {NODE: {"connected": False, "last_error": "TimeoutError"}}, SINCE, {})
    [unknown] = collector_causes(["nuna-node-09"], {NODE: {"connected": True}}, SINCE, {})

    assert (unreachable.kind, unreachable.fault, unreachable.node) == ("collector_node", "bench", NODE)
    assert "not connected" in unreachable.text and "TimeoutError" in unreachable.text
    assert (unknown.kind, unknown.fault, unknown.node) == ("collector_node", "bench", "nuna-node-09")
    assert "does not know" in unknown.text


def test_a_reconnect_after_the_start_invalidates_only_when_lines_were_lost():
    reconnected = {NODE: {"connected": True, "connected_since": "2026-10-07T03:00:00+00:00",
                          "recovered_from_replay": 1}}
    [gap] = collector_causes([NODE], reconnected, SINCE, {NODE: 13})

    assert (gap.kind, gap.fault, gap.node) == ("collector_gap", "bench", NODE)
    assert "13 lines lost" in gap.text and "2026-10-07T03:00:00+00:00" in gap.text
    assert collector_causes([NODE], reconnected, SINCE, {NODE: 0}) == []  # the Pi's buffer was enough
    assert collector_causes([NODE], reconnected, SINCE, {}) == []
    steady = {NODE: {"connected": True, "connected_since": "2026-10-07T02:16:42+00:00"}}
    assert collector_causes([NODE], steady, SINCE, {NODE: 13}) == []  # lost with no reconnect: the trace's own verdict


def test_a_node_quiet_for_over_ten_minutes_is_a_heads_up_and_not_a_cause():
    quiet = {NODE: {"connected": True, "silent_for_s": 3600}}

    assert collector_causes([NODE], quiet, SINCE, {}) == []
    assert "3600 s" in quiet_notes([NODE], quiet)[0]
    assert quiet_notes([NODE], {NODE: {"connected": True, "silent_for_s": 15}}) == []
    # Not connected: the silence is the collector's, and collector_causes says so.
    assert quiet_notes([NODE], {NODE: {"connected": False, "silent_for_s": 3600}}) == []




def status_with(**fields) -> dict:
    return {"nuna-node-03": {**STATUS["nuna-node-03"], **fields}}



def test_a_passing_run_on_a_pi_with_an_unsynchronised_clock_is_invalid(tmp_path, serve):
    code, said = run_judge(tmp_path, serve, SINCE + timedelta(minutes=16), status={"pi_ntp": "unsynced"})

    assert code == 4
    assert any(m.startswith("WARN ") and "not synchronised" in m for m in said)
    assert said[0].startswith("PASS")  # the verdict is still reached and said
    assert any(m.startswith("INVALID") for m in said)


def test_a_quiet_node_is_a_warning_when_the_scenario_does_not_watch_its_silence(tmp_path, serve):
    code, said = run_judge(tmp_path, serve, SINCE + timedelta(minutes=16), status={"silent_for_s": 3600})

    assert code == 0
    assert any(m.startswith("WARN ") and "no line from it" in m for m in said)


def test_a_node_silent_longer_than_max_silence_fails_a_run_that_met_its_expectations(tmp_path, serve):
    now = SINCE + timedelta(minutes=16)  # the log ends 10 min 44.5 s before

    assert run_judge(tmp_path / "off", serve, now)[0] == 0
    code, said = run_judge(tmp_path / "on", serve, now, scenario={**SCENARIO, "max_silence": "10m"})
    assert code == 1
    assert any(m.startswith("WARN ") and "no line for 644 s" in m for m in said)
    assert run_judge(tmp_path / "long", serve, now, scenario={**SCENARIO, "max_silence": "20m"})[0] == 0


def test_lines_lost_across_a_reconnect_invalidate_the_run_that_lost_them_alone_fail_it(tmp_path, serve):
    gapped = [r for r in ROWS if " #0c " not in r[1]]
    assert run_judge(tmp_path / "alone", serve, SINCE + timedelta(minutes=16), gapped)[0] == 1

    code, said = run_judge(tmp_path / "reconnected", serve, SINCE + timedelta(minutes=16), gapped,
                           status={"connected_since": "2026-10-07T03:00:00+00:00"})
    assert code == 4
    assert any(m.startswith("WARN ") and "1 lines lost" in m for m in said)


def test_a_firmware_event_invalidates_a_dataset_session_and_only_fails_an_acceptance_run(tmp_path, serve):
    gapped = [r for r in ROWS if " #0c " not in r[1]]
    now = SINCE + timedelta(minutes=16)

    code, said = run_judge(tmp_path / "acceptance", serve, now, gapped)
    assert code == 1
    assert any(m.startswith("WARN ") and "1 line(s) lost before core 0 #0d" in m for m in said)

    code, said = run_judge(tmp_path / "dataset", serve, now, gapped, scenario={**SCENARIO, "dataset": True})
    assert code == 4
    assert any(m.startswith("INVALID") for m in said)


def test_the_pi_clock_states_are_the_collector_s_view_of_the_run_s_pi_nodes(monkeypatch):
    status = {"nuna-node-03": {"connected": True, "pi_ntp": "synced", "clock_lag_s": 0.07, "silent_for_s": 3},
              "nuna-node-01": {"connected": True, "pi_ntp": "unsynced"}, "nuna-node-09": {"pi_ntp": "synced"}}
    monkeypatch.setattr(collector, "http_get", lambda url, timeout=120.0: json.dumps(status))

    assert pi_clock_states("http://collector", ["nuna-node-03", "nuna-node-01", "com8"]) == {
        "nuna-node-03": {"pi_ntp": "synced", "clock_lag_s": 0.07}, "nuna-node-01": {"pi_ntp": "unsynced"}}


def test_a_collector_that_cannot_be_asked_gives_no_pi_clock_states(monkeypatch):
    def refuse(url: str, timeout: float = 120.0) -> str:
        raise OSError("connection refused")

    monkeypatch.setattr(collector, "http_get", refuse)
    assert pi_clock_states("http://collector", ["nuna-node-03"]) == {}
    assert pi_clock_states(None, ["nuna-node-03"]) == {}


# --- a record that is not whole is not judged -------------------------------------------------------------------


def serve_two_runs(serve):
    """Run A at 08:43 and run B at 14:08 of the same build on one node: their first lines print the same text."""
    a = soak(datetime(2026, 10, 7, 8, 43, tzinfo=UTC), 0.2, "16ee4e5-o954ffd")
    b = soak(datetime(2026, 10, 7, 14, 8, tzinfo=UTC), 0.2, "16ee4e5-o954ffd")
    return serve({NODE3: a + b})


SOAK = {"nodes": {"2": "C2"}, "timeout": "30m", "expect": [{"node": 2, "event": "SLOT", "count": 30, "within": "20m"}]}


def judge_soak(folder, since: datetime, now: datetime, scenario=SOAK, build="16ee4e5-o954ffd", **kw):
    said: list[str] = []
    code = judge(from_dict(scenario), build, {2: NODE3}, since, folder, now=now, report=said.append, **kw)
    return code, said


def test_two_runs_of_the_same_build_in_one_folder_are_each_judged_with_their_first_lines(tmp_path, serve):
    fake, base = serve_two_runs(serve)
    folder = tmp_path / "record"
    start = datetime(2026, 10, 7, 8, 40, tzinfo=UTC)
    pull(base, [NODE3], "16ee4e5-o954ffd", start, folder, PARIS, schema_source=lambda c: None)

    for since in (start, datetime(2026, 10, 7, 14, 5, tzinfo=UTC)):
        code, said = judge_soak(folder, since, since + timedelta(hours=1), until=since + timedelta(minutes=30))
        assert code == 0, said
        assert "1 BOOT" in said[1], "each run sees its own BOOT and only that"
    # without `until` the later run is a reboot nobody planned
    code, said = judge_soak(folder, start, start + timedelta(hours=8), scenario={**SOAK, "dataset": True})
    assert code == 4 and any("unexpected reboot" in m for m in said)


def test_a_pull_that_hit_the_collector_s_retention_before_the_start_is_an_unfit_record_not_a_missing_boot(
        tmp_path, serve):
    fake, base = serve_two_runs(serve)
    fake.drop_oldest(NODE3, 60)  # the first run's lines and the pre-run history are gone
    folder = tmp_path / "record"
    start = datetime(2026, 10, 7, 8, 40, tzinfo=UTC)
    pull(base, [NODE3], "16ee4e5-o954ffd", start, folder, PARIS, schema_source=lambda c: None)

    code, said = judge_soak(folder, start, start + timedelta(minutes=30))

    assert code == 4
    assert said[0].startswith("INVALID the record is not whole")
    assert any("no longer holds the beginning" in m for m in said)
    assert not any("FAIL" in m or "BOOT" in m for m in said), "no verdict on the nodes"


def test_a_pull_cut_short_is_never_judged(tmp_path, serve):
    fake, base = serve({NODE3: ROWS}, cap=10)
    fake.fail_logs_after = 2
    folder = tmp_path / "record"
    with pytest.raises(OSError):
        pull(base, [NODE3], "474afcb", SINCE, folder, PARIS, chunk=10, schema_source=old_schema)

    said: list[str] = []
    code = judge(from_dict(SCENARIO), "474afcb", {2: NODE3}, SINCE, folder, now=SINCE + timedelta(minutes=16),
                 report=said.append)

    assert code == 4 and "the pull did not finish" in "\n".join(said)
    assert not any(m.startswith("FAIL") for m in said)


def test_a_pull_that_is_still_truncated_is_never_judged(tmp_path, serve):
    fake, base = serve({NODE3: ROWS})
    folder = tmp_path / "record"
    pull(base, [NODE3], "474afcb", SINCE, folder, PARIS, schema_source=old_schema)
    state = load_state(folder)
    assert export_causes([NODE3], state["nodes"]) == []
    state["nodes"][NODE3].problem = "the collector says there is more but sent no line: the pull cannot go on"

    [cause] = export_causes([NODE3], state["nodes"])

    assert (cause.kind, cause.fault, cause.node) == ("collector_export", "bench", NODE3)
    assert "cannot go on" in cause.text
    [unknown] = export_causes(["nuna-node-09"], state["nodes"])
    assert "pull it first" in unknown.text
    [unfinished] = export_causes([NODE3], {NODE3: NodeState(NODE3)})
    assert "did not finish" in unfinished.text


def test_a_gap_right_after_a_chunk_boundary_of_the_pull_is_the_export_s_and_one_inside_a_chunk_is_the_nodes(
        tmp_path, serve):
    rows = soak(SINCE, 0.3, "474afcb")
    holed = (rows[:19] + rows[20:])[:57]  # the 20th line is missing: three chunks of 19 meet right after the hole
    scenario = {"nodes": {"2": "C2"}, "timeout": "30m"}
    results = {}
    for chunk in (19, 200):
        fake, base = serve({NODE3: holed})
        folder = tmp_path / str(chunk)
        pull(base, [NODE3], "474afcb", SINCE, folder, PARIS, chunk=chunk, schema_source=lambda c: None)
        said: list[str] = []
        code = judge(from_dict(scenario), "474afcb", {2: NODE3}, SINCE, folder, now=SINCE + timedelta(minutes=30),
                     report=said.append)
        results[chunk] = (code, said)

    code, said = results[19]
    assert code == 4 and any("chunk boundary of the pull" in m for m in said), said
    code, said = results[200]
    assert code == 1 and any("line(s) lost" in m for m in said), said


def test_lines_more_than_the_replay_buffer_out_of_order_make_the_record_unfit(tmp_path, serve):
    rows = soak(SINCE, 3, "474afcb")
    fake, base = serve({NODE3: rows})
    folder = tmp_path / "record"
    pull(base, [NODE3], "474afcb", SINCE, folder, PARIS, schema_source=lambda c: None)
    # a line stored now with the Pi time of 100 minutes before the newest: the Pi buffers an hour
    fake.add(NODE3, rows[-1][0] - timedelta(minutes=100), "260101T000000.0000 0T H #0b SLOT ph=1 ty=SYNC ce=1 sl=0 "
                                                          "pos=CELL dec=RX wake=0 nom=1")
    pull(base, [NODE3], "474afcb", SINCE, folder, PARIS, schema_source=lambda c: None)

    code, said = judge_soak(folder, SINCE, SINCE + timedelta(hours=4),
                            scenario={"nodes": {"2": "C2"}, "timeout": "4h"}, build="474afcb")

    assert code == 4
    assert any("out of order" in m for m in said)


def test_a_folder_that_no_pull_made_or_that_was_pulled_for_another_build_is_refused(tmp_path, serve):
    fake, base = serve({NODE3: ROWS})
    empty = tmp_path / "empty"
    empty.mkdir()

    with pytest.raises(PullError, match="not a pulled record"):
        judge(from_dict(SCENARIO), "474afcb", {2: NODE3}, SINCE, empty, now=SINCE)
    pull(base, [NODE3], "474afcb", SINCE, tmp_path / "record", PARIS, schema_source=old_schema)
    with pytest.raises(PullError, match="pulled for build 474afcb, not abc1234"):
        judge(from_dict(SCENARIO), "abc1234", {2: NODE3}, SINCE, tmp_path / "record", now=SINCE)


# --- what is printed ------------------------------------------------------------------------------------------


def test_the_summary_is_a_verdict_a_line_per_node_and_the_record_and_the_detail_goes_to_a_file(tmp_path, serve):
    code, said = run_judge(tmp_path, serve, SINCE + timedelta(minutes=16))

    assert code == 0
    assert len(said) == 3, said
    assert said[0].startswith("PASS")
    assert said[1].startswith("nuna-node-03 (Node 2): 34 lines, last 02:33:15Z") and "1 BOOT, last 02:28:49 C2 474afcb" in said[1]
    assert said[2].startswith("record: 1 node(s), 34 lines in the folder, schema commit 474afcb, detail in ")
    log = detail(tmp_path)
    assert "ok   nuna-node-03 armed" in log and "ok   nuna-node-03 CORE_SYNC stage=linked" in log
    assert log.rstrip().endswith("exit 0")


def test_verbose_prints_the_detail_too(tmp_path, serve):
    code, said = run_judge(tmp_path, serve, SINCE + timedelta(minutes=16), verbose=True)

    assert code == 0
    assert any(m.startswith("ok   nuna-node-03 armed") for m in said)
    assert said[-1] == "exit 0"


def test_the_causes_shown_are_the_first_five_and_the_rest_are_counted(tmp_path, serve):
    rows = soak(SINCE, 0.3, "474afcb")
    holed = [r for i, r in enumerate(rows) if i % 7 != 3]  # lines lost all along
    fake, base = serve({NODE3: holed})
    folder = tmp_path / "record"
    pull(base, [NODE3], "474afcb", SINCE, folder, PARIS, schema_source=lambda c: None)

    code, said = judge_soak(folder, SINCE, SINCE + timedelta(minutes=30), scenario={"nodes": {"2": "C2"}, "timeout": "30m",
                            "dataset": True}, build="474afcb")

    assert code == 4
    assert len([m for m in said if m.startswith("WARN ")]) == 6
    assert re.search(r"WARN \+\d+ more cause\(s\) in .*judge.log", " ".join(said))
    assert len(said) <= 12
    assert (folder / "judge.log").read_text().count("WARN ") > 6


def test_the_command_line_pulls_then_judges_and_judges_a_folder_again_without_the_collector(tmp_path, serve, capsys,
                                                                                         monkeypatch):
    monkeypatch.delenv("BENCH_COLLECTOR_URL", raising=False)
    from bench import pull as pulling
    monkeypatch.setattr(pulling, "schema_from_git", old_schema)
    monkeypatch.setattr(collector, "schema_from_git", old_schema)
    scenario = tmp_path / "x.toml"
    scenario.write_text('timeout = "20m"\n[nodes]\n2 = "C2"\n[[expect]]\nnode = 2\nevent = "SLOT"\ncount = 3\nwithin = "5m"\n')
    fake, base = serve({NODE3: ROWS})
    common = ["--build", "474afcb", "--since", "2026-10-07T02:28:00Z", "--node", "2=" + NODE3]
    pulled = ["--dir", str(tmp_path / "cli")]

    # the clock is real, so the run is long past its timeout: the verdict is still the record's
    assert collector.main([str(scenario), "--collector", base, *common, *pulled]) == 0
    assert capsys.readouterr().out.startswith("PASS")
    requests = len(fake.requests)
    assert collector.main([str(scenario), *common, *pulled, "--no-pull"]) == 0
    assert len(fake.requests) == requests, "--no-pull does not ask the collector"
    assert collector.main([str(scenario), *common, "--dir", str(tmp_path / "elsewhere"), "--no-pull"]) == 3
    assert "not a pulled record" in capsys.readouterr().err
    assert collector.main([str(scenario), *common, "--dir", str(tmp_path / "nowhere")]) == 3
    assert "no collector" in capsys.readouterr().err
