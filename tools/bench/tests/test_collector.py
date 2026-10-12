"""bench.collector: the log collector's lines decide a scenario the way a capture does."""

import json
import re
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import pytest

from bench import collector
from bench.collector import (collector_causes, convert_line, judge, node_number, pi_clock_states, quiet_notes,
                             write_capture)
from bench.scenario import from_dict

PARIS = ZoneInfo("Europe/Paris")
UTC = timezone.utc
SINCE = datetime(2026, 10, 7, 2, 28, 0, tzinfo=UTC)

# The first lines of nuna-node-03 after its reset on 2026-10-07, as the collector returns them
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
2026-10-07 04:31:35.506469 | nuna-node-03 | 000101T235839.9387 0T H #0c RX_WIN last=86321640 cap=86322632 g=500 win=1701
2026-10-07 04:31:37.389852 | nuna-node-03 | 000101T235841.9028 0R H #0d RX_TIMEOUT pre=0
2026-10-07 04:31:55.423861 | nuna-node-03 | 000101T235859.9335 0T H #0e SLOT ph=1 ty=SYNC ce=2 sl=0 pos=CELL dec=RX wake=0 nom=86340032
2026-10-07 04:31:55.507063 | nuna-node-03 | 000101T235859.9387 0T H #0f RX_WIN last=86341640 cap=86342632 g=500 win=1701
2026-10-07 04:31:57.390524 | nuna-node-03 | 000101T235901.9028 0R H #10 RX_TIMEOUT pre=0
2026-10-07 04:32:15.424464 | nuna-node-03 | 000101T235919.9335 0T H #11 SLOT ph=1 ty=SYNC ce=3 sl=0 pos=CELL dec=RX wake=0 nom=86360032
2026-10-07 04:32:15.507665 | nuna-node-03 | 000101T235919.9387 0T H #12 RX_WIN last=86361640 cap=86362632 g=500 win=1701
2026-10-07 04:32:17.391533 | nuna-node-03 | 000101T235921.9033 0R H #13 RX_TIMEOUT pre=0
2026-10-07 04:32:29.615269 | nuna-node-03 | 000101T235934.1264 0P M #14 STOP2_WAKES n=128
2026-10-07 04:32:35.425072 | nuna-node-03 | 000101T235939.9335 0T H #15 SLOT ph=1 ty=SYNC ce=4 sl=0 pos=CELL dec=RX wake=0 nom=86380032
2026-10-07 04:32:35.508263 | nuna-node-03 | 000101T235939.9387 0T H #16 RX_WIN last=86381640 cap=86382632 g=500 win=1701
2026-10-07 04:32:37.392262 | nuna-node-03 | 000101T235941.9033 0R H #17 RX_TIMEOUT pre=0
2026-10-07 04:32:55.424710 | nuna-node-03 | 000101T235959.9335 0T H #18 SLOT ph=1 ty=SYNC ce=5 sl=0 pos=CELL dec=RX wake=0 nom=32
2026-10-07 04:32:55.507927 | nuna-node-03 | 000101T235959.9377 0T H #19 RX_WIN last=1640 cap=2632 g=500 win=1702
2026-10-07 04:32:57.390468 | nuna-node-03 | 000102T000001.9011 0R H #1a RX_TIMEOUT pre=0
2026-10-07 04:33:15.425573 | nuna-node-03 | 000102T000019.9335 0T H #1b SLOT ph=1 ty=SYNC ce=6 sl=0 pos=CELL dec=RX wake=0 nom=20032
2026-10-07 04:33:15.508803 | nuna-node-03 | 000102T000019.9382 0T H #1c RX_WIN last=21640 cap=22632 g=500 win=1702"""
# The same lines as the collector writes them now: ISO 8601 with the offset.
LOG_ISO = re.sub(r"^(\d{4}-\d\d-\d\d) (\d\d:\d\d:\d\d\.\d+) ", r"\1T\2+02:00 ", LOG, flags=re.M)
FIRST_SLOT = next(i for i, ln in enumerate(LOG.splitlines()) if " SLOT " in ln)
SCENARIO = {"nodes": {"2": "C2"}, "timeout": "20m",
            "expect": [{"node": 2, "event": "SLOT", "count": 3, "within": "5m"}]}
STATUS = {"nuna-node-03": {"connected": True, "connected_since": "2026-10-07T02:16:42+00:00",
                           "last_pi_timestamp": None, "last_line_received": None, "last_error": None}}


def collector_serving(monkeypatch, log: str, status: dict = STATUS) -> list[str]:
    asked: list[str] = []

    def http_get(url: str, timeout: float = 120.0) -> str:
        asked.append(url)
        return json.dumps(status) if url.endswith("/nodes") else log

    monkeypatch.setattr(collector, "http_get", http_get)
    return asked


def run_judge(tmp_path, now: datetime, log: str = LOG, scenario: dict = SCENARIO) -> tuple[int, list[str]]:
    said: list[str] = []
    code = judge(from_dict(scenario), "474afcb", {2: "nuna-node-03"}, SINCE, "http://collector",
                 tmp_path, PARIS, now=now, report=said.append)
    return code, said


def test_a_collector_line_is_in_utc_with_its_node_and_device_line():
    node, t, device = convert_line(LOG.splitlines()[5], PARIS)
    assert node == "nuna-node-03"
    assert t == datetime(2026, 10, 7, 2, 28, 49, 615240, tzinfo=UTC)
    assert device.startswith("000101T000000.0034 0S A #00 BOOT cls=C2 id=2")


def test_a_line_with_its_offset_is_converted_without_the_pi_zone():
    node, t, device = convert_line(LOG_ISO.splitlines()[5], UTC)
    assert node == "nuna-node-03"
    assert t == datetime(2026, 10, 7, 2, 28, 49, 615240, tzinfo=UTC)
    assert device.startswith("000101T000000.0034 0S A #00 BOOT cls=C2 id=2")


def test_the_pi_s_own_lines_are_not_trace_lines():
    pi = "2026-10-07T16:04:04.856452+02:00 | nuna-node-03 | [pi] uart_idle=6s ntp=synced"
    assert convert_line(pi, PARIS) is None


@pytest.mark.parametrize("text", ["", "# collector: connected", "nuna-node-03 | no time"])
def test_a_line_of_another_shape_is_skipped(text):
    assert convert_line(text, PARIS) is None


def test_capture_files_are_split_at_utc_midnight(tmp_path):
    text = ("2026-10-08 01:59:59.500000 | nuna-node-03 | 000102T000000.0000 0P M #00 STOP2_WAKES n=1\n"
            "2026-10-08 02:00:01.500000 | nuna-node-03 | 000102T000002.0000 0P M #01 STOP2_WAKES n=2\n")
    assert write_capture(text, tmp_path, PARIS) == {"nuna-node-03": 2}
    assert sorted(p.name for p in tmp_path.iterdir()) == ["nuna-node-03-20261007.log", "nuna-node-03-20261008.log"]
    assert (tmp_path / "nuna-node-03-20261007.log").read_text().startswith("2026-10-07T23:59:59.500000Z\t")


def test_node_numbers_come_from_the_name():
    assert node_number("nuna-node-03") == 3 and node_number("nuna-node-12") == 12
    with pytest.raises(ValueError):
        node_number("collector")


@pytest.mark.parametrize("log", [LOG, LOG_ISO], ids=["first format", "iso with offset"])
def test_a_run_whose_expectations_are_in_the_collector_passes(monkeypatch, tmp_path, log):
    asked = collector_serving(monkeypatch, log)
    code, said = run_judge(tmp_path, SINCE + timedelta(minutes=16), log)
    assert code == 0, said
    assert asked[0] == "http://collector/logs?nodes=3&since=2026-10-07T02:23:00Z"
    assert any("SLOT (3/3)" in m for m in said) and not any("SLOT (1/3)" in m for m in said)


def test_a_node_that_went_silent_fails_its_expectation_at_the_wall_clock(monkeypatch, tmp_path):
    before_the_first_slot = "\n".join(LOG.splitlines()[:FIRST_SLOT])
    collector_serving(monkeypatch, before_the_first_slot)
    code, said = run_judge(tmp_path, SINCE + timedelta(minutes=16), before_the_first_slot)
    assert code == 1
    assert any("SLOT" in m and "not within" in m for m in said)


def test_a_run_not_yet_due_has_no_verdict(monkeypatch, tmp_path):
    before_the_first_slot = "\n".join(LOG.splitlines()[:FIRST_SLOT])
    collector_serving(monkeypatch, before_the_first_slot)
    code, said = run_judge(tmp_path, SINCE + timedelta(minutes=2), before_the_first_slot)
    assert code == 2
    assert any(m.startswith("NO VERDICT YET") for m in said)


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


def test_a_passing_run_on_a_pi_with_an_unsynchronised_clock_is_invalid(monkeypatch, tmp_path):
    collector_serving(monkeypatch, LOG, status_with(pi_ntp="unsynced"))
    code, said = run_judge(tmp_path, SINCE + timedelta(minutes=16))

    assert code == 4
    assert any(m.startswith("WARN ") and "not synchronised" in m for m in said)
    assert any("SLOT (3/3)" in m for m in said)  # the verdict is still reached and said
    assert any(m.startswith("INVALID") for m in said)


def test_a_quiet_node_is_a_warning_when_the_scenario_does_not_watch_its_silence(monkeypatch, tmp_path):
    collector_serving(monkeypatch, LOG, status_with(silent_for_s=3600))
    code, said = run_judge(tmp_path, SINCE + timedelta(minutes=16))

    assert code == 0
    assert any(m.startswith("WARN ") and "no line from it" in m for m in said)


def test_a_node_silent_longer_than_max_silence_fails_a_run_that_met_its_expectations(monkeypatch, tmp_path):
    collector_serving(monkeypatch, LOG)
    now = SINCE + timedelta(minutes=16)  # the log ends 10 min 44.5 s before

    assert run_judge(tmp_path / "off", now)[0] == 0
    code, said = run_judge(tmp_path / "on", now, scenario={**SCENARIO, "max_silence": "10m"})
    assert code == 1
    assert any(m.startswith("WARN ") and "no line for 644 s" in m for m in said)
    assert run_judge(tmp_path / "long", now, scenario={**SCENARIO, "max_silence": "20m"})[0] == 0


def test_lines_lost_across_a_reconnect_invalidate_the_run_that_lost_them_alone_fail_it(monkeypatch, tmp_path):
    gapped = "\n".join(ln for ln in LOG.splitlines() if " #0c " not in ln)
    collector_serving(monkeypatch, gapped)
    assert run_judge(tmp_path / "alone", SINCE + timedelta(minutes=16), gapped)[0] == 1

    collector_serving(monkeypatch, gapped, status_with(connected_since="2026-10-07T03:00:00+00:00"))
    code, said = run_judge(tmp_path / "reconnected", SINCE + timedelta(minutes=16), gapped)
    assert code == 4
    assert any(m.startswith("WARN ") and "1 lines lost" in m for m in said)


def test_a_firmware_event_invalidates_a_dataset_session_and_only_fails_an_acceptance_run(monkeypatch, tmp_path):
    gapped = "\n".join(ln for ln in LOG.splitlines() if " #0c " not in ln)
    collector_serving(monkeypatch, gapped)
    now = SINCE + timedelta(minutes=16)

    code, said = run_judge(tmp_path / "acceptance", now, gapped)
    assert code == 1
    assert any(m.startswith("WARN ") and "1 line(s) lost before core 0 #0d" in m for m in said)

    code, said = run_judge(tmp_path / "dataset", now, gapped, scenario={**SCENARIO, "dataset": True})
    assert code == 4
    assert any(m.startswith("INVALID") for m in said)


def test_the_pi_clock_states_are_the_collector_s_view_of_the_run_s_pi_nodes(monkeypatch):
    status = {"nuna-node-03": {"connected": True, "pi_ntp": "synced", "clock_lag_s": 0.07, "silent_for_s": 3},
              "nuna-node-01": {"connected": True, "pi_ntp": "unsynced"}, "nuna-node-09": {"pi_ntp": "synced"}}
    collector_serving(monkeypatch, "", status)

    assert pi_clock_states("http://collector", ["nuna-node-03", "nuna-node-01", "com8"]) == {
        "nuna-node-03": {"pi_ntp": "synced", "clock_lag_s": 0.07}, "nuna-node-01": {"pi_ntp": "unsynced"}}


def test_a_collector_that_cannot_be_asked_gives_no_pi_clock_states(monkeypatch):
    def refuse(url: str, timeout: float = 120.0) -> str:
        raise OSError("connection refused")

    monkeypatch.setattr(collector, "http_get", refuse)
    assert pi_clock_states("http://collector", ["nuna-node-03"]) == {}
    assert pi_clock_states(None, ["nuna-node-03"]) == {}
