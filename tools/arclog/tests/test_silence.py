"""silence_causes: a node that stopped logging while its port was up is a node fault, not a capture gap."""

from datetime import timedelta

from test_expect import SINCE, Trace

from arclog.validity import silence_causes


def at(seconds: float):
    return SINCE + timedelta(seconds=seconds)


def lines_at(*seconds: float, node: str = "n2") -> list:
    t = Trace()
    for s in seconds:
        t.idle(s, node)
    return t.lines


def silences(lines, end: float, max_silence: float = 60, outages=(), nodes=("n2",)):
    found = silence_causes(lines, list(nodes), timedelta(seconds=max_silence), at(0), at(end), outages)
    return [(c.kind, c.fault, c.node, c.start, c.end) for c in found]


def test_a_node_silent_for_5_minutes_with_its_port_up_is_a_node_fault_naming_the_interval():
    lines = lines_at(10, 20, 320, 330)

    assert silences(lines, end=330) == [("node_silent", "node", "n2", at(20), at(320))]
    [cause] = silence_causes(lines, ["n2"], timedelta(seconds=60), at(0), at(330))
    assert "n2" in cause.text and "300 s" in cause.text


def test_a_gap_within_the_limit_is_not_silence():
    assert silences(lines_at(10, 20, 70, 80), end=80) == []


def test_a_gap_while_the_port_was_down_is_a_capture_gap_not_the_node_s_silence():
    lines = lines_at(10, 20, 320, 330)

    assert silences(lines, end=330, outages=[("n2", at(100), at(130))]) == []
    # An outage of another node does not excuse this one.
    assert len(silences(lines, end=330, outages=[("n3", at(100), at(130))])) == 1


def test_a_node_that_stopped_is_silent_from_its_last_line_to_the_end_of_the_window():
    assert silences(lines_at(10, 20), end=400) == [("node_silent", "node", "n2", at(20), at(400))]


def test_a_node_that_never_logged_is_silent_for_the_whole_window():
    assert silences([], end=300) == [("node_silent", "node", "n2", at(0), at(300))]
