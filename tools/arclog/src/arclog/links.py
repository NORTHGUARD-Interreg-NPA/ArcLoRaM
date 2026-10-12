"""Link statistics: per transmitter -> receiver, what was sent, what was heard, at which RSSI and SNR (issue #85).

A C3 never listens, so no link ends at one. A packet is matched to its sender by the protocol key (epoch, phase, cell), as `merge` does (merge.pair), so the
host clocks do not matter. "Sent" counts the transmissions of the sender from the moment the receiver booted
(before that nobody was listening: those packets are not lost, they were never offered); a packet the
receiver missed is a packet sent and not heard. A C2 that is locked listens for the packets it expects, not for
every one the C3 sends: for it "sent" overstates what was offered, and the loss is a figure of a receiver that
listens all the time (cold, scanning, or acquiring).

The radio reports two numbers per packet: RSSI, the power in the channel, and SNR, the signal over the noise.
Below 0 dB SNR the signal is under the noise: the RSSI is then mostly noise, and it is the SNR that says how
close the packet was to being lost. SF12 demodulates down to -20 dB.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Mapping

from arclog.merge import merge, pair, rx_records, tx_records
from arclog.model import Line


@dataclass
class LinkStats:
    tx: str
    rx: str
    sent: int = 0
    heard: int = 0
    rssi: list[int] = field(default_factory=list)
    snr: list[int] = field(default_factory=list)


def _listens(stream: list[Line]) -> bool:
    """A C3 is the anchor: it transmits and never listens, so no link ends at it."""
    boots = [ln for ln in stream if ln.event == "BOOT" and ln.core == "0" and ln.get("cls")]
    return not boots or boots[-1].get("cls") != "C3"


def _up_since(stream: list[Line]) -> "datetime | None":
    """When the node began to listen: its first CM0+ BOOT, else its first line (a trace that starts mid-run)."""
    boot = next((ln.host_time for ln in stream if ln.event == "BOOT" and ln.core == "0" and ln.host_time), None)
    return boot or min((ln.host_time for ln in stream if ln.host_time), default=None)


def link_stats(streams: Mapping[str, list[Line]]) -> list[LinkStats]:
    """Every direction between two nodes that sent or heard a Sync packet, ordered by (tx, rx)."""
    lines = merge(streams.values())
    txs, rxs = tx_records(lines), rx_records(lines)
    matched = pair(txs, rxs)
    first_line = {node: _up_since(stream) for node, stream in streams.items()}
    sent_by = {node: [t for t in txs if t.node == node] for node in streams}
    found: dict[tuple[str, str], LinkStats] = {}

    def link(tx: str, rx: str) -> LinkStats:
        return found.setdefault((tx, rx), LinkStats(tx, rx))

    for tx_node, sent in sent_by.items():
        for rx_node, start in first_line.items():
            if rx_node == tx_node or not sent or not _listens(streams[rx_node]):
                continue
            n = sum(1 for t in sent if start is None or (t.tx.host_time and t.tx.host_time >= start))
            link(tx_node, rx_node).sent = n
    for r in rxs:
        t = matched.get(id(r.rx))
        if t is None:
            continue
        stats = link(t.node, r.node)
        stats.heard += 1
        rssi, snr = (r.radio.int("rssi"), r.radio.int("snr")) if r.radio else (None, None)
        if rssi is not None:
            stats.rssi.append(rssi)
        if snr is not None:
            stats.snr.append(snr)
    return [found[k] for k in sorted(found)]
