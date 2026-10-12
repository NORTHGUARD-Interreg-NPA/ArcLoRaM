"""stream: a record folder read in time order, with memory bounded by a window and not by the run."""

import tracemalloc
from datetime import datetime, timedelta, timezone

from arclog.model import format_host_time
from arclog.stream import StreamStats, merged_lines, node_lines

T0 = datetime(2026, 10, 7, 23, 0, 0, tzinfo=timezone.utc)
WINDOW = timedelta(minutes=10)


def at(seconds: float) -> datetime:
    return T0 + timedelta(seconds=seconds)


def entry(seconds: float, text: str = "") -> str:
    return f"{format_host_time(at(seconds))}\t{text or f'dev line at {seconds:g}'}\n"


def put(directory, node: str, day: str, *lines: str) -> None:
    (directory / f"{node}-{day}.log").write_text("".join(lines), encoding="utf-8")


def times(lines) -> list[float]:
    return [(ln.host_time - T0).total_seconds() for ln in lines]


def test_the_days_of_a_node_are_read_in_order_across_midnight(tmp_path):
    put(tmp_path, "n2", "20261008", entry(3700), entry(3800))
    put(tmp_path, "n2", "20261007", entry(10), entry(3500))

    assert times(node_lines(tmp_path, "n2", at(0), WINDOW)) == [10, 3500, 3700, 3800]


def test_a_line_before_since_a_half_written_line_and_a_line_without_a_host_time_are_skipped(tmp_path):
    put(tmp_path, "n2", "20261007", entry(-5), entry(10), "no host time at all\n", entry(20),
        entry(30).rstrip("\n"))

    assert times(node_lines(tmp_path, "n2", at(0), WINDOW)) == [10, 20]


def test_a_node_name_that_is_the_start_of_another_is_not_mistaken_for_it(tmp_path):
    put(tmp_path, "n2", "20261007", entry(10))
    put(tmp_path, "n2-b", "20261007", entry(20))

    assert times(node_lines(tmp_path, "n2", at(0), WINDOW)) == [10]


def test_a_recovered_line_stored_after_the_lines_that_came_live_is_put_back_in_place(tmp_path):
    # The collector stores a line it recovered after a reconnect after the lines that came live: older Pi time, later place.
    put(tmp_path, "n2", "20261007", entry(10), entry(20), entry(40), entry(50), entry(25, "recovered"), entry(60))
    stats = StreamStats()

    lines = list(node_lines(tmp_path, "n2", at(0), WINDOW, stats))

    assert times(lines) == [10, 20, 25, 40, 50, 60]
    assert lines[2].raw == "recovered"
    assert not stats.late


def test_a_line_older_than_the_window_is_passed_on_where_it_came_and_counted(tmp_path):
    put(tmp_path, "n2", "20261007", entry(10), entry(20), entry(2000), entry(15, "too late"), entry(2010))
    stats = StreamStats()

    lines = list(node_lines(tmp_path, "n2", at(0), WINDOW, stats))

    assert sorted(times(lines)) == [10, 15, 20, 2000, 2010]
    assert [ln.raw for ln in lines if ln.raw == "too late"] == ["too late"]
    assert stats.late["n2"] == 1 and stats.first_late["n2"] == at(15)


def test_the_nodes_are_merged_in_time_order(tmp_path):
    put(tmp_path, "n2", "20261007", entry(10), entry(30), entry(50))
    put(tmp_path, "n5", "20261007", entry(20), entry(40), entry(60))

    lines = list(merged_lines(tmp_path, ["n2", "n5"], at(0), WINDOW))

    assert times(lines) == [10, 20, 30, 40, 50, 60]
    assert [ln.node for ln in lines] == ["n2", "n5", "n2", "n5", "n2", "n5"]


def test_the_memory_does_not_grow_with_the_length_of_the_run(tmp_path):
    def peak(count: int) -> int:
        directory = tmp_path / str(count)
        directory.mkdir()
        put(directory, "n2", "20261007", *(entry(s * 20.0) for s in range(count)))
        tracemalloc.start()
        try:
            for _ in node_lines(directory, "n2", at(0), WINDOW):
                pass
            return tracemalloc.get_traced_memory()[1]
        finally:
            tracemalloc.stop()

    short, long = peak(3_000), peak(24_000)

    assert long < short * 1.5, (short, long)
