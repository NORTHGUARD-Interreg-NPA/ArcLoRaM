"""Radio channels of a run (issue #85): the plan, the channel an override names, the lease, who is on the air."""

from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from bench.channels import (
    ChannelPlan,
    Transmitter,
    air_conflicts,
    channel_of,
    describe_busy,
    hold_channel,
    held_channels,
    load_plan,
    mhz,
    channel_ids,
    observe_transmitters,
    parse_hz,
    parse_plan,
)
from bench.lease import Held, Leases

REPO = Path(__file__).resolve().parents[3]
PLAN = ChannelPlan(min_hz=865_100_000, max_hz=868_500_000, raster_hz=100_000, spacing_hz=1_000_000,
                   default_hz=868_300_000)
NOW = datetime(2026, 10, 12, 16, 30, tzinfo=timezone.utc)


# --- the plan comes from the firmware header, with no second copy -----------------------------------------------

def test_the_plan_is_read_from_the_firmware_header():
    assert load_plan(REPO) == PLAN


def test_a_header_missing_a_constant_is_refused():
    text = (REPO / "Common/Protocol/channel_plan.h").read_text(encoding="utf-8")
    broken = "\n".join(line for line in text.splitlines() if "CHANNEL_SPACING_HZ" not in line.split("/*")[0])
    with pytest.raises(ValueError, match="CHANNEL_SPACING_HZ"):
        parse_plan(broken)


# --- the channel an override names -------------------------------------------------------------------------------

@pytest.mark.parametrize("text, hz", [("867300000u", 867_300_000), ("867300000", 867_300_000),
                                       ("867300000UL", 867_300_000), (" 868300000u ", 868_300_000)])
def test_a_frequency_is_a_c_integer_in_hertz(text, hz):
    assert parse_hz(text) == hz


@pytest.mark.parametrize("text", ["867.3e6", "0x33AB0A60", "-1", "867,3", "FREQ", "", "8673e5u"])
def test_anything_else_is_refused(text):
    with pytest.raises(ValueError, match="Hz"):
        parse_hz(text)


def test_no_override_is_the_default_channel():
    assert channel_of({}, PLAN) == 868_300_000
    assert channel_of({"SYNC_BOOT_BURST": "5u"}, PLAN) == 868_300_000


def test_the_override_names_the_channel_of_the_run():
    assert channel_of({"FREQ_DEFAULT_HZ": "867300000u"}, PLAN) == 867_300_000


@pytest.mark.parametrize("hz, why", [
    ("865000000u", "865.1"),            # below the plan
    ("868600000u", "868.5"),            # above it
    ("868350000u", "100 kHz"),          # off the raster
    ("869525000u", "868.5"),            # the 10 % sub-band: not in the plan
])
def test_a_channel_outside_the_plan_is_refused_before_the_build(hz, why):
    with pytest.raises(ValueError, match=why):
        channel_of({"FREQ_DEFAULT_HZ": hz}, PLAN)


def test_a_channel_is_written_in_megahertz():
    assert mhz(868_300_000) == "868.3 MHz" and mhz(865_100_000) == "865.1 MHz"


# --- the lease: one session per channel, a megahertz of guard --------------------------------------------------

def leases(tmp_path, who):
    return Leases(tmp_path, who, f"bench run {who}")


def test_a_channel_leases_itself_exclusively_and_the_slots_within_a_megahertz_as_guard():
    own, guard = channel_ids(867_300_000, PLAN)
    assert own == "channel-867300"
    assert guard == [f"channel-{k}" for k in (866400, 866500, 866600, 866700, 866800, 866900, 867000, 867100, 867200,
                                              867400, 867500, 867600, 867700, 867800, 867900, 868000, 868100, 868200)]


def test_the_guard_stops_at_the_edge_of_the_plan():
    _, guard = channel_ids(865_100_000, PLAN)
    assert guard[0] == "channel-865200" and guard[-1] == "channel-866000" and len(guard) == 9
    _, guard = channel_ids(868_500_000, PLAN)
    assert guard[0] == "channel-867600" and guard[-1] == "channel-868400" and len(guard) == 9


def test_two_sessions_on_channels_a_megahertz_apart_coexist(tmp_path):
    with hold_channel(leases(tmp_path, "desk"), 868_300_000, PLAN):
        with hold_channel(leases(tmp_path, "remote"), 867_300_000, PLAN):
            pass


def test_the_same_channel_is_refused_with_who_holds_it(tmp_path):
    with hold_channel(leases(tmp_path, "desk"), 868_300_000, PLAN):
        with pytest.raises(Held) as caught:
            with hold_channel(leases(tmp_path, "remote"), 868_300_000, PLAN):
                pass
    assert "desk" in describe_busy(caught.value.busy, PLAN)
    assert "868.3 MHz" in describe_busy(caught.value.busy, PLAN)


@pytest.mark.parametrize("hz", [867_400_000, 867_900_000, 868_100_000, 868_500_000])
def test_a_channel_closer_than_a_megahertz_is_refused(tmp_path, hz):
    with hold_channel(leases(tmp_path, "desk"), 868_300_000, PLAN):
        with pytest.raises(Held) as caught:
            with hold_channel(leases(tmp_path, "remote"), hz, PLAN):
                pass
    text = describe_busy(caught.value.busy, PLAN)
    assert "1 MHz" in text and mhz(hz) in text


def test_the_guard_works_from_either_side(tmp_path):
    with hold_channel(leases(tmp_path, "far"), 867_300_000, PLAN):
        with pytest.raises(Held):
            with hold_channel(leases(tmp_path, "desk"), 868_000_000, PLAN):
                pass


def test_a_channel_is_given_back_when_the_session_ends(tmp_path):
    with hold_channel(leases(tmp_path, "a"), 868_300_000, PLAN):
        pass
    with hold_channel(leases(tmp_path, "b"), 868_200_000, PLAN):
        pass


def test_held_channels_say_who_holds_them_and_not_the_guard(tmp_path):
    with hold_channel(leases(tmp_path, "desk"), 868_300_000, PLAN):
        found = held_channels(leases(tmp_path, "me"), PLAN)
    assert list(found) == [868_300_000] and "desk" in found[868_300_000]
    assert held_channels(leases(tmp_path, "me"), PLAN) == {}


# --- who is on the air: the Sync packets in the shared capture ----------------------------------------------------

def sync_tx(when, freq):
    return (f"{when:%Y-%m-%dT%H:%M:%S.%f}Z\t000102T001532.0000 0Y M #06 SYNC_TX ph=0 ce=0 ep=32 plan=1 send=1 "
            f"freq={freq}\n")


def write_capture(directory, node, *lines):
    path = directory / f"{node}-{NOW:%Y%m%d}.log"
    path.write_text("".join(lines), encoding="utf-8")
    return path


def test_the_last_sync_of_a_board_says_which_channel_it_is_on(tmp_path):
    write_capture(tmp_path, "nuna-node-04", sync_tx(NOW - timedelta(minutes=15), 868_300_000),
                  sync_tx(NOW - timedelta(minutes=2), 867_300_000))
    found = observe_transmitters(tmp_path, ["nuna-node-04"], NOW)
    assert found == {"nuna-node-04": Transmitter(867_300_000, NOW - timedelta(minutes=2))}


def test_a_board_that_has_not_sent_in_twenty_minutes_is_not_on_the_air(tmp_path):
    write_capture(tmp_path, "nuna-node-06", sync_tx(NOW - timedelta(minutes=21), 868_300_000))
    write_capture(tmp_path, "nuna-node-08", "garbage\n")
    assert observe_transmitters(tmp_path, ["nuna-node-06", "nuna-node-08", "nuna-node-09"], NOW) == {}


def test_yesterdays_file_counts_just_after_midnight(tmp_path):
    midnight = datetime(2026, 10, 13, 0, 5, tzinfo=timezone.utc)
    (tmp_path / "nuna-node-06-20261012.log").write_text(sync_tx(midnight - timedelta(minutes=8), 868_300_000),
                                                         encoding="utf-8")
    found = observe_transmitters(tmp_path, ["nuna-node-06"], midnight)
    assert found["nuna-node-06"].freq_hz == 868_300_000


def test_a_long_capture_is_read_from_its_end(tmp_path):
    filler = "".join(f"{(NOW - timedelta(hours=5)):%Y-%m-%dT%H:%M:%S.%f}Z\t000102T001532.0000 0T H #01 SLOT x=1\n"
                     for _ in range(20_000))
    path = write_capture(tmp_path, "nuna-node-06", filler, sync_tx(NOW - timedelta(minutes=1), 868_300_000))
    assert path.stat().st_size > 1_000_000
    assert observe_transmitters(tmp_path, ["nuna-node-06"], NOW)["nuna-node-06"].freq_hz == 868_300_000


def test_boards_on_a_channel_within_a_megahertz_share_the_air_with_the_run():
    on_air = {
        "nuna-node-08": Transmitter(868_300_000, NOW),      # the same channel
        "nuna-node-09": Transmitter(867_400_000, NOW),      # 900 kHz away
        "nuna-node-02": Transmitter(867_300_000, NOW),      # exactly a megahertz: clear
        "nuna-node-04": Transmitter(868_300_000, NOW),      # in the run itself
    }
    found = air_conflicts(868_300_000, on_air, PLAN, mine={"nuna-node-04"})
    assert sorted(found) == ["nuna-node-08", "nuna-node-09"]


# --- the policy of a command: refuse, warn, or go ----------------------------------------------------------------

from bench.channels import free_channels, judge_air  # noqa: E402


def test_free_channels_keep_a_megahertz_from_everything_in_use():
    free = free_channels(PLAN, [868_300_000])
    assert free[0] == 867_300_000
    assert all(abs(f - 868_300_000) >= 1_000_000 for f in free) and 868_300_000 not in free


def test_the_free_channels_are_the_default_first_when_nothing_is_in_use():
    assert free_channels(PLAN, [])[0] == 868_300_000


def test_a_plan_full_of_traffic_has_no_free_channel():
    assert free_channels(PLAN, [865_100_000, 866_100_000, 867_100_000, 868_100_000, 868_500_000]) == []


LABELS = {"nuna-node-06": "nuna-node-06 (Node ID 1)", "nuna-node-08": "nuna-node-08 (Node ID 6)",
          "nuna-node-04": "nuna-node-04 (Node ID 4)"}


def test_an_idle_board_on_the_channel_is_a_warning_not_a_refusal():
    air = judge_air(868_300_000, PLAN, {"nuna-node-08": Transmitter(868_300_000, NOW)}, LABELS, mine=set(), held={})
    assert air.refuse == [] and len(air.warn) == 1
    assert "nuna-node-08 (Node ID 6)" in air.warn[0] and "868.3 MHz" in air.warn[0] and "idle" in air.warn[0]


def test_a_board_another_session_holds_on_the_channel_is_a_refusal_naming_the_session():
    air = judge_air(868_300_000, PLAN, {"nuna-node-06": Transmitter(868_400_000, NOW)}, LABELS, mine=set(),
                    held={"nuna-node-06": "101-crash-record: bench run x.toml (pid 7, since 15:49:26 UTC)"})
    assert air.warn == [] and len(air.refuse) == 1
    assert "101-crash-record" in air.refuse[0] and "nuna-node-06 (Node ID 1)" in air.refuse[0]
    assert "868.4 MHz" in air.refuse[0]


def test_boards_of_the_run_and_boards_a_megahertz_away_say_nothing():
    on_air = {"nuna-node-04": Transmitter(868_300_000, NOW), "nuna-node-08": Transmitter(867_300_000, NOW)}
    air = judge_air(868_300_000, PLAN, on_air, LABELS, mine={"nuna-node-04"}, held={})
    assert air.refuse == [] and air.warn == []
