"""Tests for the air_quality module.

Import directly to avoid pulling in the full engine (numba dependency).
"""
import importlib
import sys
from pathlib import Path

import pytest

_engine_dir = str(Path(__file__).resolve().parents[1] / "bih_engine")
if _engine_dir not in sys.path:
    sys.path.insert(0, _engine_dir)
aq = importlib.import_module("air_quality")


def test_category_good():
    cat = aq._category(42)
    assert cat["label"] == "Good"


def test_category_moderate():
    cat = aq._category(75)
    assert cat["label"] == "Moderate"


def test_category_unhealthy():
    cat = aq._category(160)
    assert cat["label"] == "Unhealthy"


def test_category_none():
    cat = aq._category(None)
    assert cat["label"] == "Unknown"


def test_category_hazardous():
    cat = aq._category(350)
    assert cat["label"] == "Hazardous"


def test_categories_cover_full_range():
    for val in [25, 75, 125, 175, 250, 400]:
        cat = aq._category(val)
        assert cat["label"] != "Unknown"


def test_no_key_raises(monkeypatch):
    monkeypatch.delenv("WEATHER_API_KEY", raising=False)
    monkeypatch.delenv("MAPS_API_KEY", raising=False)
    with pytest.raises(aq.AirQualityUnavailable, match="no WEATHER_API_KEY"):
        aq.fetch_current(9.97, 76.28)


def test_result_as_dict():
    r = aq.AirQualityResult(
        aqi=65,
        category="Moderate",
        dominant_pollutant="pm25",
        pollutants=[{"code": "pm25", "display_name": "PM2.5", "concentration": 18.5, "unit": "μg/m³"}],
        lat=9.97,
        lon=76.28,
    )
    d = r.as_dict()
    assert d["aqi"] == 65
    assert d["category"] == "Moderate"
    assert d["dominant_pollutant"] == "pm25"
    assert d["source"] == "Google Air Quality API"
    assert len(d["caveats"]) >= 2


def test_result_as_dict_no_aqi():
    r = aq.AirQualityResult(lat=9.97, lon=76.28)
    d = r.as_dict()
    assert d["aqi"] is None
    assert d["category"] == "Unknown"


def test_the_provider_s_unit_enums_are_written_the_way_people_write_them():
    """The value is the provider's and untouched; only the name of the unit is made readable."""
    assert aq._unit("PARTS_PER_BILLION") == "ppb"
    assert aq._unit("MICROGRAMS_PER_CUBIC_METER") == "µg/m³"
    assert aq._unit("SOMETHING_NEW") == "something new"      # an unknown one is not dropped
    assert aq._unit("") == ""


def test_categories_ordered():
    prev_max = -1
    for cat in aq.AQ_CATEGORIES:
        assert cat["range"][0] > prev_max
        prev_max = cat["range"][1]
