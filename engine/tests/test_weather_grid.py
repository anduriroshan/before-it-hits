"""The weather cube behind the animated map field: offline.

The fetching needs a key and a network, so what is checked here is everything else - the shape of
the cube, the reduction to one variable, and the two properties the field rendering leans on: a
missing hour stays missing rather than becoming a zero, and the colour bounds do not move when the
data moves inside them.
"""
import importlib
import sys
from pathlib import Path

import pytest

_engine_dir = str(Path(__file__).resolve().parents[1] / "bih_engine")
if _engine_dir not in sys.path:
    sys.path.insert(0, _engine_dir)
wg = importlib.import_module("weather_grid")


def _cube(*series):
    """A cube from (heat_index, humidity) pairs per hour, one point per series."""
    pts = []
    for i, hours in enumerate(series):
        pts.append({"lat": 10.0 + i, "lng": 76.0 + i,
                    "hours": [{"time": f"2026-10-13T0{h}:00:00Z", "heat_index_c": hi,
                               "humidity_pct": hum, "temperature_c": 30.0}
                              for h, (hi, hum) in enumerate(hours)]})
    return {"points": pts, "times": [f"2026-10-13T0{h}:00:00Z" for h in range(len(series[0]))],
            "bbox": [76.2, 9.85, 76.45, 10.1], "grid_size": 2, "spacing_km": 27.8,
            "source": "Google Maps Platform Weather API (hourly forecast)"}


def test_no_key_raises(monkeypatch):
    monkeypatch.delenv("WEATHER_API_KEY", raising=False)
    monkeypatch.delenv("MAPS_API_KEY", raising=False)
    with pytest.raises(wg.WeatherUnavailable, match="no WEATHER_API_KEY"):
        wg.grid((76.20, 9.85, 76.45, 10.10))


def test_the_axis_spans_the_whole_edge_and_degenerates_to_the_centre():
    assert wg._axis(0.0, 10.0, 3) == [0.0, 5.0, 10.0]
    assert wg._axis(0.0, 10.0, 1) == [5.0]


def test_an_hour_is_parsed_into_flat_named_values():
    h = wg._parse_hour({
        "interval": {"startTime": "2026-10-13T06:00:00Z"},
        "temperature": {"degrees": 31.4}, "relativeHumidity": 78,
        "heatIndex": {"degrees": 38.2}, "wind": {"speed": {"value": 12.0},
                                                 "direction": {"degrees": 240}},
        "precipitation": {"qpf": {"quantity": 2.5}},
    })
    assert h == {"time": "2026-10-13T06:00:00Z", "temperature_c": 31.4, "humidity_pct": 78,
                 "heat_index_c": 38.2, "wind_speed_kmh": 12.0, "wind_dir_deg": 240,
                 "precip_mm": 2.5}


def test_a_missing_value_parses_to_none_rather_than_zero():
    h = wg._parse_hour({"interval": {"startTime": "t"}})
    assert h["temperature_c"] is None and h["precip_mm"] is None and h["wind_dir_deg"] is None


def test_the_field_is_one_variable_per_point_per_hour():
    f = wg.field(_cube([(36.0, 70), (38.0, 72)], [(34.0, 80), (35.0, 82)]), "heat")
    assert [p["values"] for p in f["points"]] == [[36.0, 38.0], [34.0, 35.0]]
    assert f["field"]["unit"] == "°C" and f["field"]["label"] == "Heat index"
    assert f["times"] == ["2026-10-13T00:00:00Z", "2026-10-13T01:00:00Z"]


def test_humidity_reads_a_different_variable_from_the_same_cube():
    f = wg.field(_cube([(36.0, 70), (38.0, 72)]), "humidity")
    assert [p["values"] for p in f["points"]] == [[70.0, 72.0]]
    assert f["field"]["unit"] == "%"


def test_heat_falls_back_to_air_temperature_where_the_provider_gives_no_heat_index():
    f = wg.field(_cube([(None, 70), (38.0, 72)]), "heat")
    assert f["points"][0]["values"] == [30.0, 38.0]          # 30.0 is the air temperature


def test_a_variable_the_provider_never_gave_stays_missing_and_is_not_filled_in():
    cube = _cube([(36.0, 70)])
    cube["points"][0]["hours"][0]["wind_speed_kmh"] = None
    with pytest.raises(wg.WeatherUnavailable, match="wind speed"):
        wg.field(cube, "wind")


def test_a_point_that_returned_fewer_hours_pads_with_none_not_with_its_last_value():
    cube = _cube([(36.0, 70), (38.0, 72)], [(34.0, 80), (35.0, 82)])
    cube["points"][1]["hours"] = cube["points"][1]["hours"][:1]
    f = wg.field(cube, "heat")
    assert f["points"][1]["values"] == [34.0, None]


def test_the_colour_bounds_hold_still_while_the_data_moves_inside_them():
    """A legend that rescales every frame cannot be read. The bounds only ever widen."""
    mild = wg.field(_cube([(30.0, 70), (31.0, 71)]), "heat")["field"]
    assert (mild["min"], mild["max"]) == (24.0, 44.0)
    extreme = wg.field(_cube([(18.0, 70), (49.0, 71)]), "heat")["field"]
    assert extreme["min"] == 18.0 and extreme["max"] == 49.0


def test_an_unknown_field_name_falls_back_to_heat_and_says_so():
    """The id comes back resolved, so the legend never labels one variable with another's name."""
    assert wg.field(_cube([(36.0, 70)]), "nonsense")["field"]["id"] == "heat"


def test_every_field_declares_what_it_is_so_the_legend_never_invents_a_label():
    for spec in wg.FIELDS.values():
        assert spec["label"] and spec["unit"] is not None and spec["note"]
        assert spec["ceil"] > spec["floor"]
