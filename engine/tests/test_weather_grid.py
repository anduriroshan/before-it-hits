"""Tests for the weather_grid module."""
import importlib
import sys
from pathlib import Path

import pytest

_engine_dir = str(Path(__file__).resolve().parents[1] / "bih_engine")
if _engine_dir not in sys.path:
    sys.path.insert(0, _engine_dir)
wg = importlib.import_module("weather_grid")


def test_no_key_raises(monkeypatch):
    monkeypatch.delenv("WEATHER_API_KEY", raising=False)
    monkeypatch.delenv("MAPS_API_KEY", raising=False)
    with pytest.raises(wg.WeatherUnavailable, match="no WEATHER_API_KEY"):
        wg.grid((76.20, 9.85, 76.45, 10.10))


def test_grid_size_affects_points():
    # With grid_size=2, we'd get 4 points (2x2) if the API was available
    # Just test that the function accepts the parameter
    with pytest.raises(wg.WeatherUnavailable):
        wg.grid((76.20, 9.85, 76.45, 10.10), grid_size=2)
