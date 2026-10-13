"""Tests for the heat hazard module.

These import heat.py directly to avoid pulling in the full engine (which needs numba).
"""
import importlib
import sys
from pathlib import Path

import pytest

_engine_dir = str(Path(__file__).resolve().parents[1] / "bih_engine")
if _engine_dir not in sys.path:
    sys.path.insert(0, _engine_dir)
heat = importlib.import_module("heat")


def _make_hour(temp_c=30, humidity=70, heat_index_c=33, wind=10, dew=24, time="2026-10-14T10:00:00Z"):
    return {
        "interval": {"startTime": time},
        "temperature": {"degrees": temp_c},
        "relativeHumidity": humidity,
        "heatIndex": {"degrees": heat_index_c},
        "dewPoint": {"degrees": dew},
        "wind": {"speed": {"value": wind}},
    }


def test_parse_hour():
    h = heat._parse_hour(_make_hour(temp_c=32, humidity=80, heat_index_c=38))
    assert h["temperature_c"] == 32
    assert h["humidity_pct"] == 80
    assert h["heat_index_c"] == 38


def test_parse_hour_missing_fields():
    h = heat._parse_hour({"interval": {"startTime": "2026-10-14T10:00:00Z"}})
    assert h["temperature_c"] is None
    assert h["humidity_pct"] is None
    assert h["heat_index_c"] is None


def test_heat_level_classification():
    hf = heat.HeatForecast()
    assert hf.heat_level(55)["label"] == "Extreme danger"
    assert hf.heat_level(45)["label"] == "Danger"
    assert hf.heat_level(35)["label"] == "Extreme caution"
    assert hf.heat_level(28)["label"] == "Caution"
    assert hf.heat_level(20)["label"] == "Normal"
    assert hf.heat_level(None)["label"] == "Normal"


def test_heat_forecast_as_dict():
    hf = heat.HeatForecast(
        hours=[{"time": "T1", "temperature_c": 30, "heat_index_c": 35, "humidity_pct": 70,
                "dew_point_c": 24, "wind_speed_kmh": 10}],
        current={"temperature_c": 30, "heat_index_c": 35, "humidity_pct": 70},
        peak={"temperature_c": 32, "heat_index_c": 38, "humidity_pct": 65},
        lat=9.97, lon=76.28,
    )
    d = hf.as_dict()
    assert d["current"]["temperature_c"] == 30
    assert d["current_level"]["label"] == "Extreme caution"
    assert d["peak_level"]["label"] == "Extreme caution"
    assert len(d["caveats"]) > 0
    assert "source" in d


def test_heat_unavailable_no_key(monkeypatch):
    monkeypatch.delenv("WEATHER_API_KEY", raising=False)
    monkeypatch.delenv("MAPS_API_KEY", raising=False)
    with pytest.raises(heat.HeatUnavailable, match="no WEATHER_API_KEY"):
        heat.fetch(9.97, 76.28)


def test_for_bbox(monkeypatch):
    monkeypatch.delenv("WEATHER_API_KEY", raising=False)
    monkeypatch.delenv("MAPS_API_KEY", raising=False)
    with pytest.raises(heat.HeatUnavailable):
        heat.for_bbox((76.20, 9.85, 76.45, 10.10))


def test_heat_levels_order():
    for i in range(len(heat.HEAT_LEVELS) - 1):
        assert heat.HEAT_LEVELS[i]["min_c"] > heat.HEAT_LEVELS[i + 1]["min_c"]
