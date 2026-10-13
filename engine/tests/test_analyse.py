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


def by_row(res):
    return sorted(res["debug"], key=lambda d: d["row"])


def test_bigger_catchment_fills_first_and_ponds_more_before_either_is_full():
    grid = make_grid(two_pits())
    a, b = by_row(analyse_grid(grid, rainfall_mm=100))  # A is far downstream (north), B upstream
    assert a["fill_order"] == 1 and b["fill_order"] == 2
    assert a["contrib_m2"] > b["contrib_m2"]
    # At 40 mm neither pit is full, so what has arrived decides the index: A collects more.
    a, b = by_row(analyse_grid(grid, rainfall_mm=40))
    assert not a["fills_at_rainfall"] and not b["fills_at_rainfall"]
    assert a["pooling_index"] > b["pooling_index"]


def small_pit_with_huge_catchment_and_big_pit_with_small_one():
    dem = tilted_plane()
    dem = dig(dem, 20, 24, 6, 10, below=0.3)   # S: tiny and shallow, far downstream, drains 5 ha
    dem = dig(dem, 60, 68, 60, 68, below=2.0)  # L: big and deep, upstream, drains 3.6 ha
    return dem


def test_a_tiny_pit_fills_first_but_does_not_top_the_hazard_ranking():
    res = analyse_grid(make_grid(small_pit_with_huge_catchment_and_big_pit_with_small_one()), rainfall_mm=100)
    s, l = by_row(res)
    assert s["row"] < 30 < l["row"]
    assert s["fill_order"] == 1 and l["fill_order"] == 2          # S needs far less rain to fill
    assert s["fills_at_rainfall"] and not l["fills_at_rainfall"]  # S is full; L is still filling
    assert l["pooling_index"] > s["pooling_index"]                # but L holds far more water
    assert res["debug"][0] is l                                   # and is reported first


def test_ids_and_fill_order_do_not_change_with_rainfall():
    grid = make_grid(two_pits())
    light = analyse_grid(grid, rainfall_mm=5)["debug"]
    heavy = analyse_grid(grid, rainfall_mm=500)["debug"]
    key = lambda ds: {d["id"]: (round(d["row"]), round(d["col"]), d["fill_order"]) for d in ds}
    assert key(light) == key(heavy)
    assert not any(d["fills_at_rainfall"] for d in light)
    assert all(d["fills_at_rainfall"] for d in heavy)


def test_treating_a_spot_takes_its_road_out_of_the_count_and_nothing_else():
    """F10: the 'after' run differs from the 'before' run only in the roads the treated spot cut."""
    from pyproj import Transformer
    from shapely.geometry import LineString
    from bih_engine.osm import Road

    grid = make_grid(small_pit_with_huge_catchment_and_big_pit_with_small_one())
    to_ll = Transformer.from_crs(32643, 4326, always_xy=True)
    lon, lat = to_ll.transform(500_000 + 64.5 * 10, 1_100_000 - 64.5 * 10)      # over the big pit
    road = Road(geom=LineString([(lon - 0.0008, lat), (lon + 0.0008, lat)]),
                highway="residential", name="Test Rd")
    before = analyse_grid(grid, rainfall_mm=1000, roads=[road])
    big = before["debug"][0]["id"]
    assert [r["road"] for r in before["screening"]["routes_affected"]] == ["Test Rd"]

    after = analyse_grid(grid, rainfall_mm=1000, roads=[road], treated={big})
    assert after["screening"]["routes_affected"] == []
    # The terrain, the ranking and the map are the same: only the road count moved.
    assert after["screening"]["depressions"] == before["screening"]["depressions"]
    assert after["geojson"] == before["geojson"]


def test_no_rain_means_nothing_to_rank():
    res = analyse_grid(make_grid(two_pits()), rainfall_mm=0)
    assert {d["pooling_index"] for d in res["screening"]["depressions"]} == {1}


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


def test_flat_nodata_edge_does_not_crash():
    dem = two_pits()
    dem[:, :5] = np.nan
    res = analyse_grid(make_grid(dem))
    assert res["screening"]["depressions"]


def test_a_hole_deeper_than_street_level_ponding_is_excluded_not_ranked():
    dem = two_pits()
    dem = dig(dem, 40, 46, 70, 76, below=7.0)  # a 7 m hole: a water body or a terrain artifact
    res = analyse_grid(make_grid(dem), rainfall_mm=100)
    shown = {d["id"] for d in res["screening"]["depressions"]}
    gone = res["screening"]["excluded"]
    assert len(shown) == 2 and len(gone) == 1
    assert gone[0]["id"] not in shown and "water body" in gone[0]["reason"]
    assert gone[0]["id"] not in {f["properties"]["id"] for f in res["geojson"]["features"]}
    assert len(res["debug_excluded"]) == 1 and res["debug_excluded"][0]["max_depth_m"] > 5


def test_exclusion_does_not_renumber_the_remaining_pits():
    dem = dig(two_pits(), 40, 46, 70, 76, below=7.0)
    excluded = analyse_grid(make_grid(dem), rainfall_mm=100)
    kept_all = analyse_grid(make_grid(dem), rainfall_mm=100, max_plausible_depth_m=50)  # hole not excluded
    ids = lambda r: {d["id"]: (round(d["row"]), round(d["col"])) for d in r["debug"]}
    assert ids(excluded).items() <= ids(kept_all).items()
    assert len(kept_all["debug"]) == len(excluded["debug"]) + 1
