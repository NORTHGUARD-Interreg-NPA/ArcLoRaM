"""validity: can the data of a capture session be used? The marks say what the node files cannot."""

from datetime import datetime, timedelta, timezone

from arclog.marks import Mark, PortMark
from arclog.validity import Cause, check_marks, exit_code, invalidating

T0 = datetime(2026, 10, 7, 14, 0, 0, tzinfo=timezone.utc)


def at(seconds: float) -> datetime:
    return T0 + timedelta(seconds=seconds)


def mark(seconds: float, c3=(True, 0), c2=(True, 0), step: float = 0.0) -> Mark:
    """The mark written `seconds` after the start. A port is (up, seconds of its last change); `step` is
    how far the host's UTC was stepped before this mark, which the monotonic reading does not follow."""
    return Mark(at(seconds + step), 1000.0 + seconds,
                {"c3": PortMark(c3[0], at(c3[1])), "c2": PortMark(c2[0], at(c2[1]))})


def every_10s(until: int) -> list[Mark]:
    return [mark(s) for s in range(0, until + 1, 10)]


def causes(marks, start=0, end=None):
    end = marks[-1].utc if end is None else at(end)
    return [(c.kind, c.node, c.start, c.end) for c in check_marks(marks, at(start), end)]


def test_a_session_with_every_mark_and_every_port_up_is_valid():
    assert causes(every_10s(60)) == []


def test_a_port_down_for_12_seconds_is_a_capture_gap_naming_the_port_and_the_interval():
    marks = [mark(0), mark(10), mark(20, c3=(False, 12)), mark(30, c3=(False, 12)),
             mark(40, c3=(True, 24)), mark(50, c3=(True, 24))]

    assert causes(marks) == [("port_down", "c3", at(12), at(24))]
    [cause] = check_marks(marks, at(0), at(50))
    assert "c3" in cause.text and "12 s" in cause.text


def test_a_port_down_for_8_seconds_is_not_a_gap():
    marks = [mark(0), mark(10), mark(20, c3=(False, 13)), mark(30, c3=(True, 21)), mark(40, c3=(True, 21))]

    assert causes(marks) == []


def test_a_port_that_never_came_up_is_down_from_the_start_to_the_last_mark():
    marks = [mark(s, c2=(False, 0)) for s in range(0, 61, 10)]

    assert causes(marks) == [("port_down", "c2", at(0), at(60))]


def test_a_utc_step_of_5_seconds_between_two_marks_names_its_size():
    marks = [mark(0), mark(10), mark(20), mark(30, step=5), mark(40, step=5)]

    assert causes(marks) == [("utc_step", None, at(20), at(35))]
    [cause] = check_marks(marks, at(0), at(45))
    assert "+5.0 s" in cause.text


def test_a_backward_step_is_signed():
    marks = [mark(0), mark(10), mark(20, step=-3)]

    [cause] = check_marks(marks, at(0), at(17))
    assert "-3.0 s" in cause.text


def test_no_marks_for_5_minutes_is_a_host_that_was_not_running():
    marks = [mark(0), mark(10), mark(20), mark(320), mark(330)]

    assert causes(marks) == [("no_marks", None, at(20), at(320))]
    [cause] = check_marks(marks, at(0), at(330))
    assert "300 s" in cause.text


def test_30_seconds_between_marks_is_not_yet_a_gap():
    assert causes([mark(0), mark(10), mark(40), mark(50)]) == []


def test_a_gap_over_a_host_sleep_is_not_also_a_utc_step():
    # The monotonic clock of a suspended host may not count the sleep: UTC alone jumps.
    asleep = Mark(at(320), 1020.0, mark(20).ports)
    assert causes([mark(0), mark(10), mark(20), asleep]) == [("no_marks", None, at(20), at(320))]


def test_a_window_with_no_marks_at_all_is_a_capture_that_was_not_running():
    assert [(c.kind, c.start, c.end) for c in check_marks([], at(0), at(300))] == [("no_marks", at(0), at(300))]


def test_marks_that_begin_late_or_end_early_leave_the_edges_of_the_window_uncovered():
    marks = [mark(60), mark(70), mark(80)]

    assert causes(marks, start=0, end=80) == [("no_marks", None, at(0), at(60))]
    assert causes(marks, start=60, end=200) == [("no_marks", None, at(80), at(200))]


def test_an_outage_before_the_window_is_not_the_window_s():
    marks = [mark(0), mark(10, c3=(False, 5)), mark(20, c3=(False, 5)), mark(30, c3=(True, 30))]
    marks += [mark(s) for s in range(40, 101, 10)]

    assert causes(marks, start=40, end=100) == []


def test_an_outage_that_began_before_the_window_counts_from_its_start():
    marks = [mark(30, c3=(False, 5)), mark(40, c3=(False, 5)), mark(50, c3=(False, 5)),
             mark(60, c3=(True, 52)), mark(70, c3=(True, 52))]

    assert causes(marks, start=40, end=70) == [("port_down", "c3", at(40), at(52))]


def test_every_cause_the_marks_show_is_a_bench_fault():
    marks = [mark(0), mark(10, c3=(False, 5)), mark(20, c3=(False, 5)), mark(30, c3=(True, 25)),
             mark(40, step=5), mark(400)]

    faults = {c.kind: c.fault for c in check_marks(marks, at(0), at(400))}
    assert faults == {"port_down": "bench", "utc_step": "bench", "no_marks": "bench"}


def test_bench_faults_always_invalidate_firmware_faults_only_in_a_dataset_and_node_faults_never():
    bench = Cause("port_down", "c3: port down", fault="bench")
    firmware = Cause("lost_lines", "c3 3 line(s) lost", fault="firmware")
    node = Cause("node_silent", "c3 silent", fault="node")

    assert invalidating([bench, firmware, node], dataset=False) == [bench]
    assert invalidating([bench, firmware, node], dataset=True) == [bench, firmware]
    assert invalidating([firmware, node], dataset=False) == []


def test_the_exit_code_of_a_run_keeps_pass_fail_and_timeout_apart_from_invalid():
    bench = Cause("port_down", "c3: port down", fault="bench")
    firmware = Cause("lost_lines", "c3 3 line(s) lost", fault="firmware")
    node = Cause("node_silent", "c3 silent", fault="node")
    PASS, FAIL, TIMEOUT = 0, 1, 2

    assert exit_code(PASS, [], dataset=False) == 0
    assert exit_code(TIMEOUT, [], dataset=False) == 2
    assert exit_code(PASS, [bench], dataset=False) == 4       # an invalid run has no verdict, whatever the engine said
    assert exit_code(FAIL, [bench], dataset=False) == 4
    assert exit_code(FAIL, [firmware], dataset=False) == 1    # an acceptance run fails on a firmware event
    assert exit_code(PASS, [firmware], dataset=True) == 4
    assert exit_code(PASS, [node], dataset=True) == 1         # a stopped node is a verdict on the firmware
    assert exit_code(TIMEOUT, [node], dataset=False) == 1
