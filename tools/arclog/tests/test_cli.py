"""End-to-end: the commands a user runs on capture files."""

from datetime import datetime, timezone

from arclog.capture import DailyWriter
from arclog.cli import main
from arclog.model import read_lines


def test_report_writes_markdown_and_csv(run_files, tmp_path, capsys):
    out = tmp_path / "report" / "run1.md"
    assert main(["report", *map(str, run_files), "-o", str(out), "--title", "Bench run 1"]) == 0
    assert out.read_text(encoding="utf-8").startswith("# Bench run 1")
    assert out.with_suffix(".csv").read_text(encoding="utf-8").startswith("node,")


def test_view_filters_by_event(run_files, capsys):
    c3_path, c2_path = run_files
    assert main(["view", str(c2_path), "--event", "CLK", "--no-color"]) == 0
    out = capsys.readouterr().out.strip().split("\n")
    assert len(out) == 2
    assert all(" CLK " in ln for ln in out)
    assert "to=WARM" in out[1]


def test_view_level_filter_hides_more_verbose_lines(run_files, capsys):
    _, c2_path = run_files
    main(["view", str(c2_path), "--level", "L", "--no-color"])
    out = capsys.readouterr().out
    assert " SYNC_RX " not in out  # M
    assert " CLK " in out           # L


def test_view_reports_lost_lines_even_when_hidden(tmp_path, capsys):
    f = tmp_path / "c2-20260925.log"
    f.write_text(
        "260925T120000.0000 0T H #01 SLOT ph=0 ty=SYNC ce=0 sl=0 pos=CELL dec=RX wake=0 nom=0\n"
        "260925T120003.0000 0T H #05 SLOT ph=0 ty=SYNC ce=1 sl=0 pos=CELL dec=RX wake=0 nom=3000\n"
        "260925T120003.1000 0Y L #06 CLK from=WARM to=COLD why=tier3\n",
        encoding="utf-8",
    )
    main(["view", str(f), "--event", "CLK", "--no-color"])
    out = capsys.readouterr().out
    assert "3 line(s) lost" in out and "hidden SLOT" in out


def test_merge_pairs_sync_packets(run_files, capsys):
    assert main(["merge", *map(str, run_files), "--event", "SYNC_RX", "--no-color"]) == 0
    out = capsys.readouterr().out.strip().split("\n")
    assert out and all("<- c3 tx#" in ln for ln in out)
    assert all(ln.startswith("c2") for ln in out)


def test_daily_writer_rotates_at_utc_midnight(tmp_path):
    w = DailyWriter(tmp_path, "c2")
    w.write(datetime(2026, 9, 25, 23, 59, 59, tzinfo=timezone.utc), "260925T235959.0000 0T H #01 SLOT")
    w.write(datetime(2026, 9, 26, 0, 0, 1, tzinfo=timezone.utc), "260926T000001.0000 0T H #02 SLOT")
    w.close()
    files = sorted(p.name for p in tmp_path.iterdir())
    assert files == ["c2-20260925.log", "c2-20260926.log"]
    (line,) = list(read_lines(tmp_path / "c2-20260926.log"))
    assert line.host_time == datetime(2026, 9, 26, 0, 0, 1, tzinfo=timezone.utc)
    assert line.seq == 2


# --- merge --by device, and the device-aligned time in the report (issue #85) ---------------------------------------

FIXTURE = "tests/data/c3-c2-20261004"


def fixture_files():
    from pathlib import Path

    d = Path(__file__).parent / "data" / "c3-c2-20261004"
    return [str(d / "c3-20261004.log"), str(d / "c2-20261004.log")]


def test_merge_by_host_stays_the_default(capsys):
    assert main(["merge", *fixture_files(), "--event", "TX_DONE,RX_DONE", "--no-color"]) == 0
    assert " ~ " not in capsys.readouterr().out.splitlines()[0]


def test_merge_by_device_puts_a_packet_s_tx_and_rx_next_to_each_other(capsys):
    assert main(["merge", *fixture_files(), "--by", "device", "--event", "TX_DONE,RX_DONE", "--no-color"]) == 0
    out = capsys.readouterr().out.splitlines()
    # 2026-10-04 20:29:22: the host clock puts the C2's RX_DONE 216 ms after the C3's TX_DONE; on device time they are 2.7 ms apart.
    i = next(n for n, ln in enumerate(out) if "ep=240032" not in ln and "rssi=-13 snr=8 pre=240103" in ln)
    assert "c3" in out[i - 1] and "TX_DONE" in out[i - 1] and "start=240032" in out[i - 1]
    assert "c2" in out[i] and "RX_DONE" in out[i]


def test_merge_by_device_marks_the_lines_placed_by_controller_time(capsys):
    assert main(["merge", *fixture_files(), "--by", "device", "--no-color"]) == 0
    out = capsys.readouterr().out.splitlines()
    c2_boot = next(ln for ln in out if ln.startswith("c2") and " BOOT " in ln and "cls=C2" in ln)
    assert " ~ " in c2_boot                          # before its lock: by controller time
    c2_rx = next(ln for ln in out if ln.startswith("c2") and "RX_DONE" in ln and "pre=60103" in ln)
    assert " ~ " not in c2_rx                        # locked: on the device axis


def test_merge_by_device_without_a_c3_asks_for_the_reference(capsys, tmp_path):
    from pathlib import Path

    c2 = Path(fixture_files()[1])
    assert main(["merge", str(c2), "--by", "device"]) == 2
    assert "--ref" in capsys.readouterr().err
    assert main(["merge", str(c2), "--by", "device", "--ref", "c2", "--no-color"]) == 0


def test_the_report_csv_carries_the_device_aligned_time_when_a_c3_is_in_the_capture(tmp_path):
    out = tmp_path / "r.md"
    assert main(["report", *fixture_files(), "-o", str(out)]) == 0
    rows = out.with_suffix(".csv").read_text(encoding="utf-8").splitlines()
    head = rows[0].split(",")
    assert head[-2:] == ["dev_aligned", "dev_basis"]
    locked = [r.split(",") for r in rows[1:] if r.split(",")[head.index("clk")] == "WARM"]
    assert locked and all(r[-1] == "device" and r[-2].startswith("2000-01-02T") for r in locked)
    cold = [r.split(",") for r in rows[1:] if r.split(",")[head.index("clk")] == "COLD"]
    assert cold and all(r[-1] == "host" for r in cold)
