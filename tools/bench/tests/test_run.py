"""Scenarios (parsing, validation, shorthand, saving) and bench run (actions, verdict, record)."""

from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from arclog.capture import DailyWriter
from bench.boards import Board, format_uid
from bench.run import RunOutcome, Scheduler, expect_spec, follow, slice_capture, write_record
from bench.scenario import from_dict, load, parse_expect_text, plan, to_toml

SCENARIOS = Path(__file__).resolve().parents[1] / "scenarios"
T0 = datetime(2026, 9, 29, 10, 0, tzinfo=timezone.utc)
UID1 = (0x0014008F, 0x32325014, 0x20383543)
UID2 = (0x0026001A, 0x32325014, 0x20383543)


# --- scenarios --------------------------------------------------------------------


@pytest.mark.parametrize("path", sorted(SCENARIOS.glob("*.toml")), ids=lambda p: p.name)
def test_the_example_scenarios_are_valid(path):
    assert plan(load(path))


def test_a_minimal_scenario_has_defaults():
    s = from_dict({"nodes": {"2": "C2"}})
    assert s.flashed == {2: "C2"} and s.timeout == timedelta(minutes=10)


@pytest.mark.parametrize("d, problem", [
    ({}, "must name at least one board"),
    ({"nodes": {"two": "C2"}}, "'two' is not a Node ID"),
    ({"nodes": {"2": "C4"}}, "'C4' is not C1, C2, C3 or \"watch\""),
    ({"nodes": {"2": "watch"}}, "no node is flashed"),
    ({"nodes": {"2": "C2"}, "expect": [{"event": "SYNC_RXX"}]}, "unknown event 'SYNC_RXX' (did you mean SYNC_RX?)"),
    ({"nodes": {"2": "C2"}, "expect": [{"event": "CLK", "where": {"too": "WARM"}}]},
     "CLK has no field 'too' (did you mean to?)"),
    ({"nodes": {"2": "C2"}, "expect": [{"event": "CLK", "node": 3}]}, "Node ID 3 is not in [nodes]"),
    ({"nodes": {"2": "C2"}, "expect": [{"event": "CLK", "wihtin": "3m"}]}, "unknown key 'wihtin' (did you mean within?)"),
    ({"nodes": {"2": "C2"}, "tiemout": "3m"}, "unknown key 'tiemout' (did you mean timeout?)"),
    ({"nodes": {"2": "C2"}, "action": [{"reset": 2}]}, "either at ="),
    ({"nodes": {"2": "C2"}, "action": [{"reset": 2, "at": "+10s", "after": {"event": "CLK"}}]}, "either at ="),
    ({"nodes": {"2": "C2", "3": "watch"}, "action": [{"reset": 3, "at": "10s"}]}, "only watched"),
    ({"nodes": {"2": "C2"}, "action": [{"halt": 2, "at": "10s"}]}, "unknown key 'halt'"),
    ({"nodes": {"2": "C2"}, "overrides": {"BENCH_BUILD_ID": "x"}}, "set by bench"),
    ({"nodes": {"2": "C2"}, "dataset": "yes"}, "dataset must be true or false"),
    ({"nodes": {"2": "C2"}, "max_silence": "soon"}, "max_silence"),
])
def test_invalid_scenarios_say_what_is_wrong(d, problem):
    with pytest.raises(ValueError) as exc:
        from_dict(d)
    assert problem in str(exc.value)


def test_a_scenario_is_not_a_dataset_session_unless_it_says_so(tmp_path):
    assert from_dict({"nodes": {"2": "C2"}}).dataset is False
    d = {"nodes": {"2": "C2"}, "dataset": True}
    assert from_dict(d).dataset is True
    path = tmp_path / "s.toml"
    path.write_text(to_toml(d), encoding="utf-8")
    assert load(path).dataset is True
    assert "dataset session" in plan(from_dict(d))
    assert "dataset session" not in plan(from_dict({"nodes": {"2": "C2"}}))


def test_max_silence_is_off_unless_the_scenario_sets_it(tmp_path):
    assert from_dict({"nodes": {"2": "C2"}}).max_silence is None
    d = {"nodes": {"2": "C2"}, "max_silence": "5m"}
    assert from_dict(d).max_silence == timedelta(minutes=5)
    path = tmp_path / "s.toml"
    path.write_text(to_toml(d), encoding="utf-8")
    assert load(path).max_silence == timedelta(minutes=5)
    assert "silent for more than 5m" in plan(from_dict(d))
    assert "silent for more than" not in plan(from_dict({"nodes": {"2": "C2"}}))


def test_expect_shorthand():
    assert parse_expect_text("2 CLK to=WARM within=7m count=2") == {
        "event": "CLK", "node": "2", "where": {"to": "WARM"}, "within": "7m", "count": 2}
    assert parse_expect_text("any SYNC_TX") == {"event": "SYNC_TX"}
    with pytest.raises(ValueError, match="field=value"):
        parse_expect_text("2 CLK WARM")


def test_saved_scenarios_read_back_the_same(tmp_path):
    d = {"description": 'C2 "rejoin"', "timeout": "20m", "nodes": {"1": "C3", "2": "C2", "3": "watch"},
         "overrides": {"TX_RAMP_MS": "5u"},
         "action": [{"reset": 2, "after": {"node": 2, "event": "CLK", "where": {"to": "WARM"}}, "delay": "10s"},
                    {"reset": 1, "at": "+5m"}],
         "expect": [parse_expect_text("2 CLK to=WARM within=18m count=2")],
         "forbid": [{"event": "TX_LATE"}]}
    path = tmp_path / "s.toml"
    path.write_text(to_toml(d), encoding="utf-8")
    assert load(path) == from_dict(d)


# --- running ------------------------------------------------------------------------

BOARDS = {1: Board("SN1", "COM8", UID1, "trace", 1), 2: Board("SN2", "COM9", UID2, "trace", 2)}


def boot(w, t, uid, cls, nid, build="b1"):
    w.write(t, f"000000T000000.9997 4S A #00 BOOT build={build}")
    w.write(t + timedelta(seconds=0.1), "000000T000000.9997 4X M #01 CORE_SYNC stage=linked")
    w.write(t + timedelta(seconds=0.3), f"000101T000000.0031 0S A #00 BOOT cls={cls} id={nid} "
                                        f"uid={format_uid(uid)} fw=1.5.0 build={build}")


class Bench:
    """Boards on a simulated clock: a reset writes a new boot into the capture."""

    def __init__(self, capture_dir):
        self.dir = capture_dir
        self.t = T0
        self.w = {"com8": DailyWriter(capture_dir, "com8"), "com9": DailyWriter(capture_dir, "com9")}
        self.resets = []
        self.scripted: list[tuple[datetime, str, str]] = []   # (time, node, raw line)

    def line(self, dt, node, raw):
        self.scripted.append((T0 + timedelta(seconds=dt), node, raw))

    def now(self):
        return self.t

    def sleep(self, s):
        self.t += timedelta(seconds=s)
        for when, node, raw in [x for x in self.scripted if x[0] <= self.t]:
            self.w[node].write(when, raw)
            self.scripted.remove((when, node, raw))

    def reset(self, sn):
        self.resets.append((sn, self.t))
        node = {"SN1": "com8", "SN2": "com9"}[sn]
        uid, cls, nid = (UID1, "C3", 1) if sn == "SN1" else (UID2, "C2", 2)
        boot(self.w[node], self.t + timedelta(seconds=1), uid, cls, nid)
        # After the reboot the C2 locks again 20 s later.
        if sn == "SN2":
            self.line((self.t - T0).total_seconds() + 21, "com9",
                      "000102T000100.0000 0Y L #01 CLK from=ACQ to=WARM why=lock")


def clk(seq):
    return f"000102T000100.0000 0Y L #{seq:02x} CLK from=ACQ to=WARM why=lock"


def run_it(bench, s, **kw):
    msgs = []
    out = follow(s, BOARDS, "b1", bench.dir, T0, reset=bench.reset, report=msgs.append,
                 now=bench.now, sleep=bench.sleep, **kw)
    return out, msgs


def test_a_reset_fires_after_its_event_and_the_run_waits_for_the_reboot(tmp_path):
    bench = Bench(tmp_path)
    boot(bench.w["com8"], T0 + timedelta(seconds=1), UID1, "C3", 1)
    boot(bench.w["com9"], T0 + timedelta(seconds=1), UID2, "C2", 2)
    bench.line(40, "com9", clk(1))
    s = load(SCENARIOS / "c2-rejoin-after-reset.toml")
    out, msgs = run_it(bench, s)
    assert out.code == 0, out.verdict
    assert [(sn, (t - T0).total_seconds()) for sn, t in bench.resets] == [("SN2", 50.0)]
    assert any(m.startswith("act  reset 2 10s after 2 CLK to=WARM") for m in msgs)
    assert "ok   com9 BOOT cls=C2 (1/1) +51.3s" in msgs


def test_a_reset_at_an_offset_from_arming(tmp_path):
    bench = Bench(tmp_path)
    boot(bench.w["com9"], T0 + timedelta(seconds=2), UID2, "C2", 2)
    s = from_dict({"nodes": {"2": "C2"}, "timeout": "5m", "action": [{"reset": 2, "at": "+30s"}]})
    out, _ = run_it(bench, s)
    assert out.code == 0, out.verdict
    assert [(t - T0).total_seconds() for _, t in bench.resets] == [33.0]  # armed at +2.3 s, polled each second


def test_a_run_does_not_pass_before_its_reset(tmp_path):
    """Everything else is met at once; without the implicit reboot expectation it would pass at +30 s."""
    bench = Bench(tmp_path)
    boot(bench.w["com9"], T0 + timedelta(seconds=2), UID2, "C2", 2)
    s = from_dict({"nodes": {"2": "C2"}, "timeout": "5m", "action": [{"reset": 2, "at": "+2m"}]})
    out, _ = run_it(bench, s)
    assert out.code == 0 and (out.ended - T0).total_seconds() > 120


def test_an_action_whose_event_never_comes_times_out(tmp_path):
    bench = Bench(tmp_path)
    boot(bench.w["com9"], T0 + timedelta(seconds=2), UID2, "C2", 2)
    s = from_dict({"nodes": {"2": "C2"}, "timeout": "2m",
                   "action": [{"reset": 2, "after": {"node": 2, "event": "CLK", "where": {"to": "WARM"}}}]})
    out, msgs = run_it(bench, s)
    assert out.code == 2
    assert "com9 BOOT cls=C2 (0/1)" in out.verdict
    assert "note action not fired: reset 2 0s after 2 CLK to=WARM" in msgs


def test_a_reboot_nobody_asked_for_fails(tmp_path):
    bench = Bench(tmp_path)
    boot(bench.w["com9"], T0 + timedelta(seconds=2), UID2, "C2", 2)
    boot(bench.w["com9"], T0 + timedelta(seconds=20), UID2, "C2", 2)
    out, _ = run_it(bench, from_dict({"nodes": {"2": "C2"}, "timeout": "2m"}))
    assert out.code == 1 and "unexpected reboot" in out.verdict


def test_watched_nodes_are_checked_but_not_armed():
    s = from_dict({"nodes": {"2": "C2", "1": "watch"}, "expect": [{"node": 1, "event": "SYNC_TX"}]})
    spec = expect_spec(s, BOARDS, "b1")
    assert spec["nodes"] == {"com9": {"cls": "C2", "reboots": 0}, "com8": {"flashed": False}}
    assert spec["expect"] == [{"event": "SYNC_TX", "where": {}, "count": 1, "node": "com8"}]


def test_the_scheduler_ignores_events_before_arming():
    s = from_dict({"nodes": {"2": "C2"}, "action": [{"reset": 2, "after": {"node": 2, "event": "CLK"}}]})
    sched = Scheduler(s.actions, {2: "com9"})
    from arclog.model import parse_line
    early = parse_line(clk(1), node="com9", host_time=T0 + timedelta(seconds=1))
    late = parse_line(clk(2), node="com9", host_time=T0 + timedelta(seconds=9))
    sched.observe(early, armed_at=T0 + timedelta(seconds=5))
    assert sched.due(T0 + timedelta(seconds=20), T0 + timedelta(seconds=5)) == []
    sched.observe(late, armed_at=T0 + timedelta(seconds=5))
    assert sched.due(T0 + timedelta(seconds=20), T0 + timedelta(seconds=5)) == [0]


# --- record ------------------------------------------------------------------------


def test_the_record_keeps_the_scenario_the_log_and_the_capture_window(tmp_path):
    cap, out = tmp_path / "cap", tmp_path / "run"
    w = DailyWriter(cap, "com9")
    w.write(T0 - timedelta(seconds=5), "before the run")
    boot(w, T0 + timedelta(seconds=1), UID2, "C2", 2)
    w.write(T0 + timedelta(minutes=5), "after the run")
    w.close()
    s = from_dict({"nodes": {"2": "C2"}})
    outcome = RunOutcome(0, "PASS every expectation met", ["ok   run armed +1.3s", "PASS +30.0s"], {},
                         T0, T0 + timedelta(seconds=30))
    slice_capture(cap, out, ["com9"], T0, outcome.ended)
    report = write_record(out, "[nodes]\n2 = \"C2\"\n", s, "b1", outcome, {2: BOARDS[2]},
                          [Board("SN1", "COM8", UID1, "trace", 1, "old")])
    text = report.read_text()
    assert "# bench run 2026-09-29 10:00:00Z: PASS" in text
    assert "| 2 | flashed as C2 | COM9 | `SN2` | `b1` |" in text
    assert "| 1 | not in the scenario | COM8 | `SN1` | `old` |" in text
    assert (out / "scenario.toml").read_text() == "[nodes]\n2 = \"C2\"\n"
    lines = (out / "com9-20260929.log").read_text().splitlines()
    assert len(lines) == 3 and "BOOT" in lines[0]


def test_each_flashed_node_arms_only_after_its_own_flash():
    s = from_dict({"nodes": {"2": "C2", "1": "watch"}})
    spec = expect_spec(s, BOARDS, "b1", flashed={2: T0 + timedelta(seconds=7)})
    assert spec["nodes"]["com9"]["since"] == "2026-09-29T10:00:07+00:00"
    assert "since" not in spec["nodes"]["com8"]


def test_expect_shorthand_ranges():
    assert parse_expect_text("2 SYNC_RX act=good err=-1..1")["where"] == {
        "act": "good", "err": {"min": -1, "max": 1}}
    assert parse_expect_text("any SYNC_RX err=..-2")["where"] == {"err": {"max": -2}}
    assert parse_expect_text("any SYNC_RX err=0.5..")["where"] == {"err": {"min": 0.5}}


def test_a_range_reads_back_the_same_and_shows_in_the_plan(tmp_path):
    d = {"nodes": {"1": "C3", "2": "C2"},
         "expect": [parse_expect_text("2 SYNC_RX act=good err=-1..1 count=2")],
         "forbid": [parse_expect_text("2 SYNC_RX err=..-2")]}
    path = tmp_path / "s.toml"
    path.write_text(to_toml(d), encoding="utf-8")
    s = load(path)
    assert s == from_dict(d)
    assert "expect: 2 SYNC_RX act=good err=-1..1 x2" in plan(s)
    assert "forbid: 2 SYNC_RX err=..-2" in plan(s)


def test_an_invalid_range_in_a_scenario_says_what_is_wrong():
    with pytest.raises(ValueError, match=r"expect\[0\]\.where\.err: min 2 is above max 1"):
        from_dict({"nodes": {"2": "C2"},
                   "expect": [{"event": "SYNC_RX", "where": {"err": {"min": 2, "max": 1}}}]})
