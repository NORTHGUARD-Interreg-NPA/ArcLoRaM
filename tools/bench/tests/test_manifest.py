"""manifest.toml: what a session was, to be read a year later without the capture."""

import sys
from datetime import datetime, timedelta, timezone

from bench.boards import Board
from bench.manifest import (Host, append_note, dumps, find_running, finish_manifest, parse_time_service,
                            read_host, read_notes, start_manifest)
from bench.scenario import from_dict

if sys.version_info >= (3, 11):
    import tomllib
else:  # pragma: no cover
    import tomli as tomllib


def test_a_manifest_reads_back_as_it_was_written():
    d = {
        "run": {"build_id": "16ee4e5-o954ffd", "dirty": False, "exit_code": 4, "mono": 12345.678,
                "overrides": {"SYNC_BOOT_BURST": "5u"}},
        "board": [{"node_id": 2, "boot": ["000101T000000.0034 0S A #00 BOOT cls=C2", 'a "quoted" \\ line']},
                  {"node_id": 5, "boot": []}],
        "note": [{"time": "2026-10-07T14:00:00.000000Z", "text": "moved the board à la fenêtre"}],
    }

    assert tomllib.loads(dumps(d)) == d


W32TM = """Leap Indicator: 0(no warning)
Stratum: 3 (secondary reference - syncd by (S)NTP)
Precision: -23 (119.209ns per tick)
Root Delay: 0.0156250s
Root Dispersion: 7.7826843s
ReferenceId: 0x4D3E4C0A (source IP:  203.0.113.10)
Last Successful Sync Time: 10/7/2026 2:03:04 PM
Source: 0.pool.ntp.org
Poll Interval: 12 (4096s)
"""


def test_the_time_service_state_is_its_source_last_sync_stratum_and_poll():
    assert parse_time_service(W32TM) == {"source": "0.pool.ntp.org", "last_sync": "10/7/2026 2:03:04 PM",
                                         "stratum": 3, "poll": "4096s"}


def test_a_time_service_output_that_is_not_understood_is_kept_whole_rather_than_dropped():
    state = parse_time_service("L'indicateur de saut : 0\nSource : ntp.example.org\n")
    assert state["error"] == "not understood" and "ntp.example.org" in state["raw"]


START = datetime(2026, 10, 7, 14, 6, 55, tzinfo=timezone.utc)
HOST = Host(START, 1234.5, {"source": "0.pool.ntp.org", "stratum": 3})
UID_C3 = (0x0014008F, 0x32325014, 0x20383543)
UID_C2 = (0x0026001A, 0x32325014, 0x20383543)


def manifest_of(scenario: dict, boards: dict[int, Board], build_id: str = "16ee4e5-o954ffd", others=()) -> dict:
    return start_manifest(command="bench run scenarios/x.toml", worktree="86-run-manifest", bench_commit="6bd2cab",
                          build_id=build_id, scenario=from_dict(scenario), boards=boards, others=list(others),
                          started=START, flashed={n: START for n in boards}, host=HOST)


def test_the_manifest_of_a_local_c3_names_the_run_the_build_the_board_and_the_host():
    scenario = {"nodes": {"1": "C3"}, "overrides": {"SYNC_BOOT_BURST": "5u"}, "dataset": True, "max_silence": "5m"}
    m = manifest_of(scenario, {1: Board("066DFF", "COM8", UID_C3, "swd", 1)})

    assert m["run"] == {
        "command": "bench run scenarios/x.toml", "worktree": "86-run-manifest", "bench_commit": "6bd2cab",
        "build_id": "16ee4e5-o954ffd", "firmware_commit": "16ee4e5", "firmware_dirty": False,
        "overrides": {"SYNC_BOOT_BURST": "5u"}, "scenario": "scenario.toml", "dataset": True, "max_silence": "5m",
        "timeout": "10m", "start": "2026-10-07T14:06:55.000000Z"}
    assert m["board"] == [{
        "node_id": 1, "role": "flashed", "class": "C3", "kind": "stlink", "uid": "0014008f3232501420383543",
        "probe": "066DFF", "port": "COM8", "log": "com8", "flashed_at": "2026-10-07T14:06:55.000000Z"}]
    assert m["controller"] == {"start_utc": "2026-10-07T14:06:55.000000Z", "start_mono": 1234.5,
                               "time_service_start": {"source": "0.pool.ntp.org", "stratum": 3}}


def test_the_manifest_of_a_pi_node_c2_names_it_and_never_its_address():
    pi = Board("nuna-node-03", "tcp://pi.example.net:4000/nuna-node-03", UID_C2, "swd", 2, remote=True)
    m = manifest_of({"nodes": {"2": "C2"}}, {2: pi})

    [board] = m["board"]
    assert (board["kind"], board["probe"], board["log"], board["class"]) == ("pinode", "nuna-node-03",
                                                                          "nuna-node-03", "C2")
    assert "port" not in board
    assert "pi.example.net" not in dumps(m) and "tcp://" not in dumps(m)


def test_a_watched_board_and_a_dirty_build_and_a_board_outside_the_scenario_are_said():
    boards = {1: Board("SN1", "COM8", UID_C3, "swd", 1), 2: Board("SN2", "COM9", UID_C2, "swd", 2)}
    other = Board("SN3", "COM10", None, "", None, build="474afcb")
    m = start_manifest(command="c", worktree="main", bench_commit="x", build_id="a1b2c3d-dab12cd-o9c0d1",
                       scenario=from_dict({"nodes": {"1": "C3", "2": "watch"}}), boards=boards, others=[other],
                       started=START, flashed={1: START}, host=HOST)

    assert (m["run"]["firmware_commit"], m["run"]["firmware_dirty"]) == ("a1b2c3d", True)
    watched = next(b for b in m["board"] if b["node_id"] == 2)
    assert watched["role"] == "watched" and "class" not in watched and "flashed_at" not in watched
    bystander = next(b for b in m["board"] if b["role"] == "not in the scenario")
    assert (bystander["probe"], bystander["build"]) == ("SN3", "474afcb") and "node_id" not in bystander


END = START + timedelta(minutes=12)


def test_the_finished_manifest_has_the_end_the_boot_lines_the_host_clocks_the_pi_clocks_and_the_notes(tmp_path):
    pi = Board("nuna-node-03", "tcp://pi.example.net:4000/nuna-node-03", UID_C2, "swd", 2, remote=True)
    m = manifest_of({"nodes": {"2": "C2"}}, {2: pi})
    append_note(tmp_path, "moved the board to the window", START + timedelta(minutes=3))
    boot = "000101T000000.0034 0S A #00 BOOT cls=C2 id=2 build=16ee4e5-o954ffd"

    finish_manifest(m, ended=END, verdict="PASS every expectation met", exit_code=0,
                    host=Host(END, 1954.5, {"source": "0.pool.ntp.org", "stratum": 3}),
                    boots={"nuna-node-03": [boot]}, notes=read_notes(tmp_path),
                    pi_clocks={"nuna-node-03": ({"pi_ntp": "synced", "clock_lag_s": 0.07},
                                                {"pi_ntp": "synced", "clock_lag_s": 0.12})})

    assert m["run"]["end"] == "2026-10-07T14:18:55.000000Z"
    assert (m["run"]["verdict"], m["run"]["exit_code"]) == ("PASS every expectation met", 0)
    assert m["board"][0]["boot"] == [boot]
    assert (m["controller"]["end_utc"], m["controller"]["end_mono"]) == ("2026-10-07T14:18:55.000000Z", 1954.5)
    assert m["pi_clock"] == [{"name": "nuna-node-03", "start_ntp": "synced", "start_lag_s": 0.07,
                              "end_ntp": "synced", "end_lag_s": 0.12}]
    assert m["note"] == [{"time": "2026-10-07T14:09:55.000000Z", "text": "moved the board to the window"}]
    assert tomllib.loads(dumps(m)) == m


def test_notes_are_kept_in_the_order_they_were_made_with_their_time(tmp_path):
    append_note(tmp_path, "ambient 21 C", START)
    append_note(tmp_path, "replugged COM8", START + timedelta(minutes=1))

    assert read_notes(tmp_path) == [{"time": "2026-10-07T14:06:55.000000Z", "text": "ambient 21 C"},
                                    {"time": "2026-10-07T14:07:55.000000Z", "text": "replugged COM8"}]
    assert read_notes(tmp_path / "nowhere") == []


def test_the_running_session_is_the_newest_unfinished_manifest_within_its_deadline(tmp_path):
    def session(name: str, start: datetime, finished: bool) -> None:
        m = manifest_of({"nodes": {"1": "C3"}}, {1: Board("SN1", "COM8", UID_C3, "swd", 1)})
        m["run"]["start"] = start.strftime("%Y-%m-%dT%H:%M:%S.000000Z")
        if finished:
            m["run"]["end"] = m["run"]["start"]
        (tmp_path / name).mkdir()
        (tmp_path / name / "manifest.toml").write_text(dumps(m), encoding="utf-8")

    session("old-finished", START - timedelta(hours=5), finished=True)
    session("stale-crashed", START - timedelta(hours=4), finished=False)
    session("running", START, finished=False)
    (tmp_path / "bench").mkdir()  # the capture folder has no manifest

    assert find_running(tmp_path, START + timedelta(minutes=5)) == tmp_path / "running"
    assert find_running(tmp_path, START + timedelta(hours=2)) is None  # past its 10 min timeout and the slack


def test_the_host_reading_pairs_utc_with_the_monotonic_clock_and_the_time_service():
    host = read_host(now=lambda: START, mono=lambda: 1234.5, query=lambda: W32TM)
    assert (host.utc, host.mono, host.time_service["source"]) == (START, 1234.5, "0.pool.ntp.org")


def test_a_time_service_that_cannot_be_asked_is_recorded_as_such_not_as_a_failure_of_the_run():
    def broken() -> str:
        raise OSError("w32tm.exe not found")

    host = read_host(now=lambda: START, mono=lambda: 1.0, query=broken)
    assert host.time_service == {"error": "w32tm.exe not found"}


def test_an_empty_table_is_a_header_alone_and_the_file_has_no_stray_blank_lines():
    d = {"run": {"overrides": {}, "n": 1}, "controller": {"start": {}, "end": {}}, "board": [{"x": 1}, {"x": 2}]}
    text = dumps(d)

    assert tomllib.loads(text) == d
    assert "\n\n\n" not in text and not text.startswith("\n")
