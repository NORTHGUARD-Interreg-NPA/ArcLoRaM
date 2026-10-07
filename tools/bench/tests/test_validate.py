"""bench validate: a stored record says again whether its data can be used, and what its verdict was."""

import sys
from datetime import datetime, timedelta, timezone

from test_run import BOARDS, UID1, UID2, T0, Bench, boot, run_it

if sys.version_info >= (3, 11):
    import tomllib
else:  # pragma: no cover
    import tomli as tomllib

from arclog.capture import DailyWriter
from arclog.marks import Mark, PortMark, format_mark
from bench.manifest import Host, dumps, finish_manifest, start_manifest
from bench.scenario import from_dict, to_toml
from bench.validate import assess, conclude, slice_marks

END = T0 + timedelta(seconds=40)  # the smoke window of a clean boot is over


def at(seconds: float) -> datetime:
    return T0 + timedelta(seconds=seconds)


def marks_every_10s(until: int, **down) -> list[Mark]:
    """Marks of com9 (and com8) with every port up, except {port: (down since s, up again s or None)}."""
    marks = []
    for s in range(0, until + 1, 10):
        ports = {}
        for node in ("com8", "com9"):
            if node in down and s >= down[node][0] and (down[node][1] is None or s < down[node][1]):
                ports[node] = PortMark(False, at(down[node][0]))
            elif node in down and down[node][1] is not None and s >= down[node][1]:
                ports[node] = PortMark(True, at(down[node][1]))
            else:
                ports[node] = PortMark(True, at(0))
        marks.append(Mark(at(s), 1000.0 + s, ports))
    return marks


def make_record(tmp_path, scenario: dict | None = None, extra_lines=(), marks=None, pi_clocks=None, end=END,
                boot_at: float = 1):
    """A record folder as `bench run` leaves it: scenario, manifest and the capture slices of Node 2 on com9."""
    scenario = scenario or {"nodes": {"2": "C2"}, "timeout": "2m"}
    out = tmp_path / "record"
    out.mkdir(parents=True)
    (out / "scenario.toml").write_text(to_toml(scenario), encoding="utf-8")
    boards = {2: BOARDS[2]}
    m = start_manifest(command="bench run x", worktree="main", bench_commit="abc1234", build_id="b1",
                       scenario=from_dict(scenario), boards=boards, others=[], started=T0,
                       flashed={2: at(0)}, host=Host(T0, 10.0, {}))
    finish_manifest(m, ended=end, verdict="PASS every expectation met", exit_code=0, host=Host(end, 50.0, {}),
                    boots={}, notes=[], pi_clocks=pi_clocks or {})
    (out / "manifest.toml").write_text(dumps(m), encoding="utf-8")
    w = DailyWriter(out, "com9")
    boot(w, at(boot_at), UID2, "C2", 2)
    for dt, raw in extra_lines:
        w.write(at(dt), raw)
    w.close()
    if marks is not None:
        (out / "marks-20260929.log").write_text("".join(format_mark(m) + "\n" for m in marks), encoding="utf-8")
    return out


def test_a_clean_record_is_valid_and_gives_the_verdict_the_engine_gives(tmp_path):
    out = make_record(tmp_path, marks=marks_every_10s(40))

    a = assess(out)
    assert (a.code, a.verdict_code, a.causes) == (0, 0, [])


def test_a_port_down_for_12_seconds_makes_the_record_invalid_and_names_the_port_and_the_interval(tmp_path):
    out = make_record(tmp_path, marks=marks_every_10s(40, com9=(12, 24)))

    a = assess(out)
    assert (a.code, a.verdict_code) == (4, 0)  # the trace passed; the data cannot be used
    [cause] = a.causes
    assert (cause.kind, cause.node, cause.start, cause.end) == ("port_down", "com9", at(12), at(24))


def test_the_port_of_a_board_outside_the_run_does_not_invalidate_it(tmp_path):
    out = make_record(tmp_path, marks=marks_every_10s(40, com8=(12, 40)))

    assert assess(out).code == 0


def test_a_record_without_marks_is_not_invalid_but_says_the_capture_could_not_be_checked(tmp_path):
    a = assess(make_record(tmp_path))

    assert a.code == 0 and a.causes == []
    assert any("no marks" in note for note in a.notes)


def test_marks_that_stop_for_5_minutes_in_a_long_run_say_the_host_was_not_running(tmp_path):
    marks = marks_every_10s(40)
    out = make_record(tmp_path, marks=marks[:2] + marks_every_10s(400)[-3:], end=at(400),
                      extra_lines=[(399, "000101T000100.0000 0Y L #01 CLK from=ACQ to=WARM why=lock")])

    assert [c.kind for c in assess(out).causes] == ["no_marks"]


LOST = [(5, "000101T000005.0000 0Y L #01 CLK from=ACQ to=WARM why=lock"),
        (6, "000101T000006.0000 0Y L #05 CLK from=ACQ to=WARM why=lock")]  # three lines lost before #05


def test_lost_lines_fail_an_acceptance_record_and_invalidate_a_dataset_one(tmp_path):
    acceptance = assess(make_record(tmp_path / "a", extra_lines=LOST, marks=marks_every_10s(40)))
    dataset = assess(make_record(tmp_path / "d", {"nodes": {"2": "C2"}, "timeout": "2m", "dataset": True},
                                 extra_lines=LOST, marks=marks_every_10s(40)))

    assert (acceptance.code, [c.kind for c in acceptance.causes]) == (1, ["lost_lines"])
    assert (dataset.code, [c.kind for c in dataset.causes]) == (4, ["lost_lines"])


def test_a_node_silent_longer_than_max_silence_with_its_port_up_fails_a_record_that_passed(tmp_path):
    scenario = {"nodes": {"2": "C2"}, "timeout": "2m", "max_silence": "20s"}

    a = assess(make_record(tmp_path / "up", scenario, marks=marks_every_10s(40)))
    assert (a.code, a.verdict_code, [c.kind for c in a.causes]) == (1, 0, ["node_silent"])
    # The same silence with the port down is the capture's gap, not the node's.
    b = assess(make_record(tmp_path / "down", scenario, marks=marks_every_10s(40, com9=(2, 40))))
    assert (b.code, sorted(c.kind for c in b.causes)) == (4, ["port_down"])
    # Off unless the scenario sets it.
    assert assess(make_record(tmp_path / "off", marks=marks_every_10s(40))).causes == []


def test_a_pi_clock_that_was_not_synchronised_or_lagged_at_either_end_invalidates_the_record(tmp_path):
    ok = ({"pi_ntp": "synced", "clock_lag_s": 0.07}, {"pi_ntp": "synced", "clock_lag_s": 0.1})
    lagging_at_the_end = ({"pi_ntp": "synced", "clock_lag_s": 0.07}, {"pi_ntp": "synced", "clock_lag_s": 42.0})

    assert assess(make_record(tmp_path / "ok", marks=marks_every_10s(40), pi_clocks={"com9": ok})).code == 0
    a = assess(make_record(tmp_path / "lag", marks=marks_every_10s(40), pi_clocks={"com9": lagging_at_the_end}))
    assert (a.code, [(c.kind, c.node) for c in a.causes]) == (4, [("pi_clock", "com9")])


def write_marks(directory, marks) -> None:
    directory.mkdir(parents=True, exist_ok=True)
    (directory / "marks-20260929.log").write_text("".join(format_mark(m) + "\n" for m in marks), encoding="utf-8")


def test_the_marks_of_the_run_window_and_of_its_boards_are_copied_into_the_record(tmp_path):
    write_marks(tmp_path / "cap", marks_every_10s(100))

    slice_marks(tmp_path / "cap", tmp_path / "rec", ["com9"], at(25), at(55))

    from arclog.marks import read_marks
    kept = read_marks(tmp_path / "rec" / "marks-20260929.log")
    assert [m.utc for m in kept] == [at(30), at(40), at(50)]
    assert all(list(m.ports) == ["com9"] for m in kept)


def finish_run(tmp_path, scenario, script=lambda bench: None, marks=None):
    """A run on the simulated bench, concluded the way `bench run` does it: (outcome, record folder)."""
    cap = tmp_path / "cap"
    bench = Bench(cap)
    boot(bench.w["com8"], T0 + timedelta(seconds=1), UID1, "C3", 1)
    boot(bench.w["com9"], T0 + timedelta(seconds=1), UID2, "C2", 2)
    script(bench)
    s = from_dict(scenario)
    outcome, _ = run_it(bench, s)
    write_marks(cap, marks if marks is not None else marks_every_10s(int((outcome.ended - T0).total_seconds()) + 10))
    manifest = start_manifest(command="bench run x", worktree="main", bench_commit="abc1234", build_id="b1",
                              scenario=s, boards=BOARDS, others=[], started=T0, flashed={n: T0 for n in s.flashed},
                              host=Host(T0, 10.0, {}))
    record = tmp_path / "record"
    a = conclude(record, cap, to_toml(scenario), s, "b1", outcome, BOARDS, [], manifest,
                 Host(outcome.ended, 99.0, {}), {})
    return outcome, a, record


class Later:
    """A capture writer whose lines appear when the simulated clock reaches them."""

    def __init__(self, bench: Bench, node: str) -> None:
        self.bench, self.node = bench, node

    def write(self, t: datetime, raw: str) -> None:
        self.bench.line((t - T0).total_seconds(), self.node, raw)


def test_validate_on_a_stored_record_gives_the_verdict_the_end_of_the_run_gave(tmp_path):
    cases = {
        "pass": ({"nodes": {"2": "C2"}, "timeout": "5m"}, lambda bench: None, 0),
        "fail": ({"nodes": {"2": "C2"}, "timeout": "2m"},
                 lambda bench: boot(Later(bench, "com9"), T0 + timedelta(seconds=20), UID2, "C2", 2), 1),
        "timeout": ({"nodes": {"2": "C2"}, "timeout": "2m",
                     "action": [{"reset": 2, "after": {"node": 2, "event": "CLK", "where": {"to": "WARM"}}}]},
                    lambda bench: None, 2),
    }
    for name, (scenario, script, code) in cases.items():
        outcome, concluded, record = finish_run(tmp_path / name, scenario, script)
        assert outcome.code == code == concluded.code == assess(record).code, name
        assert tomllib.loads((record / "manifest.toml").read_text())["run"]["exit_code"] == code, name


def test_an_invalid_run_opens_its_report_with_the_cause_and_the_manifest_keeps_the_validity(tmp_path):
    outcome, a, record = finish_run(tmp_path, {"nodes": {"2": "C2"}, "timeout": "5m"},
                                    marks=marks_every_10s(100, com9=(12, 40)))

    assert (outcome.code, a.code) == (0, 4)
    report = (record / "report.md").read_text(encoding="utf-8")
    assert report.splitlines()[0].endswith(": INVALID (verdict PASS)")
    assert "## Validity" in report and "port_down: com9: port down for" in report
    m = tomllib.loads((record / "manifest.toml").read_text(encoding="utf-8"))
    assert m["validity"]["valid"] is False and m["validity"]["cause"][0]["kind"] == "port_down"
    assert m["run"]["exit_code"] == 4 and m["run"]["verdict"].startswith("PASS")
    assert m["board"][0]["boot"][0].startswith("000000T000000.9997 4S A #00 BOOT") and len(m["board"][0]["boot"]) == 2


def test_a_valid_run_says_so_and_a_record_without_marks_says_what_was_not_checked(tmp_path):
    _, a, record = finish_run(tmp_path, {"nodes": {"2": "C2"}, "timeout": "5m"})
    assert a.code == 0
    assert "- Valid: nothing the bench did makes the data unfit." in (record / "report.md").read_text(encoding="utf-8")

    _, b, record = finish_run(tmp_path / "bare", {"nodes": {"2": "C2"}, "timeout": "5m"}, marks=[])
    assert b.code == 0 and any("no marks" in n for n in b.notes)
    assert "- note: no marks in the record" in (record / "report.md").read_text(encoding="utf-8")


def test_the_exit_codes_of_bench_validate_keep_0_1_2_3_and_4_apart(tmp_path, capsys):
    from bench.cli import main

    cases = {
        0: ({"nodes": {"2": "C2"}, "timeout": "5m"}, lambda bench: None, None),
        1: ({"nodes": {"2": "C2"}, "timeout": "2m"},
            lambda bench: boot(Later(bench, "com9"), T0 + timedelta(seconds=20), UID2, "C2", 2), None),
        2: ({"nodes": {"2": "C2"}, "timeout": "2m",
             "action": [{"reset": 2, "after": {"node": 2, "event": "CLK", "where": {"to": "WARM"}}}]},
            lambda bench: None, None),
        4: ({"nodes": {"2": "C2"}, "timeout": "5m"}, lambda bench: None, marks_every_10s(100, com9=(12, 40))),
    }
    for code, (scenario, script, marks) in cases.items():
        _, _, record = finish_run(tmp_path / str(code), scenario, script, marks)
        assert main(["validate", str(record)]) == code, code
        out = capsys.readouterr().out
        assert f"exit code {code}" in out
    assert ("INVALID" in out) and ("port_down" in out)

    (tmp_path / "bare").mkdir()
    assert main(["validate", str(tmp_path / "bare")]) == 3
    assert "no manifest.toml" in capsys.readouterr().err


def test_bench_validate_says_when_the_exit_code_stored_at_the_end_of_the_run_differs(tmp_path, capsys):
    from bench.cli import main

    _, _, record = finish_run(tmp_path, {"nodes": {"2": "C2"}, "timeout": "5m"})
    assert main(["validate", str(record)]) == 0
    assert "differs" not in capsys.readouterr().out

    manifest = record / "manifest.toml"
    manifest.write_text(manifest.read_text(encoding="utf-8").replace("exit_code = 0", "exit_code = 1", 1),
                        encoding="utf-8")
    assert main(["validate", str(record)]) == 0
    assert "differs from the exit code 1 stored" in capsys.readouterr().out


def test_bench_note_adds_a_timestamped_line_to_the_running_session_and_refuses_without_one(tmp_path, capsys):
    from bench.cli import main
    from bench.manifest import read_notes

    runs = tmp_path / "runs"
    assert main(["note", "too early", "--runs", str(runs)]) == 1
    assert "no running session" in capsys.readouterr().err

    session = runs / "20261007T140655Z-b1"
    session.mkdir(parents=True)
    m = start_manifest(command="bench run x", worktree="main", bench_commit="abc1234", build_id="b1",
                       scenario=from_dict({"nodes": {"2": "C2"}}), boards={2: BOARDS[2]}, others=[],
                       started=datetime.now(timezone.utc), flashed={}, host=Host(T0, 10.0, {}))
    (session / "manifest.toml").write_text(dumps(m), encoding="utf-8")

    assert main(["note", "moved", "Node 2", "to the window", "--runs", str(runs)]) == 0
    assert "20261007T140655Z-b1" in capsys.readouterr().out
    [note] = read_notes(session)
    assert note["text"] == "moved Node 2 to the window"


def test_the_validity_lines_are_plain_text_that_reads_the_same_in_the_report_and_the_terminal(tmp_path):
    from bench.validate import format_validity

    _, a, _ = finish_run(tmp_path, {"nodes": {"2": "C2"}, "timeout": "5m"}, marks=marks_every_10s(100, com9=(12, 40)))
    assert all("**" not in line for line in format_validity(a))
    assert format_validity(a)[0].startswith("- INVALID (exit 4): ")
