"""Offline tests for the OSM water layer: parsing, rasterising, and what it does to the screening."""
import numpy as np
from pyproj import Transformer

from bih_engine import analyse_grid, parse_overpass, rasterize_layers
from helpers import dig, make_grid, tilted_plane

TO_LL = Transformer.from_crs(32643, 4326, always_xy=True)


def ll(col, row):
    """Grid (col, row) in cells, as an Overpass geometry node. Origin and 10 m cells match make_grid."""
    lon, lat = TO_LL.transform(500_000 + 10 * col, 1_100_000 - 10 * row)
    return {"lon": lon, "lat": lat}


def way(i, tags, pts):
    return {"type": "way", "id": i, "tags": tags, "geometry": [ll(c, r) for c, r in pts]}


def ring(c0, r0, c1, r1):
    return [(c0, r0), (c1, r0), (c1, r1), (c0, r1), (c0, r0)]


def canal_into_pit():
    """A canal along row 43 reaching the pit's west rim."""
    return parse_overpass({"elements": [way(1, {"waterway": "canal"}, [(20.5, 43.5), (39.5, 43.5)])]})


def pit_grid():
    return make_grid(dig(tilted_plane(), 40, 46, 40, 46, below=1.5))


def test_parses_water_polygons_and_waterway_lines():
    osm = parse_overpass({"elements": [
        way(1, {"natural": "water", "water": "pond"}, ring(10, 10, 20, 20)),
        way(2, {"landuse": "reservoir"}, ring(30, 30, 40, 40)),
        way(3, {"waterway": "canal"}, [(0, 0), (50, 0)]),
        way(4, {"waterway": "stream"}, [(0, 5), (50, 5)]),
        way(5, {"waterway": "weir"}, [(0, 9), (5, 9)]),                  # not a waterway line we use
        way(6, {"natural": "water"}, [(60, 60), (70, 60), (70, 70)]),    # open ring: not an area
        way(7, {"highway": "residential"}, [(0, 1), (9, 1)]),           # not water
    ]})
    assert len(osm.water) == 2 and len(osm.waterways) == 2
    assert osm.stats["relations_skipped_incomplete"] == 0


def test_multipolygon_keeps_its_holes_and_skips_incomplete_rings():
    rel = {"type": "relation", "id": 9, "tags": {"natural": "water", "type": "multipolygon"}, "members": [
        {"type": "way", "role": "outer", "geometry": [ll(c, r) for c, r in ring(0, 0, 40, 40)]},
        {"type": "way", "role": "inner", "geometry": [ll(c, r) for c, r in ring(10, 10, 20, 20)]},
    ]}
    broken = {"type": "relation", "id": 10, "tags": {"natural": "water", "type": "multipolygon"}, "members": [
        {"type": "way", "role": "outer", "geometry": [ll(c, r) for c, r in [(0, 0), (40, 0), (40, 40)]]},  # never closes
    ]}
    stream = {"type": "relation", "id": 11, "tags": {"natural": "water", "type": "waterway"}, "members": []}
    osm = parse_overpass({"elements": [rel, broken, stream]})
    assert len(osm.water) == 1 and osm.stats["relations_skipped_incomplete"] == 1  # a waterway relation is not counted
    lay = rasterize_layers(osm, make_grid(np.full((60, 60), 5.0)))
    assert lay.water[30, 30] and not lay.water[15, 15]  # island in the middle stays dry


def test_a_line_narrower_than_a_cell_still_forms_a_continuous_path():
    osm = parse_overpass({"elements": [way(1, {"waterway": "drain"}, [(5.2, 7.5), (44.8, 31.5)])]})  # diagonal
    lay = rasterize_layers(osm, make_grid(np.full((60, 60), 5.0)))
    rows, cols = np.nonzero(lay.waterway)
    assert len(rows) >= 40 and not lay.water.any()
    covered = sorted(set(cols))
    assert covered == list(range(covered[0], covered[-1] + 1))


def test_features_outside_the_grid_are_ignored():
    osm = parse_overpass({"elements": [way(1, {"natural": "water"}, ring(500, 500, 520, 520))]})
    lay = rasterize_layers(osm, make_grid(np.full((60, 60), 5.0)))
    assert not lay.water.any() and not lay.waterway.any()


def test_a_pit_beside_a_canal_drains_into_it():
    base = analyse_grid(pit_grid(), 100)
    with_canal = analyse_grid(pit_grid(), 100, layers=rasterize_layers(canal_into_pit(), pit_grid()))
    assert len(base["debug"]) == 1       # holds water on its own
    assert with_canal["debug"] == []     # a canal cut 2 m into the bank drains it


def test_the_burn_is_what_lets_a_canal_drain_the_land_beside_it():
    lay = rasterize_layers(canal_into_pit(), pit_grid())
    assert len(analyse_grid(pit_grid(), 100, layers=lay, burn_waterway_m=0.0)["debug"]) == 1


def test_open_water_over_a_pit_removes_it():
    pond = parse_overpass({"elements": [way(1, {"natural": "water", "water": "pond"}, ring(42, 42, 45, 45))]})
    res = analyse_grid(pit_grid(), 100, layers=rasterize_layers(pond, pit_grid()))
    assert res["debug"] == []


def test_osm_use_is_disclosed_in_the_result():
    plain = analyse_grid(pit_grid(), 100)
    assert plain["screening"]["confidence"]["water_bodies"] == "terrain_only" and plain["grid"]["osm"] is None
    res = analyse_grid(pit_grid(), 100, layers=rasterize_layers(canal_into_pit(), pit_grid()))
    assert res["screening"]["confidence"]["water_bodies"] == "osm"
    assert res["grid"]["osm"]["waterway_cells"] >= 20
    assert res["screening"]["confidence"]["drains"] == "gap"  # canals are not storm drains
