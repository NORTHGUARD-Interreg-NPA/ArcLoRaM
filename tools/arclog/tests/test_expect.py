"""expect: a run decided from capture lines, offline and following the files."""

from datetime import datetime, timedelta, timezone

import pytest

from arclog.capture import DailyWriter
from arclog.cli import main
from arclog.expect import (FAIL, PASS, TIMEOUT, DirFollower, Run, parse_duration, parse_since,
                           replay, spec_from_dict)
from arclog.model import parse_line
from arclog.schema import EVENTS, events_from_source

SINCE = datetime(2026, 9, 29, 10, 0, 0, tzinfo=timezone.utc)
UID = "0026001a3232501420383543"


class Trace:
    """Lines of several nodes, with host times as seconds after SINCE."""

    def __init__(self) -> None:
        self.lines = []
        self._seq: dict[tuple[str, str], int] = {}

    def emit(self, dt: float, node: str, core: str, event: str, skip: int = 0, **fields) -> None:
        key = (node, core)
        seq = 0 if event == "BOOT" else (self._seq.get(key, -1) + 1 + skip) % 256
        self._seq[key] = seq
        body = "".join(f" {k}={v}" for k, v in fields.items())
        text = f"260929T100000.0000 {core}{EVENTS[event].mod} A #{seq:02x} {event}{body}"
        self.lines.append(parse_line(text, node=node, host_time=SINCE + timedelta(seconds=dt)))

    def boot(self, dt: float, node: str, build: str = "b1", cls: str = "C2", id: str = "2") -> None:
        """Both cores booting, CM4 first, as the firmware does."""
        self.emit(dt, node, "4", "BOOT", build=build)
        self.emit(dt + 0.1, node, "4", "CORE_SYNC", stage="linked")
        self.emit(dt + 0.3, node, "0", "BOOT", cls=f"{cls}", id=id, uid=UID, fw="1.5.0", build=build)

    def clk(self, dt: float, node: str, to: str = "WARM") -> None:
        self.emit(dt, node, "0", "CLK", **{"from": "ACQ", "to": to, "why": "tier1"})

    def idle(self, dt: float, node: str) -> None:
        """A harmless line that moves the clock forward."""
        self.emit(dt, node, "0", "INIT_DONE", phases=3)


def spec(**overrides):
    d = {"timeout": "10m", "build": "b1", "nodes": {"n2": {}}}
    d.update(overrides)
    return spec_from_dict(d)


def decide(s, trace, end: float | None = None):
    """Replays the trace, then ticks the clock to `end` seconds when given."""
    msgs = []
    run = Run(s, SINCE, report=msgs.append)
    replay(run, trace.lines)
    if run.verdict is None and end is not None:
        run.tick(SINCE + timedelta(seconds=end))
    return run.verdict, msgs


# --- parsing ---------------------------------------------------------------


@pytest.mark.parametrize("value, seconds", [
    ("30s", 30), ("5m", 300), ("1h", 3600), ("500ms", 0.5), (12, 12), ("2.5", 2.5),
])
def test_durations(value, seconds):
    assert parse_duration(value) == timedelta(seconds=seconds)


@pytest.mark.parametrize("value", ["soon", "5 minutes", True, "-3s"])
def test_invalid_durations_are_refused(value):
    with pytest.raises(ValueError):
        parse_duration(value)


def test_since_is_utc():
    assert parse_since("2026-09-29T10:00:00Z") == SINCE
    assert parse_since("2026-09-29T12:00:00+02:00") == SINCE


@pytest.mark.parametrize("d, problem", [
    ({"nodes": {"n2": {}}}, "'timeout' is required"),
    ({"timeout": "1m"}, "at least one node"),
    ({"timeout": "1m", "nodes": {"n2": {"clas": "C2"}}}, "nodes.n2: unknown key(s) clas"),
    ({"timeout": "1m", "nodes": {"n2": {}}, "expect": [{"node": "n9", "event": "CLK"}]}, "not in [nodes]"),
    ({"timeout": "1m", "nodes": {"n2": {}}, "expect": [{"where": {}}]}, "'event' is required"),
    ({"timeout": "1m", "nodes": {"n2": {}}, "tiemout": "1m"}, "unknown key(s) tiemout"),
])
def test_invalid_expect_files_are_refused(d, problem):
    with pytest.raises(ValueError, match=__import__("re").escape(problem)):
        spec_from_dict(d)


# --- arming and smoke ------------------------------------------------------


def test_a_clean_boot_passes_after_the_smoke_window():
    t = Trace()
    t.boot(2, "n2")
    t.idle(10, "n2")
    verdict, msgs = decide(spec(), t)
    assert verdict is None  # 10 s in: the reset-loop watch is not over
    verdict, msgs = decide(spec(), t, end=30)
    assert verdict.code == PASS
    assert "ok   n2 armed (both cores booted build=b1) +2.3s" in msgs
    assert "ok   run armed +2.3s" in msgs


def test_lines_of_the_old_image_before_the_boot_are_ignored():
    t = Trace()
    t.emit(0.5, "n2", "0", "INIT_DONE", phases=3, junk=1)   # old image: schema problem
    t.emit(0.6, "n2", "0", "INIT_DONE", skip=40, phases=3)  # old image: lost lines
    t.boot(2, "n2")
    assert decide(spec(), t, end=30)[0].code == PASS


def test_a_stale_build_before_arming_is_waited_through():
    t = Trace()
    t.boot(1, "n2", build="old")
    t.boot(4, "n2", build="b1")
    verdict, msgs = decide(spec(), t, end=30)
    assert verdict.code == PASS
    assert any(m.startswith("note n2 core 4 booted build=old, waiting for b1") for m in msgs)


def test_never_booting_the_expected_build_fails_the_smoke_check():
    t = Trace()
    t.boot(1, "n2", build="old")
    verdict, _ = decide(spec(), t, end=30)
    assert verdict.code == FAIL
    assert verdict.reason == "smoke check: n2 no BOOT on core 0,4 with build=b1 (last booted build=old)"


def test_firmware_without_the_bench_hook_is_named():
    t = Trace()
    t.emit(1, "n2", "4", "BOOT")
    verdict, _ = decide(spec(), t, end=30)
    assert "last booted build=(none: firmware without the bench hook)" in verdict.reason


def test_missing_core_link_fails_the_smoke_check():
    t = Trace()
    t.emit(1, "n2", "4", "BOOT", build="b1")
    t.emit(1.3, "n2", "0", "BOOT", cls="C2", id="2", uid=UID, fw="1.5.0", build="b1")
    verdict, _ = decide(spec(), t, end=30)
    assert verdict.reason == "smoke check: n2 no CORE_SYNC stage=linked"


def test_wrong_class_fails():
    t = Trace()
    t.boot(1, "n2", cls="C3")
    verdict, _ = decide(spec(nodes={"n2": {"cls": "C2"}}), t)
    assert verdict.code == FAIL
    assert verdict.reason.startswith("n2 booted cls=C3, expected C2")


def test_unregistered_board_fails():
    t = Trace()
    t.boot(1, "n2", id="0")
    verdict, _ = decide(spec(), t)
    assert verdict.code == FAIL
    assert "board not registered" in verdict.reason


def test_without_build_any_boot_arms():
    t = Trace()
    t.boot(1, "n2", build="whatever")
    assert decide(spec(build=None), t, end=30)[0].code == PASS


# --- failures after arming -------------------------------------------------


def test_an_unexpected_reboot_fails():
    t = Trace()
    t.boot(1, "n2")
    t.boot(12, "n2")
    verdict, _ = decide(spec(), t)
    assert verdict.code == FAIL
    assert verdict.reason == "n2 unexpected reboot (1, 0 allowed) +12.0s"


def test_scheduled_reboots_are_allowed_and_must_reboot_the_same_build():
    t = Trace()
    t.boot(1, "n2")
    t.boot(12, "n2")
    assert decide(spec(nodes={"n2": {"reboots": 1}}), t, end=30)[0].code == PASS
    t.boot(20, "n2", build="b2")
    verdict, _ = decide(spec(nodes={"n2": {"reboots": 2}}), t)
    assert verdict.reason == "n2 core 4 booted build=b2, expected b1 +20.0s"


def test_a_cm0_rebooting_alone_fails():
    t = Trace()
    t.boot(1, "n2")
    t.emit(8, "n2", "0", "BOOT", cls="C2", id="2", uid=UID, fw="1.5.0", build="b1")
    verdict, _ = decide(spec(nodes={"n2": {"reboots": 1}}), t)
    assert verdict.reason == "n2 CM0+ rebooted alone +8.0s"


def test_lost_lines_fail():
    t = Trace()
    t.boot(1, "n2")
    t.idle(5, "n2")
    t.emit(6, "n2", "0", "INIT_DONE", skip=3, phases=3)
    verdict, _ = decide(spec(), t)
    assert verdict.reason == "n2 3 line(s) lost before core 0 #05 INIT_DONE +6.0s"


def test_schema_problems_fail():
    t = Trace()
    t.boot(1, "n2")
    t.emit(5, "n2", "0", "INIT_DONE")
    verdict, _ = decide(spec(), t)
    assert verdict.reason == "n2 INIT_DONE: missing phases +5.0s"


def test_forbidden_events_fail():
    t = Trace()
    t.boot(1, "n2")
    t.emit(5, "n2", "0", "TX_LATE", plan=1, fire=2, now=3)
    verdict, _ = decide(spec(forbid=[{"event": "TX_LATE"}]), t)
    assert verdict.code == FAIL
    assert verdict.reason.startswith("n2 forbidden any TX_LATE:")


# --- expectations ----------------------------------------------------------


def test_expectations_count_matching_lines_once_armed():
    t = Trace()
    t.boot(1, "n2")
    t.clk(3, "n2", to="ACQ")
    t.clk(5, "n2")
    t.clk(9, "n2")
    s = spec(expect=[{"node": "n2", "event": "CLK", "where": {"to": "WARM"}, "count": 2}])
    verdict, msgs = decide(s, t, end=30)
    assert verdict.code == PASS
    assert "ok   n2 CLK to=WARM (2/2) +9.0s" in msgs


def test_pass_waits_for_min_duration():
    t = Trace()
    t.boot(1, "n2")
    t.clk(5, "n2")
    s = spec(min_duration="2m", expect=[{"event": "CLK"}])
    assert decide(s, t, end=60)[0] is None
    assert decide(s, t, end=120)[0].code == PASS


def test_an_expectation_missing_its_within_fails():
    t = Trace()
    t.boot(1, "n2")
    t.idle(20, "n2")
    s = spec(expect=[{"event": "CLK", "within": "10s"}])
    verdict, _ = decide(s, t)
    assert verdict.code == FAIL
    assert verdict.reason == "any CLK not within 10s of arming (0/1)"


def test_timeout_lists_what_is_missing():
    t = Trace()
    t.boot(1, "n2")
    s = spec(timeout="1m", expect=[{"event": "CLK"}])
    verdict, _ = decide(s, t, end=60)
    assert verdict.code == TIMEOUT
    assert verdict.reason == "missing: any CLK (0/1)"


def test_the_run_arms_when_every_flashed_node_has_booted():
    t = Trace()
    t.boot(1, "n2")
    t.clk(2, "n2")         # before n3 booted: the run is not armed yet
    t.boot(6, "n3", id="3")
    t.clk(8, "n2")
    s = spec(nodes={"n2": {}, "n3": {}}, expect=[{"node": "n2", "event": "CLK", "count": 2}])
    verdict, msgs = decide(s, t, end=30)
    assert verdict is None
    assert "ok   run armed +6.3s" in msgs
    assert [m for m in msgs if "CLK" in m] == ["ok   n2 CLK (1/2) +8.0s"]


def test_a_node_not_flashed_needs_no_boot_and_counts_from_the_start():
    t = Trace()
    t.boot(1, "n2")
    t.clk(3, "n3")         # the peer, running whatever it last booted
    s = spec(nodes={"n2": {}, "n3": {"flashed": False}}, expect=[{"node": "n3", "event": "CLK"}])
    assert decide(s, t, end=30)[0].code == PASS


def test_lines_before_since_are_not_read(tmp_path):
    w = DailyWriter(tmp_path, "n2")
    w.write(SINCE - timedelta(seconds=1), "260929T095959.0000 0S A #00 INIT_DONE phases=3")
    w.write(SINCE + timedelta(seconds=1), "260929T100001.0000 0S A #01 INIT_DONE phases=3")
    w.close()
    lines = DirFollower(tmp_path, ["n2"], SINCE).poll()
    assert [ln.seq for ln in lines] == [1]


# --- following the files ---------------------------------------------------


def test_follower_reads_only_complete_new_lines_across_midnight(tmp_path):
    since = datetime(2026, 9, 29, 23, 59, 58, tzinfo=timezone.utc)
    f = DirFollower(tmp_path, ["n2"], since)
    day1 = tmp_path / "n2-20260929.log"
    day1.write_text("2026-09-29T23:59:59.000000Z\t260929T235959.0000 0S A #00 INIT_DONE phases=3\n"
                    "2026-09-29T23:59:59.500000Z\t260929T235959.5000 0S A #01 INIT", encoding="utf-8")
    assert [ln.seq for ln in f.poll()] == [0]
    with day1.open("a", encoding="utf-8") as fh:
        fh.write("_DONE phases=3\n")
    (tmp_path / "n2-20260930.log").write_text(
        "2026-09-30T00:00:01.000000Z\t260930T000001.0000 0S A #02 INIT_DONE phases=3\n", encoding="utf-8")
    assert [ln.seq for ln in f.poll()] == [1, 2]
    assert f.poll() == []


def test_files_of_other_nodes_and_older_days_are_ignored(tmp_path):
    (tmp_path / "n2-20260928.log").write_text("x\n", encoding="utf-8")
    (tmp_path / "n22-20260929.log").write_text(
        "2026-09-29T10:00:01.000000Z\t260929T100001.0000 0S A #00 INIT_DONE phases=3\n", encoding="utf-8")
    assert DirFollower(tmp_path, ["n2"], SINCE).poll() == []


# --- command line ----------------------------------------------------------


def write_capture(directory, trace):
    writers = {}
    for ln in trace.lines:
        writers.setdefault(ln.node, DailyWriter(directory, ln.node)).write(ln.host_time, ln.raw)
    for w in writers.values():
        w.close()


def test_cli_offline_pass_and_fail(tmp_path, capsys):
    t = Trace()
    t.boot(1, "n2")
    t.clk(40, "n2")
    write_capture(tmp_path, t)
    spec_file = tmp_path / "run.toml"
    spec_file.write_text('timeout = "5m"\nbuild = "b1"\n[nodes.n2]\n[[expect]]\nevent = "CLK"\n',
                         encoding="utf-8")
    args = ["expect", str(spec_file), "--dir", str(tmp_path), "--since", "2026-09-29T10:00:00Z"]
    assert main(args) == PASS
    assert "PASS +40.0s" in capsys.readouterr().out

    spec_file.write_text('timeout = "5m"\nbuild = "b2"\n[nodes.n2]\n', encoding="utf-8")
    assert main(args) == FAIL
    assert "FAIL smoke check: n2 no BOOT on core 0,4 with build=b2" in capsys.readouterr().out


def test_cli_offline_undecided_is_a_timeout(tmp_path, capsys):
    t = Trace()
    t.boot(1, "n2")
    write_capture(tmp_path, t)
    spec_file = tmp_path / "run.toml"
    spec_file.write_text('timeout = "5m"\n[nodes.n2]\n', encoding="utf-8")
    assert main(["expect", str(spec_file), "--dir", str(tmp_path), "--since", "2026-09-29T10:00:00Z"]) == TIMEOUT
    assert "TIMEOUT trace ends before a verdict" in capsys.readouterr().out


def test_cli_invalid_expect_file_exits_3(tmp_path, capsys):
    spec_file = tmp_path / "run.toml"
    spec_file.write_text('[nodes.n2]\n', encoding="utf-8")
    assert main(["expect", str(spec_file), "--dir", str(tmp_path), "--since", "now"]) == 3
    assert "'timeout' is required" in capsys.readouterr().err


def test_cli_follow_decides_on_the_clock(tmp_path, capsys):
    """Following live: a run that started 31 s ago with a clean boot passes at once."""
    since = datetime.now(timezone.utc) - timedelta(seconds=31)
    w = DailyWriter(tmp_path, "n2")
    t = Trace()
    t.boot(0, "n2")
    for ln in t.lines:
        w.write(since + (ln.host_time - SINCE) + timedelta(seconds=1), ln.raw)
    w.close()
    spec_file = tmp_path / "run.toml"
    spec_file.write_text('timeout = "5m"\nbuild = "b1"\n[nodes.n2]\n', encoding="utf-8")
    assert main(["expect", str(spec_file), "--dir", str(tmp_path), "--since",
                 since.strftime("%Y-%m-%dT%H:%M:%S.%fZ"), "--follow", "--poll", "0.01"]) == PASS


def test_a_boot_glued_to_a_line_cut_by_the_reset_still_arms():
    """Seen on the bench: the old image's last line cut by the flash, then the new CM4 BOOT."""
    t = Trace()
    t.boot(1, "n2", build="old")
    t.lines.append(parse_line("260929T100002.0000 0S A �260929T100003.0000 4S A #00 BOOT build=b1",
                              node="n2", host_time=SINCE + timedelta(seconds=3)))
    t._seq[("n2", "4")] = 0  # the glued BOOT was #00
    t.emit(3.1, "n2", "4", "CORE_SYNC", stage="linked")
    t.emit(3.3, "n2", "0", "BOOT", cls="C2", id="2", uid=UID, fw="1.5.0", build="b1")
    verdict, msgs = decide(spec(), t, end=30)
    assert verdict.code == PASS
    assert "ok   n2 armed (both cores booted build=b1) +3.3s" in msgs


def test_the_boot_that_arms_the_run_is_not_counted():
    """An expectation on BOOT counts reboots after arming, not the boot that armed the run."""
    t = Trace()
    t.boot(1, "n2")
    s = spec(nodes={"n2": {"reboots": 1}}, expect=[{"node": "n2", "event": "BOOT", "where": {"cls": "C2"}}])
    assert decide(s, t, end=30)[0] is None
    t.boot(12, "n2")
    verdict, msgs = decide(s, t, end=30)
    assert verdict.code == PASS
    assert "ok   n2 BOOT cls=C2 (1/1) +12.3s" in msgs


def test_a_node_ignores_its_lines_before_its_own_flash_ended():
    """Reflashing the build a board already runs: the reboot before the flash must not arm it."""
    t = Trace()
    t.boot(1, "n2")            # the UID read before the flash reboots the same build
    t.boot(6, "n2")            # the flash's own reboot
    since2 = (SINCE + timedelta(seconds=5)).isoformat()
    verdict, msgs = decide(spec(nodes={"n2": {"since": since2}}), t, end=40)
    assert verdict.code == PASS, verdict
    assert "ok   n2 armed (both cores booted build=b1) +6.3s" in msgs
    # Without it, the flash's reboot is taken for an unexpected one.
    assert decide(spec(), t, end=40)[0].reason == "n2 unexpected reboot (1, 0 allowed) +6.0s"


# --- range conditions ------------------------------------------------------


def sync_rx(t: Trace, dt: float, node: str, err) -> None:
    t.emit(dt, node, "0", "SYNC_RX", ph=0, ce=1, ep=0, st=0, exp=0, err=err, erru=err * 1000, clk="WARM", act="t1")


def test_a_range_counts_only_lines_inside_it():
    t = Trace()
    t.boot(1, "n2")
    for dt, err in ((3, -3), (4, -1), (5, 0), (6, 1), (7, 2)):
        sync_rx(t, dt, "n2", err)
    s = spec(expect=[{"node": "n2", "event": "SYNC_RX", "where": {"err": {"min": -1, "max": 1}},
                      "count": 3}])
    verdict, msgs = decide(s, t, end=30)
    assert verdict.code == PASS
    assert "ok   n2 SYNC_RX err=-1..1 (3/3) +6.0s" in msgs


def test_a_one_sided_range_in_a_forbid_fails_only_outside_the_bound():
    t = Trace()
    t.boot(1, "n2")
    sync_rx(t, 3, "n2", -1)
    sync_rx(t, 4, "n2", 2)
    s = spec(forbid=[{"event": "SYNC_RX", "where": {"err": {"min": 2}}}])
    verdict, _ = decide(s, t)
    assert verdict.code == FAIL
    assert verdict.reason.startswith("n2 forbidden any SYNC_RX err=2..:")
    assert "err=2" in verdict.reason


def test_a_range_never_matches_a_field_that_is_not_a_number():
    t = Trace()
    t.boot(1, "n2")
    t.clk(3, "n2")
    s = spec(forbid=[{"event": "CLK", "where": {"to": {"max": 0}}}])
    verdict, _ = decide(s, t, end=30)
    assert verdict.code == PASS


@pytest.mark.parametrize("cond, problem", [
    ({}, "needs min, max or both"),
    ({"mn": 1}, "unknown key(s) mn"),
    ({"min": "a"}, "min must be a number"),
    ({"min": 2, "max": 1}, "min 2 is above max 1"),
])
def test_invalid_ranges_are_refused(cond, problem):
    d = {"timeout": "1m", "nodes": {"n2": {}},
         "expect": [{"event": "SYNC_RX", "where": {"err": cond}}]}
    with pytest.raises(ValueError, match=__import__("re").escape(problem)):
        spec_from_dict(d)


def test_a_run_checks_the_lines_with_the_schema_it_is_given():
    t = Trace()
    t.boot(1, "n2")
    t.emit(5, "n2", "0", "RX_WIN", last=1, cap=2)  # a build from before g and win
    old = events_from_source('EVENTS = {"RX_WIN": Event("T", ("last", "cap"), ""), "BOOT": Event("S", (), "", '
                             '("cls", "id", "uid", "fw", "build", "sync")), '
                             '"CORE_SYNC": Event("X", ("stage",), "")}\n')

    run = Run(spec(), SINCE, events=old)
    replay(run, t.lines)
    other = Run(spec(), SINCE)
    replay(other, t.lines)

    assert run.verdict is None or run.verdict.code != FAIL
    assert other.verdict is not None and "missing g,win" in other.verdict.reason


def test_what_a_run_still_waits_for_is_listed():
    s = spec(expect=[{"node": "n2", "event": "CLK", "where": {"to": "WARM"}}, {"node": "n2", "event": "SLOT", "count": 3}])
    t = Trace()
    t.boot(1, "n2")
    run = Run(s, SINCE)
    replay(run, t.lines)
    run.tick(SINCE + timedelta(seconds=40))

    assert run.pending() == ["n2 CLK to=WARM (0/1)", "n2 SLOT (0/3)"]
