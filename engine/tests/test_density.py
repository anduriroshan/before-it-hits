"""The population density raster: offline, on made-up buildings.

What matters here is not the arithmetic so much as what the raster refuses to say. Ground with no
building on it comes back as null rather than as the bottom of the colour ramp, because "nobody
lives here" and "we have no footprint here" are different statements and only one of them is ours
to make. And the ramp is cut at the 95th percentile, so one dense block cannot flatten a city.
"""
import importlib
import math
import sys
from pathlib import Path

from shapely.geometry import box

_engine_dir = str(Path(__file__).resolve().parents[1] / "bih_engine")
if _engine_dir not in sys.path:
    sys.path.insert(0, _engine_dir)
density = importlib.import_module("density")

BBOX = (76.30, 10.00, 76.32, 10.02)               # about 2.2 km square
POP = {"people": 100000, "built_footprint_m2": 1000000, "source": "a census", "extent": "a city"}


def _buildings_in(w, s, e, n, step=0.002, size=0.0008):
    """A regular scatter of little square footprints across part of the bbox."""
    out = []
    y = s
    while y < n:
        x = w
        while x < e:
            out.append(box(x, y, x + size, y + size))
            x += step
        y += step
    return out


def test_an_area_with_no_population_staged_is_not_guessed_at():
    out = density.raster(BBOX, {"people": 0, "built_footprint_m2": 0}, [])
    assert out["available"] is False and "not staged" in out["reason"]


def test_an_area_with_no_buildings_in_it_says_so_rather_than_returning_zeroes():
    out = density.raster(BBOX, POP, [box(70.0, 5.0, 70.001, 5.001)], cells=8)
    assert out["available"] is False and "no buildings" in out["reason"]


def test_ground_with_no_footprint_is_null_not_the_bottom_of_the_ramp():
    """Half the box built on, half empty. The empty half must not read as 'nobody lives here'."""
    out = density.raster(BBOX, POP, _buildings_in(76.30, 10.00, 76.31, 10.02), cells=10)
    assert out["available"] is True
    assert None in out["values"]                              # the unbuilt half
    assert any(v is not None and v > 0 for v in out["values"])


def test_the_raster_is_square_row_major_and_complete():
    out = density.raster(BBOX, POP, _buildings_in(*BBOX), cells=12)
    assert out["rows"] == 12 and out["cols"] == 12
    assert len(out["values"]) == 144
    assert out["bbox"] == list(BBOX)


def test_the_ramp_is_cut_at_the_95th_percentile_so_one_block_cannot_flatten_a_city():
    """One cell packed with footprint, the rest sparse. p95 has to stay below the peak."""
    builds = _buildings_in(*BBOX, step=0.004, size=0.0003)
    builds += _buildings_in(76.3005, 10.0005, 76.3045, 10.0045, step=0.0004, size=0.00035)
    out = density.raster(BBOX, POP, builds, cells=10)
    assert out["p95"] < out["max"]


def test_density_scales_with_how_much_roof_is_in_the_cell():
    sparse = density.raster(BBOX, POP, _buildings_in(*BBOX, step=0.004, size=0.0004), cells=6)
    dense = density.raster(BBOX, POP, _buildings_in(*BBOX, step=0.004, size=0.0008), cells=6)
    assert dense["max"] > sparse["max"]


def test_a_whole_city_is_capped_so_it_stays_inside_one_request():
    assert density._cells_for((76.20, 9.85, 76.45, 10.10)) == density.TARGET_CELLS


def test_no_area_is_ever_drawn_finer_than_the_method_supports():
    """A smaller box gets fewer cells, not finer ones: the footprint ratio is a city average."""
    for bbox in [(76.30, 10.00, 76.31, 10.01),              # ~1.1 km
                 (76.30, 10.00, 76.33, 10.03),              # ~3.3 km
                 (76.20, 9.85, 76.45, 10.10)]:              # ~28 km
        assert density._side_m(bbox) / density._cells_for(bbox) >= density.MIN_CELL_M


def test_the_reported_cell_size_is_the_one_actually_used():
    """The scale on the map quotes this, so it has to be the real cell, not a nominal one."""
    out = density.raster(BBOX, POP, _buildings_in(*BBOX), cells=10)
    assert abs(out["cell_m"] - density._side_m(BBOX) / 10) < 1


def test_a_box_smaller_than_one_cell_honestly_gets_one_number():
    assert density._cells_for((76.300, 10.000, 76.3008, 10.0008)) == 1


def test_the_method_travels_with_the_numbers():
    out = density.raster(BBOX, POP, _buildings_in(*BBOX), cells=8)
    assert "approximation by area" in out["method"]
    assert "never a count" in out["method"]
    assert out["source"] == "a census" and out["city_people"] == 100000


def test_the_people_in_an_area_are_the_area_s_share_not_the_city_total():
    """A box answered '2 million people' for 19 km2 because the city's whole total was reported."""
    one = [box(76.305, 10.005, 76.3058, 10.0058)]                  # 0.0008 degrees square
    roof_m2 = (0.0008 * 110574) * (0.0008 * 111320 * math.cos(math.radians(10.0054)))
    out = density.raster(BBOX, POP, one, cells=10)
    expected = roof_m2 * POP["people"] / POP["built_footprint_m2"]  # that roof's share of the city
    assert abs(out["people_in_area"] - expected) / expected < 0.03   # two figures: a few percent either way
    assert out["people_in_area"] < out["city_people"]
