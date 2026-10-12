"""What a command does about its channel before it builds or flashes (bench.cli._claim_channel)."""

from contextlib import ExitStack
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from bench.boards import Board
from bench.channels import AirBusy
from bench.cli import _claim_channel
from bench.lease import Held, Leases

REPO = Path(__file__).resolve().parents[3]
UID = (1, 2, 3)


def board(name, node_id):
    return Board(name, f"tcp://{name}:4000/{name}", UID, "trace", node_id, remote=True)


MINE = board("nuna-node-04", 4)
OTHER = board("nuna-node-08", 6)
FOUND = [MINE, OTHER]


def sync(capture, board_, freq, minutes_ago=2):
    when = datetime.now(timezone.utc) - timedelta(minutes=minutes_ago)
    (capture / f"{board_.node}-{when:%Y%m%d}.log").write_text(
        f"{when:%Y-%m-%dT%H:%M:%S.%f}Z\t000102T001532.0000 0Y M #06 SYNC_TX ph=0 ce=0 ep=32 plan=1 send=1 freq={freq}\n",
        encoding="utf-8")


def claim(tmp_path, overrides, found=FOUND, mine=(MINE,), share_air=False, session="me"):
    leases = Leases(tmp_path / "leases", session, f"bench run {session}")
    with ExitStack() as stack:
        hz = _claim_channel(stack, REPO, leases, overrides, found, list(mine), tmp_path / "capture", share_air)
        return hz


@pytest.fixture
def capture(tmp_path):
    (tmp_path / "capture").mkdir()
    return tmp_path / "capture"


def test_no_override_is_the_default_channel_and_nobody_to_wait_for(tmp_path, capture, capsys):
    assert claim(tmp_path, {}) == 868_300_000
    assert "channel 868.3 MHz" in capsys.readouterr().out


def test_the_override_names_the_channel(tmp_path, capture):
    assert claim(tmp_path, {"FREQ_DEFAULT_HZ": "867300000u"}) == 867_300_000


def test_a_channel_outside_the_plan_stops_the_command(tmp_path, capture):
    with pytest.raises(ValueError, match="outside the channel plan"):
        claim(tmp_path, {"FREQ_DEFAULT_HZ": "869525000u"})


def test_another_session_on_the_channel_holds_it(tmp_path, capture):
    leases = Leases(tmp_path / "leases", "desk", "bench run desk")
    with ExitStack() as stack:
        _claim_channel(stack, REPO, leases, {}, FOUND, [MINE], capture, False)
        with pytest.raises(Held):
            claim(tmp_path, {"FREQ_DEFAULT_HZ": "868100000u"}, session="remote")
        assert claim(tmp_path, {"FREQ_DEFAULT_HZ": "867300000u"}, session="remote") == 867_300_000


def test_an_idle_board_on_the_channel_is_said_and_the_command_goes_on(tmp_path, capture, capsys):
    sync(capture, OTHER, 868_300_000)
    assert claim(tmp_path, {}) == 868_300_000
    out = capsys.readouterr().out
    assert "air: nuna-node-08 (Node ID 6) sent Sync on 868.3 MHz" in out and "idle" in out


def test_a_board_another_session_holds_stops_the_command_with_a_free_channel_to_take(tmp_path, capture):
    sync(capture, OTHER, 868_300_000)
    other = Leases(tmp_path / "leases", "101-crash-record", "bench run core-stall.toml")
    with other.hold(exclusive=[OTHER.sn]):
        with pytest.raises(AirBusy) as caught:
            claim(tmp_path, {})
    text = str(caught.value)
    assert "101-crash-record" in text and "nuna-node-08 (Node ID 6)" in text
    assert "-D FREQ_DEFAULT_HZ=867300000u" in text and "--share-air" in text


def test_share_air_goes_on_and_says_so(tmp_path, capture, capsys):
    sync(capture, OTHER, 868_300_000)
    other = Leases(tmp_path / "leases", "101-crash-record", "bench run core-stall.toml")
    with other.hold(exclusive=[OTHER.sn]):
        assert claim(tmp_path, {}, share_air=True) == 868_300_000
    assert "air (--share-air): " in capsys.readouterr().out


def test_a_held_board_a_megahertz_away_is_no_conflict(tmp_path, capture):
    sync(capture, OTHER, 868_300_000)
    other = Leases(tmp_path / "leases", "101-crash-record", "bench run core-stall.toml")
    with other.hold(exclusive=[OTHER.sn]):
        assert claim(tmp_path, {"FREQ_DEFAULT_HZ": "867300000u"}) == 867_300_000


def test_a_run_says_its_channel_and_power(tmp_path, capture, capsys):
    claim(tmp_path, {"FREQ_DEFAULT_HZ": "869500000u", "RADIO_TX_POWER_DBM": "22"})
    assert "channel 869.5 MHz at 22 dBm" in capsys.readouterr().out


def test_more_than_the_limit_off_the_high_power_channel_stops_the_command(tmp_path, capture):
    with pytest.raises(ValueError, match="869.5 MHz"):
        claim(tmp_path, {"FREQ_DEFAULT_HZ": "867300000u", "RADIO_TX_POWER_DBM": "22"})


def test_scenario_check_refuses_it_with_exit_3_and_accepts_the_high_power_channel(tmp_path, capsys):
    from bench.cli import main

    bad = tmp_path / "bad.toml"
    bad.write_text('[nodes]\n2 = "C3"\n[overrides]\nRADIO_TX_POWER_DBM = "22"\n', encoding="utf-8")
    assert main(["--repo", str(REPO), "scenario", "check", str(bad)]) == 3
    assert "869.5 MHz" in capsys.readouterr().err
    good = tmp_path / "good.toml"
    good.write_text('[nodes]\n2 = "C3"\n[overrides]\nFREQ_DEFAULT_HZ = "869500000u"\nRADIO_TX_POWER_DBM = "22"\n',
                    encoding="utf-8")
    assert main(["--repo", str(REPO), "scenario", "check", str(good)]) == 0
    assert "channel: 869.5 MHz at 22 dBm" in capsys.readouterr().out
