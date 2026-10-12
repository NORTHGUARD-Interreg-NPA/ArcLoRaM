"""arclog command line: capture, view, merge, report, expect."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from arclog.model import LEVELS, Line, parse_line, read_lines
from arclog.view import Filter, format_line, render, use_color


def _csv_set(value: str | None, upper: bool = True) -> set[str]:
    if not value:
        return set()
    items = {v.strip() for v in value.split(",") if v.strip()}
    return {v.upper() for v in items} if upper else items


def _filter(args: argparse.Namespace) -> Filter:
    return Filter(
        cores=_csv_set(args.core),
        mods=_csv_set(args.mod),
        max_level=args.level.upper(),
        events=_csv_set(args.event),
        nodes=_csv_set(args.node_filter, upper=False),
        grep=args.grep or "",
        show_legacy=not args.no_legacy,
        show_raw=not args.no_raw,
    )


def _add_filter_args(p: argparse.ArgumentParser) -> None:
    g = p.add_argument_group("filters")
    g.add_argument("--core", help="cores to show: 0 (CM0+), 4 (CM4), e.g. 0,4")
    g.add_argument("--mod", help="modules: T M Y R X S P, e.g. Y,M")
    g.add_argument("--level", default="H", choices=list(LEVELS) + list(LEVELS.lower()),
                   help="most verbose level shown (A < L < M < H), default H")
    g.add_argument("--event", help="events to show, e.g. SYNC_RX,CLK")
    g.add_argument("--only-node", dest="node_filter", help="nodes to show (merge), e.g. c2")
    g.add_argument("--grep", help="only lines containing this text")
    g.add_argument("--no-legacy", action="store_true", help="hide LEGACY (ST free-text) lines")
    g.add_argument("--no-raw", action="store_true", help="hide RAW (unparsed) lines")
    c = p.add_mutually_exclusive_group()
    c.add_argument("--color", dest="color", action="store_true", default=None)
    c.add_argument("--no-color", dest="color", action="store_false")


def cmd_capture(args: argparse.Namespace) -> int:
    from arclog.capture import capture

    if len(args.port) != len(args.node):
        print(f"arclog capture: {len(args.port)} --port but {len(args.node)} --node; "
              "give one --node per --port, in the same order", file=sys.stderr)
        return 2
    ports = list(zip(args.port, args.node))
    flt = _filter(args)
    color = use_color(sys.stdout, args.color)
    show_node = len(ports) > 1

    def echo(line: Line) -> None:
        if not args.quiet and flt.accepts(line):
            print(format_line(line, color=color, show_node=show_node), flush=True)

    for port, node in ports:
        print(f"arclog: {port} -> {args.out}/{node}-YYYYMMDD.log", file=sys.stderr)
    print("arclog: Ctrl+C to stop", file=sys.stderr)
    try:
        capture(ports, Path(args.out), baud=args.baud, on_line=echo, duration_s=args.duration)
    except ValueError as exc:
        print(f"arclog capture: {exc}", file=sys.stderr)
        return 2
    return 0


def cmd_view(args: argparse.Namespace) -> int:
    flt = _filter(args)
    if args.port:
        from arclog.capture import serial_lines

        lines = (parse_line(raw, node=args.name, host_time=t)
                 for t, raw in serial_lines(args.port, args.baud))
        try:
            render(lines, flt, color=args.color, show_node=False)
        except KeyboardInterrupt:
            pass
        return 0
    if not args.files:
        print("arclog view: give capture files or --port", file=sys.stderr)
        return 2
    for f in args.files:
        render(read_lines(f), flt, color=args.color, show_node=len(args.files) > 1)
    return 0


def _streams(files: list[str]) -> dict[str, list[Line]]:
    """Group capture files by node (daily files of one node are concatenated)."""
    streams: dict[str, list[Line]] = {}
    for f in sorted(files):
        lines = list(read_lines(f))
        node = lines[0].node if lines else Path(f).stem
        streams.setdefault(node, []).extend(lines)
    return streams


def cmd_merge(args: argparse.Namespace) -> int:
    from arclog.merge import merge, pair_notes
    from arclog.view import annotate, format_placed

    streams = _streams(args.files)
    placed = None
    if args.by == "device":
        from arclog.devicetime import align

        try:
            placed = align(streams, ref=args.ref)
        except ValueError as exc:
            print(f"arclog merge: {exc}", file=sys.stderr)
            return 2
        merged = [p.line for p in placed]
    else:
        merged = merge(streams.values())
    notes = pair_notes(merged)
    flt = _filter(args)
    color = use_color(sys.stdout, args.color)
    # Health notes are computed per node stream, then shown on the timeline.
    health = {}
    for stream in streams.values():
        for line, note in annotate(stream):
            if note:
                health[id(line)] = note
    for i, line in enumerate(merged):
        if flt.accepts(line):
            note = " ".join(n for n in (health.get(id(line)), notes.get(id(line))) if n)
            print(format_placed(placed[i], color=color, note=note) if placed else format_line(line, color=color, note=note))
    return 0


def cmd_report(args: argparse.Namespace) -> int:
    from arclog.report import analyse, to_csv, to_markdown

    streams = _streams(args.files)
    run = analyse(streams, sf=args.sf, bw_hz=args.bw, preamble=args.preamble)
    aligned = None
    try:
        from arclog.devicetime import align

        aligned = {id(p.line): p for p in align(streams)}
    except ValueError:
        pass    # no C3 in the capture: there is no reference axis, and the CSV has no aligned columns
    md = to_markdown(run, title=args.title)
    if args.out:
        out = Path(args.out)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(md, encoding="utf-8", newline="\n")
        csv_path = out.with_suffix(".csv")
        csv_path.write_text(to_csv(run, aligned), encoding="utf-8", newline="\n")
        print(f"arclog: wrote {out} and {csv_path}", file=sys.stderr)
    else:
        sys.stdout.write(md)
    return 0


def cmd_expect(args: argparse.Namespace) -> int:
    import time
    from datetime import datetime, timezone

    from arclog.expect import TIMEOUT, DirFollower, Run, describe_start, load_spec, parse_since, replay

    try:
        spec = load_spec(args.spec)
        since = parse_since(args.since)
    except (OSError, ValueError) as exc:
        print(f"arclog expect: {exc}", file=sys.stderr)
        return 3

    def report(msg: str) -> None:
        print(msg, flush=True)

    report(describe_start(spec, since))
    run = Run(spec, since, report=report)
    follower = DirFollower(args.dir, list(spec.nodes), since)
    if not args.follow:
        verdict = replay(run, follower.poll())
        if verdict is None:
            report("TIMEOUT trace ends before a verdict")
            return TIMEOUT
        return verdict.code
    try:
        while True:
            replay(run, follower.poll())
            verdict = run.tick(datetime.now(timezone.utc))
            if verdict is not None:
                return verdict.code
            time.sleep(args.poll)
    except KeyboardInterrupt:
        report("arclog expect: interrupted")
        return 130


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="arclog", description=__doc__)
    sub = p.add_subparsers(dest="cmd", required=True)

    c = sub.add_parser("capture", help="record serial ports to daily capture files, one node per port")
    c.add_argument("--port", required=True, action="append",
                   help="serial port, e.g. /dev/ttyUSB0 or COM5, or a Pi Node's log server, "
                        "tcp://host:4000/name; repeat for several ports")
    c.add_argument("--node", required=True, action="append",
                   help="node name used in file names, e.g. c3; one per --port, same order")
    c.add_argument("--out", default="runs", help="output directory (default: runs)")
    c.add_argument("--baud", type=int, default=9600)
    c.add_argument("--quiet", action="store_true", help="do not echo lines to the terminal")
    c.add_argument("--duration", type=float, help="stop after this many seconds (default: until Ctrl+C)")
    _add_filter_args(c)
    c.set_defaults(func=cmd_capture)

    v = sub.add_parser("view", help="show capture files or a live port, classified and filtered")
    v.add_argument("files", nargs="*")
    v.add_argument("--port", help="read live from a serial port instead of files")
    v.add_argument("--name", default="", help="node name for --port")
    v.add_argument("--baud", type=int, default=9600)
    _add_filter_args(v)
    v.set_defaults(func=cmd_view)

    m = sub.add_parser("merge", help="interleave several nodes on one timeline, pairing Sync packets")
    m.add_argument("files", nargs="+")
    m.add_argument("--by", choices=["host", "device"], default="host",
                   help="order by the host's receive time (default) or by the C3's device time: a node is on it from "
                        "its CLK to=WARM on, its other lines are placed by host time and marked ~")
    m.add_argument("--ref", help="the C3 node name for --by device (default: the first node that booted as a C3)")
    _add_filter_args(m)
    m.set_defaults(func=cmd_merge)

    r = sub.add_parser("report", help="sync run report (Markdown + CSV)")
    r.add_argument("files", nargs="+")
    r.add_argument("-o", "--out", help="report path (.md); the CSV is written next to it")
    r.add_argument("--title", default="Sync run report")
    r.add_argument("--sf", type=int, default=12)
    r.add_argument("--bw", type=int, default=125_000, help="bandwidth in Hz")
    r.add_argument("--preamble", type=int, default=8, help="preamble symbols")
    r.set_defaults(func=cmd_report)

    e = sub.add_parser("expect", help="decide a run from capture files: exit 0 pass, 1 fail, 2 timeout "
                                      "(3 invalid expect file)")
    e.add_argument("spec", help="expect file (TOML), see arclog/expect.py")
    e.add_argument("--dir", required=True, help="capture directory (<node>-YYYYMMDD.log files)")
    e.add_argument("--since", required=True,
                   help="run start, ISO UTC time (e.g. 2026-09-28T23:24:00Z) or 'now'")
    e.add_argument("--follow", action="store_true",
                   help="keep reading as the files grow, until a verdict (default: decide on the files as they are)")
    e.add_argument("--poll", type=float, default=1.0, help="seconds between reads with --follow")
    e.set_defaults(func=cmd_expect)
    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        return args.func(args)
    except BrokenPipeError:
        # Output piped into a pager or `head` that closed early: not an error.
        sys.stderr.close()
        return 0
