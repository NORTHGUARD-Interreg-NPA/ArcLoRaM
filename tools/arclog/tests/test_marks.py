"""marks: the capture's sidecar, which says when each port was up and whether the host was running."""

from datetime import datetime, timedelta, timezone

from arclog.marks import Marks, format_mark, read_marks

T0 = datetime(2026, 10, 7, 14, 0, 0, tzinfo=timezone.utc)


class FakeClock:
    """The host's UTC and monotonic clocks, moved by hand; `step` moves UTC alone, as a time service does."""

    def __init__(self, utc: datetime = T0, mono: float = 1000.0) -> None:
        self.utc = utc
        self.mono = mono

    def now(self) -> datetime:
        return self.utc

    def monotonic(self) -> float:
        return self.mono

    def advance(self, seconds: float) -> None:
        self.utc += timedelta(seconds=seconds)
        self.mono += seconds

    def step(self, seconds: float) -> None:
        self.utc += timedelta(seconds=seconds)


def marks_for(directory, clock, nodes=("c3", "c2")):
    return Marks(directory, list(nodes), now=clock.now, monotonic=clock.monotonic)


def state(mark):
    return {node: (port.up, port.since) for node, port in mark.ports.items()}


def test_a_port_that_never_came_up_is_down_since_the_start(tmp_path):
    clock = FakeClock()
    marks = marks_for(tmp_path, clock)
    marks.tick()
    marks.close()

    [mark] = read_marks(tmp_path / "marks-20261007.log")
    assert (mark.utc, mark.mono) == (T0, 1000.0)
    assert state(mark) == {"c3": (False, T0), "c2": (False, T0)}


def test_a_mark_is_written_every_10_seconds(tmp_path):
    clock = FakeClock()
    marks = marks_for(tmp_path, clock)
    for _ in range(60):  # the capture's loop wakes every 0.5 s, for 30 s
        marks.tick()
        clock.advance(0.5)
    marks.close()

    times = [m.utc for m in read_marks(tmp_path / "marks-20261007.log")]
    assert times == [T0, T0 + timedelta(seconds=10), T0 + timedelta(seconds=20)]


def test_a_port_transition_is_recorded_with_its_time(tmp_path):
    clock = FakeClock()
    marks = marks_for(tmp_path, clock)
    marks.tick()
    clock.advance(3)
    marks.port_up("c3")
    clock.advance(7)
    marks.tick()
    clock.advance(5)
    marks.port_down("c3")
    clock.advance(5)
    marks.tick()
    marks.close()

    at = lambda s: T0 + timedelta(seconds=s)
    first, second, third = read_marks(tmp_path / "marks-20261007.log")
    assert state(first) == {"c3": (False, at(0)), "c2": (False, at(0))}
    assert state(second) == {"c3": (True, at(3)), "c2": (False, at(0))}
    assert state(third) == {"c3": (False, at(15)), "c2": (False, at(0))}


def test_repeating_a_state_keeps_the_time_it_began(tmp_path):
    clock = FakeClock()
    marks = marks_for(tmp_path, clock)
    marks.port_up("c3")
    clock.advance(4)
    marks.port_up("c3")    # a reader may report a state at every retry or read
    marks.port_down("c2")  # c2 never came up: down since the start
    clock.advance(4)
    marks.port_down("c2")
    marks.tick()
    marks.close()

    [mark] = read_marks(tmp_path / "marks-20261007.log")
    assert state(mark) == {"c3": (True, T0), "c2": (False, T0)}


def test_marks_follow_the_monotonic_clock_when_utc_is_stepped(tmp_path):
    clock = FakeClock()
    marks = marks_for(tmp_path, clock)
    marks.tick()
    clock.advance(10)
    marks.tick()
    clock.step(-8)  # the time service corrects the host's UTC
    clock.advance(10)
    marks.tick()
    marks.close()

    [a, b, c] = read_marks(tmp_path / "marks-20261007.log")
    assert [(m.utc - T0).total_seconds() for m in (a, b, c)] == [0, 10, 12]
    assert [m.mono for m in (a, b, c)] == [1000.0, 1010.0, 1020.0]


def test_a_restart_appends_and_leaves_no_mark_for_the_time_it_was_stopped(tmp_path):
    clock = FakeClock()
    first = marks_for(tmp_path, clock)
    first.port_up("c3")
    first.tick()
    first.close()
    clock.advance(300)  # the process is not running
    second = marks_for(tmp_path, clock)
    second.tick()
    second.close()

    before, after = read_marks(tmp_path / "marks-20261007.log")
    assert (after.utc - before.utc).total_seconds() == 300
    assert state(before)["c3"] == (True, T0)
    assert state(after)["c3"] == (False, T0 + timedelta(seconds=300))  # down until its reader reports


def test_marks_rotate_at_utc_midnight(tmp_path):
    clock = FakeClock(utc=datetime(2026, 10, 7, 23, 59, 55, tzinfo=timezone.utc))
    marks = marks_for(tmp_path, clock)
    marks.tick()
    clock.advance(10)
    marks.tick()
    marks.close()

    [before] = read_marks(tmp_path / "marks-20261007.log")
    [after] = read_marks(tmp_path / "marks-20261008.log")
    assert (before.utc.day, after.utc.day) == (7, 8)


def test_a_mark_torn_by_a_killed_capture_is_not_read(tmp_path):
    path = tmp_path / "marks-20261007.log"
    complete = "2026-10-07T14:00:00.000000Z\tmono=1000.000\tc3=up@2026-10-07T14:00:00.000000Z\n"
    path.write_text(complete + "2026-10-07T14:00:10.000000Z\tmono=10", encoding="utf-8")

    assert [m.mono for m in read_marks(path)] == [1000.0]


def test_a_mark_formats_as_the_writer_wrote_it(tmp_path):
    clock = FakeClock()
    marks = marks_for(tmp_path, clock)
    marks.port_up("c3")
    marks.tick()
    clock.advance(10)
    marks.port_down("c3")
    marks.tick()
    marks.close()

    path = tmp_path / "marks-20261007.log"
    assert [format_mark(m) for m in read_marks(path)] == path.read_text(encoding="utf-8").splitlines()
