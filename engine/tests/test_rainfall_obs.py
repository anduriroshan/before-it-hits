"""Live observed rainfall (NEA gauges): offline. The network payload is injected via `data=`, so
what is tested is the part that is ours - ranking gauges by nearness to the box, picking the
wettest nearby, and failing loudly when nothing reports."""
import pytest

from bih_engine import rainfall_obs
from bih_engine.rainfall_obs import RainfallObsUnavailable


def _payload():
    return {
        "stations": [
            {"id": "S1", "name": "In The Box", "location": {"latitude": 1.303, "longitude": 103.835}},
            {"id": "S2", "name": "Just Outside", "location": {"latitude": 1.33, "longitude": 103.86}},
            {"id": "S3", "name": "Far North", "location": {"latitude": 1.45, "longitude": 103.80}},
        ],
        "readings": [{"timestamp": "2026-10-12T22:30:00+08:00",
                      "data": [{"stationId": "S1", "value": 2.4},
                               {"stationId": "S2", "value": 11.8},
                               {"stationId": "S3", "value": 0.0}]}],
        "readingType": "TB1 Rainfall 5 Minute Total F", "readingUnit": "mm",
    }


BOX = (103.825, 1.295, 103.845, 1.312)   # Orchard Road


def test_gauges_are_ranked_by_nearness_to_the_box_centre():
    out = rainfall_obs.near(BOX, data=_payload())
    assert out["available"] is True
    assert [g["name"] for g in out["nearest"]] == ["In The Box", "Just Outside", "Far North"]
    assert out["nearest"][0]["in_box"] is True


def test_the_wettest_nearby_gauge_is_picked_not_the_wettest_anywhere():
    # S2 is wetter than S1 and within 10 km, so it is the wettest-nearby; S3 is far and dry.
    out = rainfall_obs.near(BOX, data=_payload())
    assert out["wettest_nearby"]["name"] == "Just Outside"
    assert out["wettest_nearby"]["mm_5min"] == 11.8
    assert out["unit"] == "mm per 5 min"
    assert out["gauges_reporting"] == 3


def test_a_gauge_with_no_reading_is_dropped():
    p = _payload()
    p["readings"][0]["data"] = [d for d in p["readings"][0]["data"] if d["stationId"] != "S2"]
    out = rainfall_obs.near(BOX, data=p)
    assert all(g["name"] != "Just Outside" for g in out["nearest"])
    assert out["gauges_reporting"] == 2


def test_no_readings_at_all_is_an_honest_failure():
    with pytest.raises(RainfallObsUnavailable):
        rainfall_obs.near(BOX, data={"stations": [], "readings": []})
