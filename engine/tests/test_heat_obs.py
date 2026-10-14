"""The NEA WBGT station feed. Offline: a captured payload in the shape the live API returns, which
sends every field as a string and sometimes sends one that will not parse."""
import pytest

from bih_engine import heat_obs


def station(sid, name, lat, lon, wbgt, rated="Low"):
    return {"station": {"id": sid, "name": name}, "location": {"latitude": str(lat), "longitude": str(lon)},
            "wbgt": str(wbgt), "heatStress": rated}


def records(rows, at="2026-10-15T02:00:00+08:00"):
    return [{"datetime": at, "item": {"type": "observation", "readings": rows}}]


# Geylang / Kallang sits at about 1.315 N, 103.87 E.
BOX = (103.860, 1.305, 103.885, 1.325)
ROWS = [
    station("S124", "Upper Changi Road North", 1.36777, 103.982262, 26.9),
    station("S125", "Woodlands Street 13", 1.433789, 103.780731, 26.7),
    station("S43", "Kallang", 1.3135, 103.8700, 33.4, "High"),
    station("S108", "Marina Gardens Drive", 1.2790, 103.8700, 31.2, "Moderate"),
]


def test_a_station_reading_is_parsed_into_numbers_and_keeps_the_authoritys_own_rating():
    out = heat_obs.near(BOX, records=records(ROWS))
    nearest = out["nearest"][0]
    assert nearest["name"] == "Kallang"
    assert nearest["wbgt_c"] == 33.4 and nearest["rated"] == "High"
    assert isinstance(nearest["lat"], float) and isinstance(nearest["lon"], float)


def test_stations_are_ranked_by_distance_from_the_box_and_the_box_one_is_flagged():
    out = heat_obs.near(BOX, records=records(ROWS), k=4)
    assert [r["name"] for r in out["nearest"]][:2] == ["Kallang", "Marina Gardens Drive"]
    assert out["nearest"][0]["in_box"] is True
    assert out["nearest"][1]["in_box"] is False
    assert out["nearest"][0]["km"] < out["nearest"][1]["km"]


def test_the_hottest_nearby_station_is_reported_as_well_as_the_nearest():
    """The nearest station is not always the worst one, and a heat answer that only ever quotes the
    nearest would understate the area whenever a hotter station sits just outside the box."""
    rows = [station("S1", "Cool and near", 1.315, 103.870, 27.0),
            station("S2", "Hot and close", 1.320, 103.875, 34.1, "High")]
    out = heat_obs.near(BOX, records=records(rows))
    assert out["nearest"][0]["name"] == "Cool and near"
    assert out["hottest_nearby"]["name"] == "Hot and close"


def test_a_station_mid_outage_is_dropped_rather_than_read_as_cool():
    rows = ROWS + [{"station": {"id": "S99", "name": "Broken"},
                    "location": {"latitude": "1.315", "longitude": "103.870"},
                    "wbgt": "-", "heatStress": None}]
    out = heat_obs.near(BOX, records=records(rows), k=9)
    assert "Broken" not in [r["name"] for r in out["nearest"]]
    assert out["stations_reporting"] == len(ROWS)


def test_a_payload_with_no_usable_station_raises_rather_than_reporting_calm():
    with pytest.raises(heat_obs.HeatObsUnavailable):
        heat_obs.near(BOX, records=records([{"station": {}, "location": {}, "wbgt": None}]))


def test_the_reading_is_labelled_as_measured_and_not_comparable_with_air_temperature():
    out = heat_obs.near(BOX, records=records(ROWS))
    assert "data.gov.sg" in out["source"]
    assert "not an air temperature" in out["note"]
    assert out["at"] == "2026-10-15T02:00:00+08:00"


def test_only_a_city_with_the_station_network_staged_offers_this():
    class R:
        def __init__(self, m):
            self.manifest = m
    assert heat_obs.available(R({"heat_standard": {"observed": {"source": "nea_wbgt"}}})) is True
    assert heat_obs.available(R({"heat_standard": {"authority": "IMD"}})) is False
    assert heat_obs.available(R({})) is False
