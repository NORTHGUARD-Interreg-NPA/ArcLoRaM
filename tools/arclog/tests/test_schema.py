"""Firmware <-> tool contract: every ARCLOG() call in the C sources must match
the schema (event, module, keys) and use only format specifiers the target
formatter supports."""

import re
from pathlib import Path

import pytest

from arclog.model import parse_line
from arclog.schema import (EVENTS, events_from_dict, events_from_source, events_to_dict, validate)

MOD_CHARS = {"TDMA": "T", "MAC": "M", "SYNC": "Y", "RADIO": "R", "MBMUX": "X", "SYS": "S", "POWER": "P"}

CALL_RE = re.compile(
    r'ARCLOG\(\s*ARCLOG_MOD_(?P<mod>\w+)\s*,\s*VLEVEL_\w+\s*,\s*"(?P<event>\w+)"\s*,\s*'
    r'(?P<fmt>(?:"[^"]*"\s*)+)'
)
SOURCE_DIRS = ["CM0PLUS", "CM4", "Common"]


def arclog_calls(root: Path):
    for d in SOURCE_DIRS:
        for path in (root / d).rglob("*.c"):
            if any(part.startswith("Debug") for part in path.parts):
                continue
            text = path.read_text(encoding="utf-8", errors="replace")
            for m in CALL_RE.finditer(text):
                fmt = "".join(re.findall(r'"([^"]*)"', m["fmt"]))
                yield path.relative_to(root), m["mod"], m["event"], fmt


def test_sources_are_found(repo_root):
    calls = list(arclog_calls(repo_root))
    assert len(calls) > 20
    assert {"SYNC_RX", "CLK", "SLOT", "RX_DONE"} <= {c[2] for c in calls}


def test_every_firmware_event_matches_the_schema(repo_root):
    problems = []
    for path, mod, event, fmt in arclog_calls(repo_root):
        ev = EVENTS.get(event)
        if ev is None:
            problems.append(f"{path}: {event} not in schema")
            continue
        if MOD_CHARS[mod] != ev.mod:
            problems.append(f"{path}: {event} module {mod}, schema says {ev.mod}")
        keys = set(re.findall(r"(\w+)=", fmt))
        allowed = set(ev.keys) | set(ev.optional)
        if not set(ev.keys) <= keys or not keys <= allowed:
            problems.append(f"{path}: {event} keys {sorted(keys)}, schema {sorted(allowed)}")
    assert not problems, "\n".join(problems)


# Events in the schema whose firmware call is not written yet: none today.
# Add an event here in the same change as its schema entry when the firmware
# call comes in a later one; the second test fails on a stale entry.
NOT_YET_EMITTED: set[str] = set()


def test_every_schema_event_is_emitted_by_firmware(repo_root):
    emitted = {c[2] for c in arclog_calls(repo_root)}
    assert set(EVENTS) - emitted - NOT_YET_EMITTED == set()


def test_no_stale_not_yet_emitted_entry(repo_root):
    emitted = {c[2] for c in arclog_calls(repo_root)}
    assert NOT_YET_EMITTED <= set(EVENTS)
    assert NOT_YET_EMITTED & emitted == set(), "emitted now: drop it from NOT_YET_EMITTED"


def test_no_length_modifiers(repo_root):
    """tiny_vsnprintf_like (TINY_PRINTF) prints %lu literally and shifts args."""
    bad = [f"{p}: {e} {f}" for p, _, e, f in arclog_calls(repo_root) if re.search(r"%\d*[lhzL]", f)]
    assert not bad, "\n".join(bad)


@pytest.mark.parametrize(
    "line, problem",
    [
        ("260925T101010.0000 0Y M #01 NOPE a=1", "unknown event NOPE"),
        ("260925T101010.0000 0T M #01 CLK from=A to=B why=c", "CLK: module T, expected Y"),
        ("260925T101010.0000 0Y M #01 CLK from=A to=B", "CLK: missing why"),
        ("260925T101010.0000 0Y M #01 CLK from=A to=B why=c x=1", "CLK: unexpected x"),
    ],
)
def test_validate_reports_problems(line, problem):
    assert problem in validate(parse_line(line))


def test_optional_keys_are_accepted():
    assert validate(parse_line("260925T101010.0000 4S A #00 BOOT")) == []
    assert validate(parse_line("260925T101010.0000 0S A #00 BOOT cls=C2 fw=1.5.0")) == []


def test_boot_with_node_id_and_uid_is_valid():
    line = "260925T101010.0000 0S A #00 BOOT cls=C2 id=2 uid=002000415642500a20383354 fw=1.5.0"
    assert validate(parse_line(line)) == []


def test_boot_with_build_id_is_valid():
    assert validate(parse_line("260925T101010.0000 4S A #00 BOOT build=dev")) == []
    line = ("260925T101010.0000 0S A #00 BOOT cls=C2 id=2 uid=002000415642500a20383354 "
            "fw=1.5.0 build=a1b2c3d-dirty-5e6f")
    assert validate(parse_line(line)) == []


def test_calr_and_drift_events_are_valid():
    calr = "260925T101010.0000 0Y M #07 CALR req=-8100 calp=0 calm=8 res=ok cal0=0 trim=-7629"
    assert validate(parse_line(calr)) == []
    drift = "260925T101010.0000 0Y M #08 DRIFT n=27 base=1222 rate=8104 resid=475 noise=361 ok=1 trim=-7629"
    assert validate(parse_line(drift)) == []
    assert "DRIFT: missing ok" in validate(parse_line(drift.replace(" ok=1", "")))


def test_unregistered_board_gives_the_table_entry_to_add():
    line = "260925T101010.0000 0S A #00 BOOT cls=C2 id=0 uid=002000415642500a20383354 fw=1.5.0"
    assert validate(parse_line(line)) == [
        "BOOT: board not registered, add { { 0x00200041u, 0x5642500Au, 0x20383354u }, "
        "<next free id> }, to Common/Protocol/node_id.c"
    ]


# --- the schema of the build that wrote a record ------------------------------------------------------------


def test_the_events_read_from_the_source_of_the_schema_are_the_table_itself():
    from arclog import schema

    read = events_from_source(Path(schema.__file__).read_text(encoding="utf-8"))

    assert {n: (e.mod, e.keys, e.optional) for n, e in read.items()} == \
        {n: (e.mod, e.keys, e.optional) for n, e in EVENTS.items()}


def test_a_source_is_read_and_never_run():
    source = (
        "from arclog.model import Kind\n"
        "EVENTS = {\n"
        '    "BOOT": Event("S", (), 1 / 0, ("cls", "id")),\n'
        '    "RX_WIN": Event("T", ("last", "cap"), "doc", optional=("x",)),\n'
        "}\n"
        "raise SystemExit('run')\n"
    )

    events = events_from_source(source)

    assert (events["BOOT"].mod, events["BOOT"].keys, events["BOOT"].optional) == ("S", (), ("cls", "id"))
    assert (events["RX_WIN"].keys, events["RX_WIN"].optional) == (("last", "cap"), ("x",))


@pytest.mark.parametrize("source", ["x = 1\n", "EVENTS = {'A': make('S')}\n", "EVENTS = {'A': Event()}\n"])
def test_a_source_without_a_table_of_events_is_refused(source):
    with pytest.raises(ValueError):
        events_from_source(source)


def test_a_line_is_checked_with_the_table_of_its_build_when_one_is_given():
    # Since #36 RX_WIN carries g and win; a record of an older build does not, and is not wrong.
    old = events_from_source(
        'EVENTS = {"RX_WIN": Event("T", ("last", "cap"), "")}\n')
    line = parse_line("261007T120000.0000 0T H #01 RX_WIN last=1 cap=2")

    assert validate(line) == ["RX_WIN: missing g,win"]
    assert validate(line, old) == []
    assert validate(line) == ["RX_WIN: missing g,win"]  # the checkout's table is untouched


def test_a_table_survives_being_kept_as_plain_data():
    kept = events_from_dict(events_to_dict(EVENTS))

    assert {n: (e.mod, e.keys, e.optional) for n, e in kept.items()} == \
        {n: (e.mod, e.keys, e.optional) for n, e in EVENTS.items()}
