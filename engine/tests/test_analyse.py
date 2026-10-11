import json

import numpy as np

from bih_engine import analyse_grid
from helpers import dig, make_grid, tilted_plane

CONTRACT_KEYS = {"depressions", "routes_affected", "confidence", "caveat"}


def two_pits():
    dem = tilted_plane()
    dem = dig(dem, 15, 21, 20, 26, below=1.5)  # A: far downstream, big catchment
    dem = dig(dem, 65, 71, 50, 56, below=1.5)  # B: further upstream, smaller catchment
    return dem


def test_bigger_catchment_fills_first():
    res = analyse_grid(make_grid(two_pits()), rainfall_mm=100)
    deps = res["screening"]["depressions"]
    assert len(deps) == 2
    first, second = deps
    assert first["fill_order"] == 1 and second["fill_order"] == 2
    assert first["contributing_ha"] > second["contributing_ha"]
    assert first["pooling_index"] > second["pooling_index"]
    # Pit A sits in the north-west (low row, low col): check it is the one ranked first.
    d = res["debug"]
    assert d[0]["row"] < 30 and d[1]["row"] > 50


def test_output_follows_the_plan_contract():
    res = analyse_grid(make_grid(two_pits()))
    s = res["screening"]
    assert CONTRACT_KEYS <= s.keys()
    assert {"terrain", "drains", "validated_layer"} <= s["confidence"].keys()
    assert "Relative ranking only" in s["caveat"]
    assert all(1 <= d["pooling_index"] <= 5 for d in s["depressions"])


def _keys(obj):
    if isinstance(obj, dict):
        for k, v in obj.items():
            yield k
            yield from _keys(v)
    elif isinstance(obj, list):
        for v in obj:
            yield from _keys(v)


def test_no_absolute_depth_leaks_into_the_result():
    res = analyse_grid(make_grid(two_pits()))
    keys = set(_keys({"screening": res["screening"], "geojson": res["geojson"]}))
    leaked = {k for k in keys if "depth" in k.lower() or k.lower().endswith(("_m", "_m2", "_m3"))}
    assert not leaked, leaked


def test_geojson_matches_screening_and_is_in_lonlat():
    res = analyse_grid(make_grid(two_pits()))
    feats = res["geojson"]["features"]
    assert {f["properties"]["id"] for f in feats} == {d["id"] for d in res["screening"]["depressions"]}
    lon, lat = feats[0]["geometry"]["coordinates"][0][0]
    assert 60 < lon < 90 and 5 < lat < 15  # UTM 43N near Kochi, converted back to degrees


def test_shallow_noise_is_flagged_low_confidence():
    dem = dig(tilted_plane(), 40, 46, 40, 46, below=0.35)  # shallower than the 0.45 m DEM error
    res = analyse_grid(make_grid(dem))
    assert res["debug"] and all(d["low_confidence"] for d in res["debug"])


def test_tiny_features_below_one_source_pixel_are_dropped():
    dem = dig(tilted_plane(), 40, 42, 40, 42, below=2.0)  # 4 cells = 400 m2 < 900 m2
    res = analyse_grid(make_grid(dem))
    assert res["screening"]["depressions"] == []


def test_rainfall_only_changes_which_spots_overflow_not_the_ranking():
    grid = make_grid(two_pits())
    light = analyse_grid(grid, rainfall_mm=5)
    heavy = analyse_grid(grid, rainfall_mm=500)
    assert [d["id"] for d in light["screening"]["depressions"]] == [d["id"] for d in heavy["screening"]["depressions"]]
    assert not any(d["fills_at_rainfall"] for d in light["debug"])
    assert all(d["fills_at_rainfall"] for d in heavy["debug"])


def test_flat_nodata_edge_does_not_crash():
    dem = two_pits()
    dem[:, :5] = np.nan
    res = analyse_grid(make_grid(dem))
    assert res["screening"]["depressions"]
