"""A record of days: 10 nodes over 72 hours is pulled and judged in a bounded time, with memory bounded by the
expectations and not by the run (#111)."""

import subprocess
import sys
import time
from datetime import datetime, timedelta, timezone

import pytest
from fake_collector import PARIS, FakeCollector, soak

from bench.pull import load_state, pull

UTC = timezone.utc
SINCE = datetime(2026, 10, 1, 8, 0, 0, tzinfo=UTC)
BUILD = "e34092f-o954ffd"
NODES = 10
HOURS = 72

# The bounds the record of the issue is held to. Measured on 2026-10-08 (WSL2 on a laptop) the 520450 lines were
# pulled in 19 s and judged in 16 s at 27 MB, the same 27 MB as for 18 h of the same nodes; the bounds are a few
# times that, so a slow machine does not fail them and a judge that grows with the run does.
PULL_SECONDS = 120
JUDGE_SECONDS = 90
JUDGE_MEGABYTES = 100


def scenario_toml() -> str:
    """The soak's staging, for every node: at least 150 SLOT an hour, asked each hour of the 72."""
    out = [f'description = "scale"\ntimeout = "{HOURS * 60 + 30}m"\n\n[nodes]']
    out += [f'{i} = "C2"' for i in range(1, NODES + 1)]
    for i in range(1, NODES + 1):
        for hour in range(1, HOURS + 1):
            out.append(f'\n[[expect]]\nnode = {i}\nevent = "SLOT"\ncount = {150 * hour}\nwithin = "{65 + 60 * (hour - 1)}m"')
    out.append('\n[[forbid]]\nevent = "TX_LATE"')
    return "\n".join(out) + "\n"


# About a minute: left out of the default run (`pytest -m scale` runs it; run it after a change to the pull, the
# judge or the engine's per-line cost).
pytestmark = [pytest.mark.scale,
              pytest.mark.skipif(not sys.platform.startswith("linux"), reason="reads the peak memory from /proc")]


@pytest.fixture(scope="module")
def record(tmp_path_factory):
    names = [f"nuna-node-{i:02d}" for i in range(1, NODES + 1)]
    fake = FakeCollector(names, cap=100000)
    for i, name in enumerate(names, start=1):
        fake.add(name, SINCE - timedelta(hours=2), "[collector] connected", source="collector")
        fake.add_all(name, soak(SINCE, HOURS, BUILD, node_id=i))
    base = fake.start()
    folder = tmp_path_factory.mktemp("scale") / "record"
    scenario = tmp_path_factory.mktemp("scenario") / "scale.toml"
    scenario.write_text(scenario_toml())
    began = time.monotonic()
    state = pull(base, names, BUILD, SINCE, folder, PARIS, chunk=20000, schema_source=lambda c: None)
    pull_seconds = time.monotonic() - began
    lines = sum(n.lines for n in state["nodes"].values())
    yield {"folder": folder, "scenario": scenario, "names": names, "lines": lines, "pull_seconds": pull_seconds,
           "requests": len(fake.requests)}
    fake.stop()


def judge_in_a_process(record, *extra: str) -> tuple[int, list[str], float, int]:
    """Judge the folder in its own process: (exit code, what it printed, seconds, peak memory in MB)."""
    program = ("import sys\nfrom bench.collector import main\ncode = main(sys.argv[1:])\n"
               "peak = [ln for ln in open('/proc/self/status') if ln.startswith('VmHWM')][0].split()[1]\n"
               "print('PEAK', int(peak) // 1024)\nsys.exit(code)\n")
    nodes = [a for i, name in enumerate(record["names"], start=1) for a in ("--node", f"{i}={name}")]
    began = time.monotonic()
    done = subprocess.run([sys.executable, "-I", "-c", program, str(record["scenario"]), "--build", BUILD, "--since",
                           SINCE.strftime("%Y-%m-%dT%H:%M:%SZ"), *nodes, "--dir", str(record["folder"]), "--no-pull",
                           *extra], capture_output=True, text=True)
    seconds = time.monotonic() - began
    out = done.stdout.splitlines()
    peak = int(out.pop().split()[1])
    return done.returncode, out, seconds, peak


def test_the_record_of_ten_nodes_over_72_hours_is_pulled_in_a_stated_time(record):
    assert record["lines"] > 500_000, record["lines"]
    assert record["pull_seconds"] < PULL_SECONDS, record
    print(f"\nPULL {record['lines']} lines of {NODES} nodes in {record['pull_seconds']:.0f} s, {record['requests']} requests")
    state = load_state(record["folder"])
    assert all(n.complete_to and not n.problem for n in state["nodes"].values())


def test_it_is_judged_in_a_stated_time_and_memory_with_a_summary_no_longer_than_the_18_hour_soak(record):
    code, out, seconds, peak = judge_in_a_process(record)

    print(f"\nJUDGE exit {code} in {seconds:.0f} s, peak {peak} MB, {len(out)} lines printed")
    assert code == 0, out
    assert seconds < JUDGE_SECONDS and peak < JUDGE_MEGABYTES, (seconds, peak)
    assert len(out) <= 30, out
    log = (record["folder"] / "judge.log").read_text().splitlines()
    assert len(log) > 700, "the per-stage detail is in the report file"


def test_its_memory_follows_the_expectations_and_not_the_length_of_the_run(record):
    _, _, _, full = judge_in_a_process(record)
    until = (SINCE + timedelta(hours=18)).strftime("%Y-%m-%dT%H:%M:%SZ")
    _, _, _, eighteen = judge_in_a_process(record, "--until", until)

    print(f"\nMEMORY 18 h {eighteen} MB, 72 h {full} MB")
    assert full < eighteen * 1.4, (eighteen, full)
