"""The Links table of a run report: what each link sent and heard, next to what the places say it should give (#85).

arclog.links counts the packets and keeps their RSSI and SNR; this adds the distance of the link (from the
positions), the loss it should have in free space at the run's channel and power, and the margin to the SF12
limit. The expected figures are a ceiling: free space, the power as configured in the firmware, no antenna
gain, no wall. What the measured link adds to it is the loss of the place: walls, trees, the antennas.
"""

from __future__ import annotations

import statistics
from pathlib import Path
from typing import Mapping

from arclog.links import LinkStats, link_stats
from arclog.model import read_lines
from bench.boards import Board, format_uid
from bench.channels import channel_of, load_plan, mhz, power_of
from bench.positions import Positions, RadioModel, link_distance_m, load_positions


def _stats(values: list[int]) -> str:
    """min / median / max, the median to the half dB."""
    med = statistics.median(values)
    return f"{min(values)} / {int(med) if med == int(med) else med} / {max(values)}"


def _signed(value: float) -> str:
    return f"{value:+.1f}".replace(".0", "") if value == int(value) else f"{value:+.1f}"


def links_section(stats: list[LinkStats], uids: Mapping[str, str], positions: Positions,
                  model: RadioModel) -> list[str]:
    """The Markdown lines of the table and its notes; none when no link sent or heard anything."""
    if not stats:
        return []
    rows = [
        "| Link | Heard | RSSI dBm (min / median / max) | SNR dB (min / median / max) | SNR margin to "
        f"{model.snr_limit_db:g} dB | Distance | Expected RSSI | Expected SNR | Loss over free space |",
        "|---|---|---|---|---|---|---|---|---|",
    ]
    for link in stats:
        heard = f"{link.heard} of {link.sent}"
        place_a, place_b = positions.place_of(uids.get(link.tx)), positions.place_of(uids.get(link.rx))
        distance = expected = snr_exp = "-"
        d = None
        if place_a is not None and place_b is not None:
            d = link_distance_m(place_a, place_b)
            if d < 1.0:
                distance = "same place"
            else:
                distance = f"{d:.0f} m"
                expected = f"{model.expected_rssi_dbm(d):.0f} dBm"
                snr_exp = f"{model.expected_snr_db(d):.0f} dB"
        if not link.heard or not link.snr:
            rows.append(f"| {link.tx} -> {link.rx} | {heard} | nothing heard | nothing heard | - | {distance} | "
                        f"{expected} | {snr_exp} | - |")
            continue
        worst, median = min(link.snr) - model.snr_limit_db, statistics.median(link.snr) - model.snr_limit_db
        margin = f"{_signed(worst)} (median {_signed(median)})"
        loss = "-"
        if d is not None and d >= 1.0 and link.rssi:
            rssi, snr = statistics.median(link.rssi), statistics.median(link.snr)
            signal = rssi + snr if snr < 0 else rssi            # below 0 dB the RSSI is mostly noise
            loss = f"~{model.expected_rssi_dbm(d) - signal:.0f} dB"
        rows.append(f"| {link.tx} -> {link.rx} | {heard} | {_stats(link.rssi)} | {_stats(link.snr)} | {margin} | "
                    f"{distance} | {expected} | {snr_exp} | {loss} |")
    rows += [
        "",
        f"- Expected: free space at {mhz(round(model.freq_hz))} and {model.tx_power_dbm:g} dBm as set in the firmware, "
        "no antenna gain and no wall: a ceiling. The last column is what the place costs on top (the signal is "
        "estimated as RSSI + SNR when the SNR is below 0 dB).",
        f"- SF12 demodulates down to {model.snr_limit_db:g} dB SNR. Below 0 dB the signal is under the noise and the "
        "RSSI is mostly noise, so the margin that counts is the SNR margin; the worst packet is the one nearest to being lost.",
        "- Heard of sent: a C2 that is locked listens for the packets it expects, not for every one the C3 sends.",
    ]
    return rows


def run_links(out_dir: Path, boards: Mapping[int, Board], overrides: Mapping[str, str], repo: Path,
              positions: Positions | None = None) -> list[str]:
    """The Links lines of a run folder: its nodes' capture files, the channel and power of its overrides."""
    plan = load_plan(repo)
    channel = channel_of(overrides, plan)
    model = RadioModel(freq_hz=float(channel), tx_power_dbm=float(power_of(overrides, channel, plan)))
    if positions is None:
        try:
            positions = load_positions()
        except ValueError:
            positions = Positions()
    streams, uids = {}, {}
    for b in boards.values():
        if not b.node:
            continue
        files = sorted(out_dir.glob(f"{b.node}-*.log"))
        if files:
            streams[b.node] = [ln for f in files for ln in read_lines(f, node=b.node)]
            if b.uid:
                uids[b.node] = format_uid(b.uid)
    return links_section(link_stats(streams), uids, positions, model)
