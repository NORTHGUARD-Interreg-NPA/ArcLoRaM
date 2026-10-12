"""arclog merge --by device: the lines of all nodes on the C3's device time axis (issue #85).

Controller time is the host's receive time of a line: it follows the 9600-baud transmission and the trace queue.
Device time is the RTC reading the node logged with the event. For a node that is locked to the C3 (from its
CLK to=WARM on) the RTC is the C3's, within milliseconds, so the lines of all nodes can be put in order on it.
"""

from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from arclog.devicetime import SANITY_S, Placed, align, reference_node
from arclog.merge import pair, rx_records, tx_records
from arclog.model import Kind, parse_line, read_lines

DATA = Path(__file__).parent / "data" / "c3-c2-20261004"
T0 = datetime(2026, 10, 12, 10, 0, 0, tzinfo=timezone.utc)


def streams_from_fixture():
    return {"c3": list(read_lines(DATA / "c3-20261004.log", node="c3")),
            "c2": list(read_lines(DATA / "c2-20261004.log", node="c2"))}


# --- the recorded capture: the eight packets of 2026-10-04 -------------------------------------------------------

def packets(placed):
    """(TX_DONE of the C3, RX_DONE of the C2) of every Sync packet, matched by (ep, ph, ce)."""
    lines = [p.line for p in placed]
    by_id = {id(p.line): p for p in placed}
    txs, rxs = tx_records(lines), rx_records(lines)
    matched = pair(txs, rxs)
    out = []
    for r in rxs:
        t = matched.get(id(r.rx))
        if t is not None and t.done is not None and r.radio is not None:
            out.append((by_id[id(t.done)], by_id[id(r.radio)]))
    return out


def test_the_reference_is_the_node_that_booted_as_c3():
    assert reference_node(streams_from_fixture()) == "c3"


def test_locked_packets_are_within_5_ms_on_device_time_where_host_time_gives_69_to_216_ms():
    placed = align(streams_from_fixture())
    locked = [(tx, rx) for tx, rx in packets(placed) if rx.basis == "device" and tx.basis == "device"]
    assert len(locked) >= 8
    gaps = [abs((rx.time - tx.time).total_seconds()) * 1000 for tx, rx in locked]
    assert max(gaps) < 5.0, gaps
    host = [abs((rx.line.host_time - tx.line.host_time).total_seconds()) * 1000 for tx, rx in locked]
    assert max(host) > 200 and sorted(host)[len(host) // 2] > 60       # the controller clock, for comparison


def test_the_eight_packets_of_the_capture_are_all_locked_and_in_device_order():
    placed = align(streams_from_fixture())
    eight = [(tx, rx) for tx, rx in packets(placed)
             if datetime(2026, 10, 4, 20, 26, 0, tzinfo=timezone.utc) <= rx.line.host_time
             <= datetime(2026, 10, 4, 20, 38, 0, tzinfo=timezone.utc)]
    assert len(eight) == 8
    order = {id(p.line): i for i, p in enumerate(placed)}
    for tx, rx in eight:
        assert rx.basis == tx.basis == "device"
        assert 0 <= order[id(rx.line)] - order[id(tx.line)] <= 3        # the C2 hears it right after the C3 sent it


def test_lines_before_the_lock_are_placed_by_controller_time_and_marked():
    placed = align(streams_from_fixture())
    c2 = [p for p in placed if p.line.node == "c2"]
    lock = next(p for p in c2 if p.line.event == "CLK" and p.line.get("to") == "WARM")
    before = [p for p in c2 if p.line.host_time < lock.line.host_time]
    assert len(before) >= 6 and all(p.basis == "host" for p in before)
    assert lock.basis == "device" and all(p.basis == "device" for p in c2 if p.line.host_time >= lock.line.host_time)


def test_the_c3_is_on_its_own_axis_the_whole_boot_even_across_its_first_rtc_set():
    placed = align(streams_from_fixture())
    c3 = [p for p in placed if p.line.node == "c3" and p.line.host_time >= datetime(2026, 10, 4, 20, 20, 21, tzinfo=timezone.utc)]
    # Every line with a valid device time is on the axis; the CM4's first BOOT line (year 0) has none.
    assert all(p.basis == "device" for p in c3 if p.line.dev_time is not None)
    assert [p.line.raw[:35] for p in c3 if p.basis == "host"] == ["000000T000000.9997 4S A #00 BOOT bu"]
    boot = next(p for p in c3 if p.line.event == "BOOT" and p.line.core == "0")
    rtc = next(p for p in c3 if p.line.event == "RTC_SET")
    # BOOT was logged at 00:00:00 by the clock before the set, which then jumped to 23:55:00: bridged, it reads 23:55:00.
    assert abs((rtc.time - boot.time).total_seconds()) < 0.5


def test_the_merged_timeline_is_ordered():
    placed = align(streams_from_fixture())
    keys = [(p.segment, p.time) for p in placed]
    assert keys == sorted(keys)


# --- synthetic streams: jumps, reboots, reference reboots -----------------------------------------------------------

def line(node, host_s, dev, event, core="0", mod="Y", **fields):
    body = "".join(f" {k}={v}" for k, v in fields.items())
    text = f"{dev} {core}{mod} M #00 {event}{body}"
    return parse_line(text, node=node, host_time=T0 + timedelta(seconds=host_s))


def c3_stream(extra=()):
    return [line("c3", 0.1, "000102T100000.0000", "BOOT", mod="S", cls="C3", id=1, build="b1"),
            line("c3", 0.2, "000102T100000.0300", "MAC_INIT", mod="M", cls="C3", st="ACTIVE", clk="WARM"),
            *extra]


def c2_booted_and_locked(lock_dev="000102T100010.0000"):
    return [line("c2", 5.0, "000101T000000.0000", "BOOT", mod="S", cls="C2", id=2, build="b1"),
            line("c2", 6.0, "000101T000001.0000", "MAC_INIT", mod="M", cls="C2", st="SCAN", clk="COLD"),
            line("c2", 10.1, lock_dev, "CLK", **{"from": "ACQ", "to": "WARM", "why": "lock"})]


def by_event(placed, node, event, nth=0):
    return [p for p in placed if p.line.node == node and p.line.event == event][nth]


def test_a_jump_at_rtc_set_is_bridged_by_its_d():
    # c2 locked at 10:00:10; its clock was 120 ms behind the C3's. At 10:00:20 (c2 time) it is set forward by 120 ms.
    c2 = c2_booted_and_locked() + [
        line("c2", 15.0, "000102T100015.0000", "SLOT", mod="T", ph=0),
        line("c2", 20.2, "000102T100020.1200", "RTC_SET", old=20000, new=20120, d=120, date="000102", shift="ok"),
        line("c2", 25.0, "000102T100025.0000", "SLOT", mod="T", ph=0),
    ]
    placed = align({"c3": c3_stream(), "c2": c2})
    before = by_event(placed, "c2", "SLOT", 0)
    after = by_event(placed, "c2", "SLOT", 1)
    assert before.basis == after.basis == "device"
    assert before.time == datetime(2000, 1, 2, 10, 0, 15, 120000)       # 120 ms added: the clock it was set to
    assert after.time == datetime(2000, 1, 2, 10, 0, 25, 0)


def test_two_jumps_add_up_for_the_lines_before_both():
    c2 = c2_booted_and_locked() + [
        line("c2", 15.0, "000102T100015.0000", "SLOT", mod="T", ph=0),
        line("c2", 20.2, "000102T100020.0500", "RTC_SET", old=20000, new=20050, d=50, date="000102", shift="ok"),
        line("c2", 30.2, "000102T100030.0300", "RTC_SET", old=30000, new=30030, d=-20, date="000102", shift="ok"),
    ]
    placed = align({"c3": c3_stream(), "c2": c2})
    assert by_event(placed, "c2", "SLOT", 0).time == datetime(2000, 1, 2, 10, 0, 15, 30000)   # +50 -20


def test_a_jump_that_changes_the_day_is_bridged_across_the_midnight_wrap():
    # d is reported modulo a day: the C3 here booted at 00:00:00 and was set to 23:55:00 (d = -300035 ms, the jump is
    # +86099968 ms: 23:55:00.000 - 00:00:00.032). The pre-jump line, stamped 3 ms before the set, reads 23:54:59.997.
    c3 = [line("c3", 0.1, "000101T000000.0034", "BOOT", mod="S", cls="C3", id=1, build="b1"),
          line("c3", 0.2, "000101T000000.0290", "MAC_INIT", mod="M", cls="C3", st="ACTIVE", clk="WARM"),
          line("c3", 0.3, "000101T235500.0009", "RTC_SET", old=32, new=86100000, d=-300032, date="000101", shift="ok")]
    placed = align({"c3": c3})
    assert by_event(placed, "c3", "MAC_INIT").time == datetime(2000, 1, 1, 23, 54, 59, 997000)


def test_a_reboot_ends_the_locked_interval():
    c2 = c2_booted_and_locked() + [
        line("c2", 15.0, "000102T100015.0000", "SLOT", mod="T", ph=0),
        line("c2", 20.0, "000101T000000.0000", "BOOT", mod="S", cls="C2", id=2, build="b1"),
        line("c2", 21.0, "000101T000001.0000", "MAC_INIT", mod="M", cls="C2", st="SCAN", clk="COLD"),
        line("c2", 22.0, "000101T000002.0000", "SLOT", mod="T", ph=0),
    ]
    placed = align({"c3": c3_stream(), "c2": c2})
    assert by_event(placed, "c2", "SLOT", 0).basis == "device"
    assert by_event(placed, "c2", "BOOT", 1).basis == "host"
    assert by_event(placed, "c2", "SLOT", 1).basis == "host"


def test_leaving_warm_ends_the_locked_interval():
    c2 = c2_booted_and_locked() + [
        line("c2", 15.0, "000102T100015.0000", "SLOT", mod="T", ph=0),
        line("c2", 16.0, "000102T100016.0000", "CLK", **{"from": "WARM", "to": "COLD", "why": "silence"}),
        line("c2", 17.0, "000102T100017.0000", "SLOT", mod="T", ph=0),
    ]
    placed = align({"c3": c3_stream(), "c2": c2})
    assert by_event(placed, "c2", "CLK", 1).basis == "device"           # the transition itself is stamped by the lock
    assert by_event(placed, "c2", "SLOT", 1).basis == "host"


def test_a_reboot_of_the_c3_starts_a_new_axis_and_ends_the_locked_interval_of_the_c2():
    c3 = c3_stream([
        line("c3", 30.0, "000101T000000.0000", "BOOT", mod="S", cls="C3", id=1, build="b1"),     # the axis restarts
        line("c3", 30.1, "000101T000000.0300", "MAC_INIT", mod="M", cls="C3", st="ACTIVE", clk="WARM"),
        line("c3", 31.0, "000101T000001.0000", "SLOT", mod="T", ph=0)])
    c2 = c2_booted_and_locked() + [
        line("c2", 15.0, "000102T100015.0000", "SLOT", mod="T", ph=0),
        line("c2", 35.0, "000102T100035.0000", "SLOT", mod="T", ph=0)]    # after the C3 rebooted: not on its new axis
    placed = align({"c3": c3, "c2": c2})
    first, second = by_event(placed, "c2", "SLOT", 0), by_event(placed, "c2", "SLOT", 1)
    assert (first.basis, second.basis) == ("device", "host")
    assert first.segment == 0 and second.segment == 1
    # the C3's second boot sorts after everything of the first, though its device times are earlier
    order = [p.line for p in placed]
    assert order.index(by_event(placed, "c3", "SLOT", 0).line) > order.index(first.line)
    assert order.index(second.line) > order.index(by_event(placed, "c3", "BOOT", 1).line)


def test_a_locked_line_far_from_controller_time_is_not_trusted():
    # A clock that is not the C3's (a missed reboot, a wrong date): more than SANITY_S away from where the controller
    # saw the line. It is placed by controller time and marked, not thrown across the timeline.
    c2 = c2_booted_and_locked() + [line("c2", 15.0, "000102T103015.0000", "SLOT", mod="T", ph=0)]
    placed = align({"c3": c3_stream(), "c2": c2})
    assert SANITY_S >= 5 and by_event(placed, "c2", "SLOT").basis == "host"


def test_a_line_without_a_time_stays_next_to_its_neighbour():
    c2 = c2_booted_and_locked() + [
        line("c2", 15.0, "000102T100015.0000", "SLOT", mod="T", ph=0),
        parse_line("garbage from a reset", node="c2", host_time=T0 + timedelta(seconds=15.5)),
        line("c2", 16.0, "000102T100016.0000", "SLOT", mod="T", ph=0)]
    placed = align({"c3": c3_stream(), "c2": c2})
    raw = next(p for p in placed if p.line.kind is Kind.RAW)
    i = placed.index(raw)
    assert placed[i - 1].line.event == "SLOT" and placed[i + 1].line.event == "SLOT"


def test_no_c3_in_the_capture_asks_for_a_reference():
    with pytest.raises(ValueError, match="--ref"):
        align({"c2": c2_booted_and_locked()})
    placed = align({"c2": c2_booted_and_locked(), "x": c3_stream()}, ref="x")
    assert isinstance(placed[0], Placed)


def test_an_unknown_reference_is_refused():
    with pytest.raises(ValueError, match="nope"):
        align({"c3": c3_stream()}, ref="nope")
