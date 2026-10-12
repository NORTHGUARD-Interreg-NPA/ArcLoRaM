"""Sync report: the evidence for a C3 -> C2 synchronisation run (issue #21).

Inputs are the capture files of every node of the run. Output is a Markdown
report plus a CSV with one row per received Sync packet.

Definitions (all times in ms unless stated):
  err         SYNC_RX err = SyncStamp - expected arrival, receiver clock.
              The SyncStamp is RxDone - ToA: the packet start on air.
  drift       Slope of err over time while CLOCK_WARM, fitted separately
              within each segment without an RTC correction (RTC_SET,
              RTC_SHIFT and ClockState changes start a new segment), pooled
              into one slope. Reported in ppm (1 ppm = 1 ms per 1000 s).
  pre - st    PREAMBLE_DETECTED minus SyncStamp: when the preamble is
              detected after the packet start. Diagnostic only; its spread is
              the preamble-detection jitter (one symbol at SF12).
  hdr - pre   Delay between PREAMBLE_DETECTED and HEADER_VALID IRQs. The
              header is valid a fixed number of symbols after the TX start,
              so (theoretical header time - mean(hdr - pre)) is a second
              estimate of the preamble-detection delay.
  st - start  Receiver SyncStamp minus sender radio TX start for the same
              packet (matched by ep, ph, ce): residual clock offset plus
              detection latency. Only meaningful once the receiver is WARM.
"""

from __future__ import annotations

import csv
import io
import math
import statistics
from collections import Counter
from dataclasses import dataclass, field
from datetime import datetime

from arclog.health import Health, check
from arclog.merge import RxRecord, TxRecord, pair, rx_records, tx_records
from arclog.model import Kind, Line


# --------------------------------------------------------------------------
# Numbers
# --------------------------------------------------------------------------

@dataclass
class Stats:
    n: int = 0
    mean: float = math.nan
    stdev: float = math.nan
    min: float = math.nan
    max: float = math.nan
    p95_abs: float = math.nan

    @classmethod
    def of(cls, values: list[float]) -> "Stats":
        if not values:
            return cls()
        ordered = sorted(abs(v) for v in values)
        p95 = ordered[min(len(ordered) - 1, math.ceil(0.95 * len(ordered)) - 1)]
        return cls(
            n=len(values),
            mean=statistics.fmean(values),
            stdev=statistics.stdev(values) if len(values) > 1 else 0.0,
            min=min(values),
            max=max(values),
            p95_abs=p95,
        )

    def row(self) -> str:
        if self.n == 0:
            return "| 0 | - | - | - | - | - |"
        return (f"| {self.n} | {self.mean:.2f} | {self.stdev:.2f} | {self.min:.0f} "
                f"| {self.max:.0f} | {self.p95_abs:.0f} |")


STATS_HEADER = "| n | mean | stdev | min | max | p95 abs |\n|---|---|---|---|---|---|"


@dataclass
class DriftFit:
    ppm: float = math.nan
    points: int = 0
    segments: int = 0
    span_s: float = 0.0


def pooled_slope(segments: list[list[tuple[float, float]]]) -> DriftFit:
    """Least-squares slope of y over x with one intercept per segment."""
    num = den = 0.0
    points = used = 0
    span = 0.0
    for seg in segments:
        if len(seg) < 2:
            continue
        xs, ys = [p[0] for p in seg], [p[1] for p in seg]
        mx, my = statistics.fmean(xs), statistics.fmean(ys)
        num += sum((x - mx) * (y - my) for x, y in seg)
        den += sum((x - mx) ** 2 for x in xs)
        points += len(seg)
        used += 1
        span += xs[-1] - xs[0]
    if den == 0:
        return DriftFit(points=points, segments=used, span_s=span)
    slope_ms_per_s = num / den
    return DriftFit(ppm=slope_ms_per_s * 1000.0, points=points, segments=used, span_s=span)


def lora_symbol_ms(sf: int, bw_hz: int) -> float:
    return (2 ** sf) / bw_hz * 1000.0


def header_valid_ms(sf: int, bw_hz: int, preamble: int) -> float:
    """Nominal HEADER_VALID time after TX start: preamble + 4.25 sync symbols
    + 8 header symbols (explicit header)."""
    return (preamble + 4.25 + 8) * lora_symbol_ms(sf, bw_hz)


# --------------------------------------------------------------------------
# Per-node analysis
# --------------------------------------------------------------------------

@dataclass
class Acquisition:
    start: Line
    packets: int = 1
    end: Line | None = None
    locked: bool = False

    @property
    def seconds(self) -> float | None:
        if self.end is None or self.start.time is None or self.end.time is None:
            return None
        return (self.end.time - self.start.time).total_seconds()


@dataclass
class NodeResult:
    node: str
    health: Health
    acquisitions: list[Acquisition] = field(default_factory=list)
    losses: Counter = field(default_factory=Counter)
    acts: Counter = field(default_factory=Counter)
    warm_err: Stats = field(default_factory=Stats)
    warm_t1_err: Stats = field(default_factory=Stats)
    drift: DriftFit = field(default_factory=DriftFit)
    pre_minus_st: Stats = field(default_factory=Stats)
    hdr_minus_pre: Stats = field(default_factory=Stats)
    rtc_sets: int = 0
    rtc_shifts: int = 0
    suspects: int = 0
    rx: list[RxRecord] = field(default_factory=list)
    tx: list[TxRecord] = field(default_factory=list)
    tx_latency: Stats = field(default_factory=Stats)   # radio start - plan
    tx_latency_by_cell: dict[int, Stats] = field(default_factory=dict)
    send_latency: Stats = field(default_factory=Stats)  # send - plan
    tx_ramp: Stats = field(default_factory=Stats)       # radio start - send
    tx_lates: int = 0


def _seconds(t: datetime) -> float:
    return t.timestamp()


def analyse_node(node: str, lines: list[Line]) -> NodeResult:
    res = NodeResult(node=node, health=check(lines))
    cm0 = [ln for ln in lines if ln.kind is Kind.ARCLOG and ln.core == "0"]

    acq: Acquisition | None = None
    segments: list[list[tuple[float, float]]] = [[]]
    warm_errs: list[float] = []
    t1_errs: list[float] = []

    for ln in cm0:
        ev = ln.event
        if ev == "SYNC_RX":
            act = ln.get("act", "?")
            res.acts[act] += 1
            if act == "set":
                if acq is not None and not acq.locked:
                    res.acquisitions.append(acq)
                acq = Acquisition(start=ln)
            elif acq is not None and not acq.locked:
                acq.packets += 1
            err = ln.int("err")
            if ln.get("clk") == "WARM" and err is not None:
                warm_errs.append(err)
                if act == "t1":
                    t1_errs.append(err)
                if ln.time is not None:
                    segments[-1].append((_seconds(ln.time), float(err)))
        elif ev == "CLK":
            segments.append([])
            if ln.get("to") == "WARM" and acq is not None:
                acq.end, acq.locked = ln, True
                res.acquisitions.append(acq)
                acq = None
            elif ln.get("to") == "COLD":
                res.losses[ln.get("why", "?")] += 1
                if acq is not None:
                    acq.end = ln
                    res.acquisitions.append(acq)
                    acq = None
        elif ev in ("RTC_SET", "RTC_SHIFT"):
            segments.append([])
            if ev == "RTC_SET":
                res.rtc_sets += 1
            else:
                res.rtc_shifts += 1
        elif ev == "SLOT_SUSPECT":
            res.suspects += 1
        elif ev == "TX_LATE":
            res.tx_lates += 1
    if acq is not None:
        res.acquisitions.append(acq)

    res.warm_err = Stats.of(warm_errs)
    res.warm_t1_err = Stats.of(t1_errs)
    res.drift = pooled_slope(segments)

    res.rx = rx_records(lines)
    res.tx = tx_records(lines)
    deltas, pre_lags = [], []
    for r in res.rx:
        if r.radio is None:
            continue
        pre, hdr, st = r.field("pre"), r.field("hdr"), r.field("st")
        if pre and hdr:
            deltas.append(hdr - pre)
        if pre and st is not None:
            pre_lags.append(pre - st)
    res.hdr_minus_pre = Stats.of(deltas)
    res.pre_minus_st = Stats.of(pre_lags)
    res.tx_latency = Stats.of([t.start - t.plan for t in res.tx
                               if t.start is not None and t.plan is not None])
    res.send_latency = Stats.of([t.tx.int("send") - t.plan for t in res.tx
                                 if t.tx.int("send") is not None and t.plan is not None])
    res.tx_ramp = Stats.of([t.start - t.tx.int("send") for t in res.tx
                            if t.start is not None and t.tx.int("send") is not None])
    by_cell: dict[int, list[float]] = {}
    for t in res.tx:
        ce = t.tx.int("ce")
        if t.start is not None and t.plan is not None and ce is not None:
            by_cell.setdefault(ce, []).append(t.start - t.plan)
    res.tx_latency_by_cell = {ce: Stats.of(v) for ce, v in sorted(by_cell.items())}
    return res


# --------------------------------------------------------------------------
# Run report
# --------------------------------------------------------------------------

@dataclass
class RunResult:
    nodes: list[NodeResult]
    pairs: dict[int, TxRecord]
    pair_offset: Stats
    matched: int
    unmatched: int
    sf: int
    bw_hz: int
    preamble: int


def analyse(streams: dict[str, list[Line]], sf: int = 12, bw_hz: int = 125_000,
            preamble: int = 8) -> RunResult:
    nodes = [analyse_node(n, ls) for n, ls in streams.items()]
    all_tx = [t for nr in nodes for t in nr.tx]
    all_rx = [r for nr in nodes for r in nr.rx]
    pairs = pair(all_tx, all_rx)
    offsets = []
    for r in all_rx:
        t = pairs.get(id(r.rx))
        st = r.rx.int("st")
        if t is not None and t.start is not None and st is not None and r.rx.get("clk") == "WARM":
            offsets.append(st - t.start)
    return RunResult(
        nodes=nodes,
        pairs=pairs,
        pair_offset=Stats.of(offsets),
        matched=sum(1 for r in all_rx if id(r.rx) in pairs),
        unmatched=sum(1 for r in all_rx if id(r.rx) not in pairs),
        sf=sf,
        bw_hz=bw_hz,
        preamble=preamble,
    )


def _fmt(v: float, spec: str = ".2f") -> str:
    return "-" if v is None or (isinstance(v, float) and math.isnan(v)) else format(v, spec)


def to_markdown(run: RunResult, title: str = "Sync run report") -> str:
    hdr_theory = header_valid_ms(run.sf, run.bw_hz, run.preamble)
    out = io.StringIO()
    w = out.write
    w(f"# {title}\n\n")
    w(f"Radio: SF{run.sf}, BW {run.bw_hz / 1000:g} kHz, {run.preamble}-symbol preamble "
      f"(symbol {lora_symbol_ms(run.sf, run.bw_hz):.3f} ms, "
      f"nominal HEADER_VALID at {hdr_theory:.1f} ms after TX start).\n\n")

    w("## Trace health\n\n")
    w("| node | id | lines | ArcLog | legacy | raw | lost lines | reordered | schema problems "
      "| RTC jumps | boots |\n")
    w("|---|---|---|---|---|---|---|---|---|---|---|\n")
    for nr in run.nodes:
        h = nr.health
        ids = ",".join(sorted(h.node_ids, key=lambda v: (len(v), v))) or "-"
        w(f"| {nr.node} | {ids} | {h.lines} | {h.kinds[Kind.ARCLOG]} | {h.kinds[Kind.LEGACY]} "
          f"| {h.kinds[Kind.RAW]} | {sum(h.lost.values())} | {h.reordered} "
          f"| {sum(h.problems.values())} | {len(h.rtc_jumps)} | {sum(h.boots.values())} |\n")
    problems = Counter()
    for nr in run.nodes:
        problems.update(nr.health.problems)
    if problems:
        w("\nSchema problems:\n\n")
        for p, n in problems.most_common():
            w(f"- {p} ({n})\n")
    w("\n")

    for nr in run.nodes:
        if not nr.acts and not nr.tx:
            continue
        w(f"## Node {nr.node}\n\n")
        if nr.tx:
            w(f"Sync packets sent: {len(nr.tx)}. TX_LATE: {nr.tx_lates}.\n\n")
            w("Radio TX start minus nominal slot start (ms), 0 in every cell when "
              "the Tx start is deterministic (ADR-0016):\n\n")
            w(STATS_HEADER + "\n" + nr.tx_latency.row() + "\n\n")
            w("| ce " + STATS_HEADER.replace("\n", "\n|---"))
            for ce, st in nr.tx_latency_by_cell.items():
                w(f"\n| {ce} " + st.row())
            w("\n\n")
            w("Tx ramp, radio TX start minus Radio.Send (ms), the platform's TX_RAMP_MS:\n\n")
            w(STATS_HEADER + "\n" + nr.tx_ramp.row() + "\n\n")
        if not nr.acts:
            continue
        locked = [a for a in nr.acquisitions if a.locked]
        w(f"Sync packets received: {sum(nr.acts.values())}. "
          f"Acquisitions: {len(nr.acquisitions)} ({len(locked)} reached CLOCK_WARM).\n\n")
        if nr.acquisitions:
            w("| # | packets to WARM | seconds | outcome |\n|---|---|---|---|\n")
            for i, a in enumerate(nr.acquisitions, 1):
                outcome = "WARM" if a.locked else (f"lost ({a.end.get('why')})" if a.end else "open")
                w(f"| {i} | {a.packets} | {_fmt(a.seconds, '.1f')} | {outcome} |\n")
            w("\n")
        lost = ", ".join(f"{k}: {v}" for k, v in nr.losses.most_common()) or "none"
        w(f"Drops to CLOCK_COLD: {sum(nr.losses.values())} ({lost}). "
          f"RTC_SET: {nr.rtc_sets}. RTC_SHIFT: {nr.rtc_shifts}. SLOT_SUSPECT: {nr.suspects}.\n\n")
        acts = " ".join(f"{k}={nr.acts[k]}" for k in ("set", "good", "bad", "t1", "t2", "t3") if nr.acts[k])
        w(f"Decisions: {acts}.\n\n")
        w("Error while CLOCK_WARM, err = stamp - expected (ms):\n\n")
        w("| packets | n | mean | stdev | min | max | p95 abs |\n|---|---|---|---|---|---|---|\n")
        w("| all tiers " + nr.warm_err.row() + "\n")
        w("| tier 1 " + nr.warm_t1_err.row() + "\n\n")
        d = nr.drift
        w(f"Drift: **{_fmt(d.ppm)} ppm** from {d.points} points in {d.segments} "
          f"correction-free segments spanning {d.span_s / 3600:.2f} h.\n\n")
        if nr.pre_minus_st.n:
            w("PREAMBLE_DETECTED minus SyncStamp (ms, diagnostic):\n\n")
            w(STATS_HEADER + "\n" + nr.pre_minus_st.row() + "\n\n")
        if nr.hdr_minus_pre.n:
            est = hdr_theory - nr.hdr_minus_pre.mean
            w("HEADER_VALID minus PREAMBLE_DETECTED (ms):\n\n")
            w(STATS_HEADER + "\n" + nr.hdr_minus_pre.row() + "\n\n")
            w(f"Estimated preamble-detection delay: **{est:.1f} ms** after TX start "
              f"(diagnostic; the SyncStamp does not depend on it).\n\n")

    w("## Cross-node pairing\n\n")
    w(f"Received Sync packets matched to a sender by (ep, ph, ce): {run.matched}; "
      f"unmatched: {run.unmatched}.\n\n")
    w("Receiver stamp minus sender radio TX start, receiver WARM (ms):\n\n")
    w(STATS_HEADER + "\n" + run.pair_offset.row() + "\n")
    return out.getvalue()


CSV_COLUMNS = [
    "node", "host_time", "dev_time", "ph", "ce", "ep", "st", "exp", "err", "clk", "act",
    "pre", "hdr", "rxd", "toa", "rssi", "snr", "tx_node", "tx_plan", "tx_start", "st_minus_start",
]


ALIGNED_COLUMNS = ["dev_aligned", "dev_basis"]


def to_csv(run: RunResult, aligned: dict | None = None) -> str:
    """One row per received Sync packet. With `aligned` (id of a line -> Placed, from devicetime.align) the rows end
    in the packet's time on the C3's device axis and its basis (`device` or `host`)."""
    out = io.StringIO()
    wr = csv.writer(out, lineterminator="\n")
    wr.writerow(CSV_COLUMNS + (ALIGNED_COLUMNS if aligned is not None else []))
    for nr in run.nodes:
        for r in nr.rx:
            ln = r.rx
            t = run.pairs.get(id(ln))
            st = ln.int("st")
            start = t.start if t else None
            wr.writerow([
                nr.node,
                ln.host_time.isoformat() if ln.host_time else "",
                ln.dev_time.isoformat() if ln.dev_time else "",
                *(ln.get(k, "") for k in ("ph", "ce", "ep", "st", "exp", "err", "clk", "act")),
                *((r.radio.get(k, "") if r.radio else "") for k in
                  ("pre", "hdr", "rxd", "toa", "rssi", "snr")),
                t.node if t else "",
                t.plan if t and t.plan is not None else "",
                start if start is not None else "",
                st - start if st is not None and start is not None else "",
                *([aligned[id(ln)].time.isoformat() if id(ln) in aligned and aligned[id(ln)].time else "",
                   aligned[id(ln)].basis if id(ln) in aligned else ""] if aligned is not None else []),
            ])
    return out.getvalue()
