"""positions.toml: where each board stands, keyed by its chip UID (issue #85)."""

import math

import pytest

from bench.positions import (
    FADE_MARGIN_DB,
    Place,
    RadioModel,
    expected_neighbours,
    free_space_loss_db,
    link_distance_m,
    links,
    load_positions,
    parse_positions,
    unknown_boards,
)

UID_A = "003400443232501420383543"
UID_B = "0014008f3232501420383543"
UID_C = "001e00114c42500d20363552"

#: Made-up coordinates (a field in the open): the real sites are not in the repo (it is public).
TWO_SITES = f"""
[default]
place = "Home field"
lat = 55.0
lon = 12.0

[[board]]
uid = "{UID_A}"
place = "East end"
lat = 55.0
lon = 12.01
alt = 20.5
"""


def test_a_board_has_its_own_place_and_the_others_the_default():
    pos = parse_positions(TWO_SITES)
    assert pos.place_of(UID_A) == Place(lat=55.0, lon=12.01, alt=20.5, name="East end")
    assert pos.place_of(UID_B) == Place(lat=55.0, lon=12.0, alt=None, name="Home field")


def test_no_default_and_no_entry_is_no_place():
    pos = parse_positions(f'[[board]]\nuid = "{UID_A}"\nlat = 1\nlon = 2\n')
    assert pos.place_of(UID_B) is None
    assert pos.place_of(None) is None
    assert pos.place_of(UID_A) == Place(lat=1.0, lon=2.0)


def test_an_empty_file_has_no_places_at_all():
    pos = parse_positions("")
    assert pos.default is None and pos.boards == {}


def test_a_uid_is_matched_in_any_letter_case():
    pos = parse_positions(f'[[board]]\nuid = "{UID_B.upper()}"\nlat = 1\nlon = 2\n')
    assert pos.place_of(UID_B) == Place(lat=1.0, lon=2.0)
    assert pos.place_of(UID_B.upper()) == Place(lat=1.0, lon=2.0)


@pytest.mark.parametrize("text, why", [
    ('[[board]]\nuid = "abc"\nlat = 1\nlon = 2\n', "uid"),                                  # too short
    (f'[[board]]\nuid = "{UID_A[:-1]}z"\nlat = 1\nlon = 2\n', "uid"),                       # not hex
    ('[[board]]\nlat = 1\nlon = 2\n', "uid"),
    (f'[[board]]\nuid = "{UID_A}"\nlat = 90.5\nlon = 2\n', "lat"),
    (f'[[board]]\nuid = "{UID_A}"\nlat = -90.0001\nlon = 2\n', "lat"),
    (f'[[board]]\nuid = "{UID_A}"\nlat = 1\nlon = 180.5\n', "lon"),
    (f'[[board]]\nuid = "{UID_A}"\nlat = 1\nlon = -181\n', "lon"),
    (f'[[board]]\nuid = "{UID_A}"\nlat = "55.7"\nlon = 2\n', "lat"),                        # a string
    (f'[[board]]\nuid = "{UID_A}"\nlat = true\nlon = 2\n', "lat"),                          # a bool is no number
    (f'[[board]]\nuid = "{UID_A}"\nlat = nan\nlon = 2\n', "lat"),
    (f'[[board]]\nuid = "{UID_A}"\nlat = 1\nlon = inf\n', "lon"),
    (f'[[board]]\nuid = "{UID_A}"\nlat = 1\n', "lon"),                                      # lon missing
    (f'[[board]]\nuid = "{UID_A}"\nlon = 1\n', "lat"),
    (f'[[board]]\nuid = "{UID_A}"\nlat = 1\nlon = 2\nalt = 99999\n', "alt"),
    (f'[[board]]\nuid = "{UID_A}"\nlat = 1\nlon = 2\nplace = 7\n', "place"),
    (f'[[board]]\nuid = "{UID_A}"\nlat = 1\nlon = 2\n[[board]]\nuid = "{UID_A.upper()}"\nlat = 3\nlon = 4\n', "twice"),
    (f'[[board]]\nuid = "{UID_A}"\nlat = 1\nlon = 2\nheight = 3\n', "unknown key"),
    ('[default]\nlat = 1\nlon = 2\nuid = "x"\n', "unknown key"),                            # a default has no UID
    ('[default]\nlat = 1\n', "lon"),
    ('[defaults]\nlat = 1\nlon = 2\n', "unknown table"),
    ('[[boards]]\nuid = "x"\n', "unknown table"),
])
def test_a_wrong_positions_file_says_what_is_wrong(text, why):
    with pytest.raises(ValueError, match=why):
        parse_positions(text)


def test_the_error_names_the_entry():
    text = f'[[board]]\nuid = "{UID_A}"\nlat = 1\nlon = 2\n[[board]]\nuid = "{UID_B}"\nlat = 99\nlon = 2\n'
    with pytest.raises(ValueError, match="board #2"):
        parse_positions(text)


def test_the_environment_names_the_file_and_it_must_exist(tmp_path):
    f = tmp_path / "p.toml"
    f.write_text(TWO_SITES, encoding="utf-8")
    assert load_positions(env={"BENCH_POSITIONS": str(f)}, home=tmp_path).place_of(UID_A).alt == 20.5
    with pytest.raises(ValueError, match="BENCH_POSITIONS"):
        load_positions(env={"BENCH_POSITIONS": str(tmp_path / "none.toml")}, home=tmp_path)


def test_the_default_file_is_in_the_user_config_directory_and_may_be_absent(tmp_path):
    assert load_positions(env={}, home=tmp_path).default is None
    f = tmp_path / ".config" / "bench" / "positions.toml"
    f.parent.mkdir(parents=True)
    f.write_text(TWO_SITES, encoding="utf-8")
    assert load_positions(env={}, home=tmp_path).default.name == "Home field"


def test_a_file_with_an_error_names_the_file(tmp_path):
    f = tmp_path / "p.toml"
    f.write_text("[[board]]\nuid = 'x'\n", encoding="utf-8")
    with pytest.raises(ValueError, match="p.toml"):
        load_positions(env={"BENCH_POSITIONS": str(f)}, home=tmp_path)


# --- distance: worked by hand -------------------------------------------------------------------
# Mean Earth radius 6371.0088 km, so one degree of arc is 6371008.8 * pi / 180 = 111194.93 m.
# Along a meridian that is the distance for any latitude; along a parallel it shrinks with cos(lat).

def test_a_thousandth_of_a_degree_of_latitude_is_111_19_m():
    assert link_distance_m(Place(55.0, 12.0), Place(55.001, 12.0)) == pytest.approx(111.195, abs=0.01)


def test_a_degree_of_longitude_at_60_north_is_half_a_degree_of_latitude():
    # cos(60 degrees) = 0.5 exactly: 111194.93 / 2 = 55597.46 m.
    assert link_distance_m(Place(60.0, 10.0), Place(60.0, 11.0)) == pytest.approx(55_597.5, abs=15)


def test_the_distance_is_the_same_both_ways_and_zero_to_itself():
    a, b = Place(55.0031, 12.0042), Place(55.0057, 12.0123)
    assert link_distance_m(a, b) == link_distance_m(b, a)
    assert link_distance_m(a, a) == 0.0


def test_a_hill_makes_the_link_longer_by_pythagoras():
    # 300 m on the ground and 400 m of height: 500 m in a straight line, if both altitudes are known.
    flat = Place(55.0, 12.0, alt=10.0)
    up = Place(55.0, 12.0 + 300 / (111_194.93 * math.cos(math.radians(55.0))), alt=410.0)
    assert link_distance_m(flat, up) == pytest.approx(500.0, abs=0.5)
    # An unknown altitude on either end leaves the ground distance.
    assert link_distance_m(flat, Place(up.lat, up.lon)) == pytest.approx(300.0, abs=0.5)


# --- expected path loss: worked by hand ---------------------------------------------------------
# Free space: 20 log10(d in km) + 20 log10(f in MHz) + 32.45 dB (32.448 exactly: 20 log10(4 pi 10^9 / c)).
#   1 km at 868.3 MHz: 0 + 58.774 + 32.45 = 91.22 dB.   100 m: 20 dB less (71.22).   2 km: 6.02 dB more.

def test_free_space_loss_of_a_kilometre_at_868_3_mhz_is_91_22_db():
    assert free_space_loss_db(1000.0, 868.3e6) == pytest.approx(91.22, abs=0.01)


def test_ten_times_the_distance_is_20_db_more_in_free_space():
    assert free_space_loss_db(100.0, 868.3e6) == pytest.approx(71.22, abs=0.01)
    assert free_space_loss_db(2000.0, 868.3e6) - free_space_loss_db(1000.0, 868.3e6) == pytest.approx(6.02, abs=0.01)


def test_a_bigger_exponent_costs_10_db_per_decade_per_unit():
    # Exponent 3: the loss at 1 km is the free-space loss at 1 m plus 30 dB per decade, three decades.
    one_m = free_space_loss_db(1.0, 868.3e6)
    assert free_space_loss_db(1000.0, 868.3e6, exponent=3.0) == pytest.approx(one_m + 90.0, abs=0.01)
    assert free_space_loss_db(1000.0, 868.3e6, exponent=2.0) == pytest.approx(one_m + 60.0, abs=0.01)


def test_the_radio_model_gives_the_expected_rssi_and_snr_of_a_link():
    # 14 dBm configured, no antenna gain assumed, 1 km: 14 - 91.22 = -77.22 dBm.
    # Noise floor in 125 kHz with a 6 dB receiver: -174 + 50.97 + 6 = -117.03 dBm, so SNR = -77.22 + 117.03 = 39.81.
    m = RadioModel()
    assert m.expected_rssi_dbm(1000.0) == pytest.approx(-77.22, abs=0.02)
    assert m.noise_floor_dbm == pytest.approx(-117.03, abs=0.02)
    assert m.expected_snr_db(1000.0) == pytest.approx(39.81, abs=0.04)


def test_the_margin_of_a_measured_link_is_to_the_sensitivity_and_to_the_snr_limit():
    m = RadioModel()
    assert m.sensitivity_dbm == -137.0 and m.snr_limit_db == -20.0
    assert m.rssi_margin_db(-97.0) == pytest.approx(40.0)
    assert m.snr_margin_db(-5.0) == pytest.approx(15.0)


# --- links and neighbours -----------------------------------------------------------------------

def test_links_lists_every_pair_once_with_its_distance_and_expected_rssi():
    pos = parse_positions(TWO_SITES)
    found = links(pos, [UID_A, UID_B, UID_C], RadioModel())
    assert [(l.a, l.b) for l in found] == [(UID_A, UID_B), (UID_A, UID_C), (UID_B, UID_C)]
    ab = found[0]
    # 0.01 degree of longitude at 55 north: 0.01 * 111194.93 * cos(55 degrees) = 637.8 m.
    assert ab.distance_m == pytest.approx(637.8, abs=1.0)
    assert ab.expected_rssi_dbm == pytest.approx(14.0 - free_space_loss_db(ab.distance_m, 868.3e6), abs=0.01)
    # B and C share the default place: a link of length zero, flagged as co-located.
    assert found[2].distance_m == 0.0 and found[2].colocated


def test_a_board_with_no_place_has_no_link():
    pos = parse_positions(f'[[board]]\nuid = "{UID_A}"\nlat = 1\nlon = 2\n')
    assert links(pos, [UID_A, UID_B], RadioModel()) == []


def test_expected_neighbours_are_the_links_above_the_sensitivity_plus_the_fade_margin():
    pos = parse_positions(f"""
[default]
lat = 55.0
lon = 12.0
[[board]]
uid = "{UID_A}"
lat = 55.0
lon = 12.01
[[board]]
uid = "{UID_B}"
lat = 56.0
lon = 12.0
""")
    # With an exponent of 3.5 the budget (14 + 137 - 10 dB) ends at about 4 km: A (640 m) is a neighbour of the
    # default place, B (111 km) is not.
    m = RadioModel(exponent=3.5)
    near = expected_neighbours(pos, [UID_A, UID_B, UID_C], m)
    assert near[UID_C] == [UID_A]
    assert near[UID_A] == [UID_C]
    assert near[UID_B] == []
    assert FADE_MARGIN_DB == 10.0


def test_unknown_boards_are_the_positions_that_no_board_has():
    pos = parse_positions(f'[[board]]\nuid = "{UID_A}"\nlat = 1\nlon = 2\n[[board]]\nuid = "{UID_C}"\nlat = 1\nlon = 3\n')
    assert unknown_boards(pos, {UID_A, UID_B}) == [UID_C]
    assert unknown_boards(parse_positions(""), {UID_A}) == []
