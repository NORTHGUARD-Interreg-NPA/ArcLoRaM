"""Link statistics: for each transmitter -> receiver pair, what was sent, what was heard, at which RSSI and SNR."""

from datetime import datetime, timedelta, timezone
from pathlib import Path

from arclog.links import LinkStats, link_stats
from arclog.model import parse_line, read_lines

DATA = Path(__file__).parent / "data" / "c3-c2-20261004"
T0 = datetime(2026, 10, 12, 10, 0, 0, tzinfo=timezone.utc)


def streams_from_fixture():
    return {"c3": list(read_lines(DATA / "c3-20261004.log", node="c3")),
            "c2": list(read_lines(DATA / "c2-20261004.log", node="c2"))}


def test_the_c3_to_c2_link_of_the_recorded_capture():
    [link] = link_stats(streams_from_fixture())
    assert (link.tx, link.rx) == ("c3", "c2")
    assert (link.sent, link.heard) == (16, 12)       # the trimmed capture: a locked C2 listens for the packets it expects
    assert min(link.rssi) >= -15 and max(link.rssi) <= -12            # boards side by side
    assert 5 <= min(link.snr) and max(link.snr) <= 9


def test_packets_sent_before_the_receiver_was_up_are_not_counted_as_lost():
    streams = streams_from_fixture()
    [link] = link_stats(streams)
    c2_first = min(ln.host_time for ln in streams["c2"] if ln.host_time)
    c3_sent_all = sum(1 for ln in streams["c3"] if ln.event == "SYNC_TX" and ln.core == "0")
    c3_sent_after = sum(1 for ln in streams["c3"] if ln.event == "SYNC_TX" and ln.core == "0" and ln.host_time >= c2_first)
    assert link.sent == c3_sent_after < c3_sent_all


def line(node, host_s, event, core="0", mod="Y", **fields):
    body = "".join(f" {k}={v}" for k, v in fields.items())
    return parse_line(f"000102T100000.0000 {core}{mod} M #00 {event}{body}", node=node,
                      host_time=T0 + timedelta(seconds=host_s))


def tx(node, host_s, ce):
    return [line(node, host_s, "SYNC_TX", ph=0, ce=ce, ep=100, plan=1, send=1, freq=867300000),
            line(node, host_s + 1, "TX_DONE", mod="R", sz=10, toa=992, end=2, start=1)]


def rx(node, host_s, ce, rssi, snr):
    return [line(node, host_s, "RX_DONE", mod="R", sz=10, rssi=rssi, snr=snr, pre=1, hdr=2, rxd=3, toa=992, st=4),
            line(node, host_s + 0.1, "SYNC_RX", ph=0, ce=ce, ep=100, st=4, exp=4, err=0, clk="ACQ", act="good")]


def test_a_packet_that_is_not_heard_is_sent_and_not_heard():
    streams = {
        "a": [line("a", 0, "BOOT", mod="S", cls="C3"), *tx("a", 5, 1), *tx("a", 25, 2), *tx("a", 45, 3)],
        "b": [line("b", 1, "BOOT", mod="S", cls="C2"), *rx("b", 6, 1, -104, -18), *rx("b", 46, 3, -105, -19)],
    }
    [link] = link_stats(streams)
    assert (link.sent, link.heard) == (3, 2)
    assert link.rssi == [-104, -105] and link.snr == [-18, -19]


def test_a_link_with_nothing_heard_is_still_a_link():
    streams = {"a": [line("a", 0, "BOOT", mod="S", cls="C3"), *tx("a", 5, 1)],
               "b": [line("b", 0, "BOOT", mod="S", cls="C2")]}
    [link] = link_stats(streams)
    assert (link.tx, link.rx, link.sent, link.heard, link.rssi, link.snr) == ("a", "b", 1, 0, [], [])


def test_each_direction_is_its_own_link():
    streams = {
        "a": [line("a", 0, "BOOT", mod="S", cls="C2"), *tx("a", 5, 1), *rx("a", 26, 2, -90, 3)],
        "b": [line("b", 0, "BOOT", mod="S", cls="C2"), *rx("b", 6, 1, -91, 4), *tx("b", 25, 2)],
    }
    found = {(l.tx, l.rx): (l.sent, l.heard) for l in link_stats(streams)}
    assert found == {("a", "b"): (1, 1), ("b", "a"): (1, 1)}


def test_no_link_ends_at_a_c3_which_never_listens():
    streams = {"a": [line("a", 0, "BOOT", mod="S", cls="C3"), *tx("a", 5, 1)],
               "b": [line("b", 0, "BOOT", mod="S", cls="C2"), *rx("b", 6, 1, -100, -10), *tx("b", 25, 2)]}
    assert [(l.tx, l.rx) for l in link_stats(streams)] == [("a", "b")]
