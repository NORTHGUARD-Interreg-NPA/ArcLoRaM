"""The Links table of a run report: what each link sent and heard, next to what the places say it should give."""

from pathlib import Path

from arclog.links import LinkStats
from bench.links import links_section, run_links
from bench.positions import RadioModel, parse_positions

REPO = Path(__file__).resolve().parents[3]
DATA = REPO / "tools" / "arclog" / "tests" / "data" / "c3-c2-20261004"
UID_A = "003400443232501420383543"
UID_B = "000900244c42500d20363552"

POSITIONS = parse_positions(f"""
[default]
lat = 55.0
lon = 12.0
[[board]]
uid = "{UID_A}"
lat = 55.0
lon = 12.01
""")


def table(stats, positions=POSITIONS, model=None):
    return "\n".join(links_section(stats, {"a": UID_A, "b": UID_B}, positions, model or RadioModel(tx_power_dbm=22)))


def test_a_link_with_packets_shows_what_was_heard_and_the_snr_margin_to_the_sf12_limit():
    out = table([LinkStats("b", "a", sent=6, heard=4, rssi=[-104, -105, -105, -104], snr=[-18, -18, -19, -19])])
    assert "nuna" not in out
    assert "| b -> a | 4 of 6 |" in out
    assert "-105 / -104.5 / -104" in out                                       # min / median / max
    assert "-19 / -18.5 / -18" in out
    assert "+1 (median +1.5)" in out           # the worst packet 1 dB above the -20 dB limit
    assert "below 0 dB" in out                 # says why the SNR and not the RSSI is the margin


def test_the_distance_and_the_expected_link_come_from_the_places():
    out = table([LinkStats("b", "a", sent=1, heard=1, rssi=[-60], snr=[9])])
    # 0.01 degree of longitude at 55 north is 638 m; free space at 868.3 MHz is 87.3 dB: 22 dBm gives -65.3 dBm
    assert "638 m" in out
    assert "-65 dBm" in out.split("638 m")[1]
    assert "0 dB" in out.split("-65 dBm")[1] or "+" in out.split("-65 dBm")[1]       # loss over free space, measured: ~-60


def test_nothing_heard_is_said_and_has_no_margin():
    out = table([LinkStats("b", "a", sent=5, heard=0)])
    assert "0 of 5" in out and "nothing heard" in out


def test_boards_with_no_place_have_no_distance():
    out = table([LinkStats("b", "a", sent=1, heard=1, rssi=[-60], snr=[9])], positions=parse_positions(""))
    assert " - |" in out and " m |" not in out


def test_no_link_is_no_section():
    assert links_section([], {}, POSITIONS, RadioModel()) == []


def test_run_links_reads_the_run_folder_and_uses_the_channel_and_power_of_the_run(tmp_path):
    from bench.boards import Board, parse_uid

    for node, src in (("c3", "c3-20261004.log"), ("c2", "c2-20261004.log")):
        (tmp_path / f"{node}-20261004.log").write_text((DATA / src).read_text(encoding="utf-8"), encoding="utf-8")
    boards = {1: Board("p3", "tcp://p3:4000/c3", parse_uid(UID_B), "trace", 1, remote=True),
              2: Board("p2", "tcp://p2:4000/c2", parse_uid(UID_A), "trace", 2, remote=True)}
    lines = run_links(tmp_path, boards, {"FREQ_DEFAULT_HZ": "867300000u", "RADIO_TX_POWER_DBM": "14"}, REPO, POSITIONS)
    out = "\n".join(lines)
    assert "| c3 -> c2 | 12 of 16 |" in out and "867.3 MHz" in out and "14 dBm" in out
