"""bench.pull: the collector's record copied into a folder, every line once, in order, however it is paged."""

import json
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import pytest
from fake_collector import PARIS, FakeCollector, soak

from bench import pull as pulling
from bench.pull import PullError, load_state, main, pull
from arclog.stream import node_lines

UTC = timezone.utc
SINCE = datetime(2026, 10, 7, 14, 6, 55, tzinfo=UTC)
BUILD = "e34092f-o954ffd"
N1, N3 = "nuna-node-01", "nuna-node-03"


def no_git(commit: str):
    return None


@pytest.fixture
def make(tmp_path):
    started = []

    def make(cap: int = 10000, nodes=(N1, N3), **kw) -> tuple[FakeCollector, str]:
        fake = FakeCollector(list(nodes), cap=cap, **kw)
        started.append(fake)
        return fake, fake.start()

    yield make
    for fake in started:
        fake.stop()


def history(fake: FakeCollector, name: str, start: datetime = SINCE) -> None:
    """What the collector holds of a node before a run starts: it has been recording for hours."""
    fake.add(name, start - timedelta(hours=2), "[collector] connected", source="collector")


def feed(fake: FakeCollector, hours: float = 0.1, start: datetime = SINCE, names=(N1, N3)) -> dict[str, list]:
    """A soak for each node, stored interleaved by time as the Pis would deliver it."""
    for name in names:
        history(fake, name, start)
    traces = {name: soak(start, hours, "e34092f-o954ffd", node_id=i + 2) for i, name in enumerate(names)}
    rows = sorted(((t, name, text) for name, rows in traces.items() for t, text in rows), key=lambda r: r[0])
    for t, name, text in rows:
        fake.add(name, t, text)
    return traces


def do_pull(base: str, tmp_path, names=(N1, N3), **kw) -> dict:
    return pull(base, list(names), BUILD, kw.pop("since", SINCE), tmp_path / "record", PARIS, schema_source=no_git, **kw)


def device_lines(directory, node: str, since: datetime = SINCE - timedelta(minutes=5)) -> list[str]:
    return [ln.raw for ln in node_lines(directory, node, since)]


def expected(traces: dict[str, list], node: str) -> list[str]:
    return [text for _, text in traces[node]]


def folder_bytes(directory) -> dict[str, bytes]:
    return {p.name: p.read_bytes() for p in sorted(directory.iterdir()) if p.is_file()}


@pytest.mark.parametrize("cap", [1, 7, 100, 100000])
def test_every_line_is_pulled_exactly_once_and_in_order_whatever_the_cap(make, tmp_path, cap):
    fake, base = make(cap=cap)
    traces = feed(fake)

    state = do_pull(base, tmp_path, chunk=50)

    for node in (N1, N3):
        assert device_lines(tmp_path / "record", node) == expected(traces, node)
        assert state["nodes"][node].lines == len(traces[node])
        assert state["nodes"][node].complete_to and not state["nodes"][node].problem
    if cap < 100:
        assert any("until=" in r for r in fake.requests), "the first pull goes back in time while it is truncated"


def test_the_lines_before_the_start_of_the_record_are_not_pulled(make, tmp_path):
    fake, base = make()
    fake.add(N1, SINCE - timedelta(hours=2), "260101T000000.0000 0S A #00 BOOT build=old")
    traces = feed(fake, names=(N1,))

    do_pull(base, tmp_path, names=(N1,))

    assert device_lines(tmp_path / "record", N1, SINCE - timedelta(days=1)) == expected(traces, N1)


def test_a_second_pull_adds_nothing(make, tmp_path):
    fake, base = make(cap=40)
    feed(fake)
    do_pull(base, tmp_path, chunk=40)
    first = folder_bytes(tmp_path / "record")
    cursors = {n: s.cursor for n, s in load_state(tmp_path / "record")["nodes"].items()}

    do_pull(base, tmp_path, chunk=40)

    assert folder_bytes(tmp_path / "record").keys() == first.keys()
    assert {k: v for k, v in folder_bytes(tmp_path / "record").items() if k.endswith(".log")} == \
        {k: v for k, v in first.items() if k.endswith(".log")}
    assert {n: s.cursor for n, s in load_state(tmp_path / "record")["nodes"].items()} == cursors


def test_a_later_pull_asks_only_for_what_came_after_and_appends_it(make, tmp_path):
    fake, base = make()
    traces = feed(fake, hours=0.1)
    do_pull(base, tmp_path)
    more = soak(SINCE + timedelta(hours=0.1), 0.05, "e34092f-o954ffd")
    for t, text in more[3:]:  # the continuation of the same trace is not needed: any new lines do
        fake.add(N1, t, text)
    fake.requests.clear()

    state = do_pull(base, tmp_path)

    assert state["nodes"][N1].lines == len(traces[N1]) + len(more) - 3
    assert state["nodes"][N3].lines == len(traces[N3])
    logs = [r for r in fake.requests if r.startswith("/logs")]
    assert logs and all("after=" in r and "since=" not in r for r in logs)


def test_the_pi_s_heartbeat_is_left_out_and_the_other_sources_go_to_their_own_file(make, tmp_path):
    fake, base = make()
    traces = feed(fake, names=(N1,))
    fake.add(N1, SINCE + timedelta(minutes=1), "[pi] uart_idle=6s ntp=synced", source="pi")
    fake.add(N1, SINCE + timedelta(minutes=2), "[collector] disconnected (connection closed by node)", source="collector")
    fake.add(N1, SINCE + timedelta(minutes=3), "[openocd] Error: clearing lockup after double fault", source="openocd")

    state = do_pull(base, tmp_path, names=(N1,))

    folder = tmp_path / "record"
    assert device_lines(folder, N1) == expected(traces, N1)
    assert all("exclude=pi" in r for r in fake.requests if r.startswith("/logs"))
    events = (folder / f"{N1}-events.log").read_text().splitlines()
    assert [e.split("\t")[1] for e in events] == ["[collector] disconnected (connection closed by node)",
                                                  "[openocd] Error: clearing lockup after double fault"]
    assert state["nodes"][N1].other == {"collector": 1, "openocd": 1}
    assert "[pi]" not in "".join(f.read_text() for f in folder.glob("*.log"))


def test_a_line_recovered_after_the_lines_that_came_live_is_pulled_once_and_read_in_its_place(make, tmp_path):
    fake, base = make(next_is_last_by_time=True)  # the worst case for a duplicate: `next` is not the highest id
    history(fake, N1)
    fake.add(N1, SINCE + timedelta(minutes=1), "260101T000100.0000 0T H #01 SLOT ph=1")
    do_pull(base, tmp_path, names=(N1,))
    fake.add(N1, SINCE + timedelta(minutes=3), "260101T000300.0000 0T H #03 SLOT ph=1")
    fake.add(N1, SINCE + timedelta(minutes=2), "260101T000200.0000 0T H #02 SLOT ph=1")  # recovered: older, stored later
    fake.add(N1, SINCE + timedelta(minutes=4), "260101T000400.0000 0T H #04 SLOT ph=1")

    do_pull(base, tmp_path, names=(N1,))
    do_pull(base, tmp_path, names=(N1,))

    assert device_lines(tmp_path / "record", N1) == [f"260101T00{m:02d}00.0000 0T H #{m:02x} SLOT ph=1"
                                                      for m in (1, 2, 3, 4)]


def test_a_recovered_line_above_the_cursor_of_its_own_answer_is_not_returned_a_second_time(make, tmp_path):
    fake, base = make(next_is_last_by_time=True)
    history(fake, N1)
    fake.add(N1, SINCE + timedelta(minutes=1), "260101T000100.0000 0T H #01 SLOT ph=1")
    do_pull(base, tmp_path, names=(N1,))
    fake.add(N1, SINCE + timedelta(minutes=3), "260101T000300.0000 0T H #03 SLOT ph=1")
    fake.add(N1, SINCE + timedelta(minutes=2), "260101T000200.0000 0T H #02 SLOT ph=1")

    do_pull(base, tmp_path, names=(N1,))
    again = do_pull(base, tmp_path, names=(N1,))

    assert again["nodes"][N1].lines == 3
    assert len((tmp_path / "record" / f"{N1}-20261007.log").read_text().splitlines()) == 3


def test_two_runs_of_the_same_build_in_one_folder_keep_each_others_first_lines(make, tmp_path):
    fake, base = make()
    history(fake, N1, datetime(2026, 10, 7, 8, 40, tzinfo=UTC))
    first = soak(datetime(2026, 10, 7, 8, 43, tzinfo=UTC), 0.05, "16ee4e5-o954ffd")
    second = soak(datetime(2026, 10, 7, 14, 8, tzinfo=UTC), 0.05, "16ee4e5-o954ffd")
    for t, text in first + second:
        fake.add(N1, t, text)
    # the two boot sequences print the same device lines: only the time the Pi stamps differs
    assert first[0][1].split(" ", 1)[1] == second[0][1].split(" ", 1)[1]
    pulling_since = datetime(2026, 10, 7, 8, 40, tzinfo=UTC)

    pull(base, [N1], "16ee4e5-o954ffd", pulling_since, tmp_path / "record", PARIS, schema_source=no_git)

    assert len(device_lines(tmp_path / "record", N1, pulling_since)) == len(first) + len(second)


def test_a_chunk_is_all_or_nothing_so_an_interrupted_pull_goes_on_without_a_duplicate_or_a_hole(make, tmp_path):
    fake, base = make(cap=30)
    traces = feed(fake)
    fake.fail_logs_after = 3
    with pytest.raises(OSError):
        do_pull(base, tmp_path, chunk=30)
    fake.fail_logs_after = None

    do_pull(base, tmp_path, chunk=30)

    for node in (N1, N3):
        assert device_lines(tmp_path / "record", node) == expected(traces, node)


def test_a_forward_pull_cut_short_goes_on_from_the_last_committed_chunk(make, tmp_path):
    fake, base = make(cap=25)
    feed(fake, hours=0.05)
    do_pull(base, tmp_path)
    more = soak(SINCE + timedelta(hours=1), 0.2, "e34092f-o954ffd")
    for t, text in more:
        fake.add(N3, t, text)
    served = fake._logs_served
    fake.fail_logs_after = served + 2
    with pytest.raises(OSError):
        do_pull(base, tmp_path, chunk=25)
    fake.fail_logs_after = None
    partial = len(device_lines(tmp_path / "record", N3))

    state = do_pull(base, tmp_path, chunk=25)

    assert 0 < partial < state["nodes"][N3].lines
    assert device_lines(tmp_path / "record", N3).count(more[0][1]) == 1
    assert state["nodes"][N3].lines == len(device_lines(tmp_path / "record", N3))


def test_what_a_crash_appended_beyond_the_last_committed_chunk_is_cut_not_kept_twice(make, tmp_path):
    fake, base = make()
    traces = feed(fake, names=(N1,))
    do_pull(base, tmp_path, names=(N1,))
    path = tmp_path / "record" / f"{N1}-20261007.log"
    committed = path.read_bytes()
    with path.open("ab") as f:  # the chunk was appended, the cursor was not saved
        f.write(b"2026-10-07T14:30:00.000000Z\t260101T000000.0000 0S A #00 BOOT build=x\n")
    (tmp_path / "record" / f"{N1}-20261008.log").write_text("2026-10-08T00:00:00.000000Z\tstray\n")

    do_pull(base, tmp_path, names=(N1,))

    assert path.read_bytes() == committed
    assert not (tmp_path / "record" / f"{N1}-20261008.log").exists()
    assert device_lines(tmp_path / "record", N1) == expected(traces, N1)


def test_a_collector_that_starts_its_storage_over_never_shrinks_the_folder_and_makes_the_node_unfit(make, tmp_path):
    fake, base = make()
    feed(fake, names=(N1,))
    do_pull(base, tmp_path, names=(N1,))
    before = folder_bytes(tmp_path / "record")
    fake.reset(N1)
    fake.add(N1, SINCE + timedelta(hours=1), "260101T010000.0000 0S A #00 BOOT build=x")

    state = do_pull(base, tmp_path, names=(N1,))

    assert {k: v for k, v in folder_bytes(tmp_path / "record").items() if k.endswith(".log")} == \
        {k: v for k, v in before.items() if k.endswith(".log")}
    assert "behind the pull" in state["nodes"][N1].problem
    fake.add(N1, SINCE + timedelta(hours=2), "260101T020000.0000 0S A #00 BOOT build=y")
    assert "behind the pull" in do_pull(base, tmp_path, names=(N1,))["nodes"][N1].problem  # it stays so


def test_a_collector_that_no_longer_holds_the_start_of_the_run_makes_the_node_unfit_and_is_not_asked(make, tmp_path):
    fake, base = make()
    feed(fake, names=(N1,), hours=0.3)
    fake.drop_oldest(N1, 150)  # its retention: the first lines, the boot among them, are gone

    state = do_pull(base, tmp_path, names=(N1,))

    assert "no longer holds the beginning" in state["nodes"][N1].problem
    assert not [r for r in fake.requests if r.startswith("/logs")]
    assert state["nodes"][N1].complete_to is None


def test_a_node_the_collector_does_not_know_is_left_for_the_judge_to_name(make, tmp_path):
    fake, base = make(nodes=(N1,))

    state = do_pull(base, tmp_path, names=(N1, "nuna-node-09"))

    assert state["nodes"]["nuna-node-09"].complete_to and state["nodes"]["nuna-node-09"].status == {}
    assert not [r for r in fake.requests if r.startswith("/logs") and "node-09" in r]


def test_the_collector_s_view_of_each_node_at_the_end_of_the_pull_is_kept_without_an_address(make, tmp_path):
    fake, base = make()
    feed(fake, names=(N1,))
    fake.status[N1].update(pi_ntp="unsynced", clock_lag_s=7.5, last_error="TimeoutError")

    state = do_pull(base, tmp_path, names=(N1,))

    status = state["nodes"][N1].status
    assert (status["pi_ntp"], status["clock_lag_s"], status["last_error"]) == ("unsynced", 7.5, "TimeoutError")
    assert status["stored_bytes"] > 0 and status["oldest_line_at"].endswith("+02:00")  # as the collector writes it, Paris time
    assert "127.0.0.1" not in (tmp_path / "record" / "pull.json").read_text()


def test_a_folder_is_only_pulled_for_the_build_and_the_start_it_was_made_for(make, tmp_path):
    fake, base = make()
    feed(fake, names=(N1,))
    do_pull(base, tmp_path, names=(N1,))

    with pytest.raises(PullError, match="was pulled for build e34092f-o954ffd, not abc1234"):
        pull(base, [N1], "abc1234", SINCE, tmp_path / "record", PARIS, schema_source=no_git)
    with pytest.raises(PullError, match="pull into a new folder"):
        do_pull(base, tmp_path, names=(N1,), since=SINCE - timedelta(hours=3))
    do_pull(base, tmp_path, names=(N1,), since=SINCE + timedelta(hours=3))  # a later start is only the judge's business


def test_a_folder_of_capture_files_that_no_pull_made_is_not_pulled_into(make, tmp_path):
    fake, base = make()
    old = tmp_path / "record"
    old.mkdir()
    (old / f"{N1}-20261007.log").write_text("2026-10-07T14:00:00.000000Z\tx\n")

    with pytest.raises(PullError, match="not a pulled record"):
        do_pull(base, tmp_path, names=(N1,))


def test_a_line_with_no_offset_is_in_the_pi_s_zone_and_a_stray_newline_does_not_split_it():
    assert pulling._utc("2026-10-07T04:28:49.349199", ZoneInfo("Europe/Paris")) == \
        datetime(2026, 10, 7, 2, 28, 49, 349199, tzinfo=UTC)
    assert pulling._utc("2026-10-07T04:28:49.349199+02:00", UTC) == \
        datetime(2026, 10, 7, 2, 28, 49, 349199, tzinfo=UTC)


def test_the_state_names_the_build_the_cursor_and_no_collector_address(make, tmp_path):
    fake, base = make()
    feed(fake, names=(N1,))
    do_pull(base, tmp_path, names=(N1,))

    data = json.loads((tmp_path / "record" / "pull.json").read_text())

    assert data["build"] == BUILD and data["firmware_commit"] == "e34092f"
    assert data["nodes"][N1]["cursor"].startswith("1:1:")
    assert data["start"] == "2026-10-07T14:01:55.000000Z"
    assert base not in json.dumps(data)


OLD_SCHEMA = 'EVENTS = {"RX_WIN": Event("T", ("last", "cap"), "")}\n'


def test_the_schema_of_the_build_is_kept_in_the_folder(make, tmp_path):
    fake, base = make()
    asked = []

    def source(commit):
        asked.append(commit)
        return OLD_SCHEMA

    pull(base, [N1], BUILD, SINCE, tmp_path / "record", PARIS, schema_source=source)

    assert asked == ["e34092f"]
    assert json.loads((tmp_path / "record" / "schema.json").read_text()) == \
        {"RX_WIN": {"mod": "T", "keys": ["last", "cap"], "optional": []}}
    assert load_state(tmp_path / "record")["schema"] == {"source": "commit e34092f", "events": 1, "dirty": False}


@pytest.mark.parametrize("source, why", [(lambda c: None, "git has no commit e34092f"),
                                         (lambda c: "x = 1\n", "could not be read")])
def test_a_schema_that_cannot_be_had_is_said_and_not_invented(make, tmp_path, source, why):
    fake, base = make()

    pull(base, [N1], BUILD, SINCE, tmp_path / "record", PARIS, schema_source=source)

    record = load_state(tmp_path / "record")["schema"]
    assert record["source"] == "checkout" and why in record["why"]
    assert not (tmp_path / "record" / "schema.json").exists()


def test_the_command_line_reports_a_line_per_node_and_the_exit_code(make, tmp_path, capsys, monkeypatch):
    monkeypatch.delenv("BENCH_COLLECTOR_URL", raising=False)
    fake, base = make()
    feed(fake)
    monkeypatch.setattr(pulling, "schema_from_git", no_git)
    argv = ["--collector", base, "--build", BUILD, "--since", "2026-10-07T14:06:55Z", "--node", "2=" + N3,
            "--node", "5=" + N1, "--dir", str(tmp_path / "cli")]

    assert main(argv) == 0
    out = capsys.readouterr().out.splitlines()
    assert len(out) == 2 and all("complete to" in line for line in out)

    fake.drop_oldest(N1, 1)
    assert main(argv[:-1] + [str(tmp_path / "cli2")]) == 4
    assert "UNFIT" in capsys.readouterr().out
    assert main(["--build", BUILD, "--since", "2026-10-07T14:06:55Z", "--node", "2=" + N3, "--dir", str(tmp_path / "x")]) == 3
    assert "no collector" in capsys.readouterr().err
