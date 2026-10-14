"""The population grid: offline, on a made-up grid where every count has a known right answer.

Ten pixels a side. Cell A holds 1000 modelled people and two buildings, one four times the other's
roof. Cell B holds 1000 people and no mapped building. Cell C holds a building and nobody. The published
total is four times the grid's, so every cell is scaled by four. What matters is what the method refuses
to do: put people where the grid has none, hand a cell's people to a spot that covers only open ground in
it, or lose the people in a cell that OpenStreetMap has not mapped.
"""
import numpy as np
import pytest
import rasterio
from rasterio.transform import from_origin
from shapely.geometry import box

from bih_engine import exposure, popgrid
from bih_engine.osm import OsmData

W, N = 76.30, 10.02
PX = 1 / 1200
SIZE = 10
BBOX = (W, N - SIZE * PX, W + SIZE * PX, N)
A, B, C = (2, 2), (2, 6), (6, 4)                       # (row, col)


def cell(rc, x0, y0, x1, y1):
    """A rectangle inside a cell, given as fractions of it from its south-west corner."""
    r, c = rc
    west, south = W + c * PX, N - (r + 1) * PX
    return box(west + x0 * PX, south + y0 * PX, west + x1 * PX, south + y1 * PX)


BIG, SMALL = cell(A, 0.1, 0.1, 0.5, 0.9), cell(A, 0.6, 0.1, 0.8, 0.5)   # roofs in the ratio 4 : 1
ONLY = cell(C, 0.2, 0.2, 0.8, 0.8)


class _Region:
    def __init__(self, root, buildings=(BIG, SMALL, ONLY), published=8000):
        self.root = root
        self.bounds = BBOX
        self._b = list(buildings)
        self.manifest = {"population": {"people": published, "source": "a census", "extent": "a city"},
                         "population_grid": {"file": "pop.tif", "source": "a grid"}}

    def read_osm(self, bbox):
        out, win = OsmData(), box(*bbox)
        out.buildings = [b for b in self._b if b.intersects(win)]
        return out


def _stage(tmp_path, **kw):
    a = np.full((SIZE, SIZE), np.nan, dtype="float32")
    a[A], a[B], a[C] = 1000.0, 1000.0, 0.0
    with rasterio.open(tmp_path / "pop.tif", "w", driver="GTiff", height=SIZE, width=SIZE, count=1,
                       dtype="float32", crs="EPSG:4326", transform=from_origin(W, N, PX, PX),
                       nodata=float("nan")) as dst:
        dst.write(a, 1)
    return _Region(tmp_path, **kw)


def test_a_region_with_no_grid_or_no_published_total_is_not_available(tmp_path):
    assert popgrid.available(_Region(tmp_path)) is False             # entry present, file never written
    region = _stage(tmp_path)
    assert popgrid.available(region) is True
    region.manifest["population"] = {}
    assert popgrid.available(region) is False


def test_the_grid_is_scaled_so_the_city_adds_up_to_its_published_total(tmp_path):
    out = popgrid.raster(_stage(tmp_path), BBOX, cells=SIZE)
    assert out["available"] is True and out["people_in_area"] == 8000          # 2000 in the grid, scaled by four
    assert out["city_people"] == 8000


def test_the_raster_runs_south_first_and_leaves_empty_ground_null(tmp_path):
    out = popgrid.raster(_stage(tmp_path), BBOX, cells=SIZE)
    v = out["values"]
    at = lambda rc: v[(SIZE - 1 - rc[0]) * SIZE + rc[1]]                       # noqa: E731
    assert at(A) > 0 and at(B) > 0
    assert at(C) is None                                                        # a modelled zero is drawn as nothing
    assert at((0, 0)) is None                                                   # no value at all
    assert at(A) == at(B)                                                       # same people, same cell size


def test_a_box_the_grid_has_nobody_in_says_so(tmp_path):
    region = _stage(tmp_path)
    out = popgrid.raster(region, (W, N - SIZE * PX, W + 1.5 * PX, N - 8 * PX), cells=1)
    assert out["available"] is False and "no people" in out["reason"]


def test_people_follow_the_roof_inside_a_cell(tmp_path):
    region = _stage(tmp_path)
    whole_big = popgrid.people_in(region, [BIG])
    assert whole_big["mapped"] == pytest.approx(3200)                           # 4000 people x 4/5 of the roof
    assert whole_big["unmapped"] == 0 and whole_big["buildings"] == 1
    half_big = popgrid.people_in(region, [cell(A, 0.1, 0.1, 0.3, 0.9)])
    assert half_big["mapped"] == pytest.approx(1600)                            # half the building, half its people
    both = popgrid.people_in(region, [BIG, SMALL])
    assert both["people"] == pytest.approx(4000)                                # the cell's people, no more


def test_a_spot_over_open_ground_in_a_mapped_cell_holds_nobody(tmp_path):
    region = _stage(tmp_path)
    out = popgrid.people_in(region, [cell(A, 0.82, 0.6, 0.98, 0.98)])           # no roof here
    assert out["people"] == 0


def test_people_in_a_cell_with_no_mapped_building_are_spread_over_the_cell_not_lost(tmp_path):
    region = _stage(tmp_path)
    half = popgrid.people_in(region, [cell(B, 0, 0, 0.5, 1)])
    assert half["unmapped"] == pytest.approx(2000) and half["mapped"] == 0      # half of 4000
    assert half["buildings"] == 0


def test_a_building_in_a_cell_with_nobody_is_given_nobody(tmp_path):
    assert popgrid.people_in(_stage(tmp_path), [ONLY])["people"] == 0


def test_the_cells_people_are_shared_among_every_building_not_just_those_a_spot_touches(tmp_path):
    # With SMALL unmapped the cell's whole 4000 would fall on BIG; with it mapped BIG gets 4/5.
    alone = popgrid.people_in(_stage(tmp_path, buildings=(BIG,)), [BIG])["mapped"]
    shared = popgrid.people_in(_stage(tmp_path), [BIG])["mapped"]
    assert alone == pytest.approx(4000) and shared == pytest.approx(3200)


def test_the_exposure_estimate_reads_the_grid_when_the_region_has_one(tmp_path):
    region = _stage(tmp_path)
    spot = {"type": "Feature", "geometry": BIG.__geo_interface__,
            "properties": {"id": "d_1", "pooling_index": 5, "fills_at_rainfall": True}}
    out = exposure.estimate([spot], [], "EPSG:32643", region.manifest["population"], region=region, bbox=BBOX)
    assert out["available"] is True and out["method"] == "grid"
    assert out["people_in_worst_spots"] == 3200 and out["people_in_box"] == 8000
    assert "building" in " ".join(out["caveats"]).lower()
    assert "not checked against a census" in out["caveats"][-1].lower()         # no check staged: said so
    region.manifest["population_grid"]["validation"] = {
        "against": "Census of Population 2020, resident population by planning area (SingStat)",
        "grid": {"share_of_people_in_the_wrong_planning_area": 0.07},
        "footprint_only": {"share_of_people_in_the_wrong_planning_area": 0.336}}
    checked = exposure.estimate([spot], [], "EPSG:32643", region.manifest["population"], region=region,
                                bbox=BBOX)["caveats"][-1]
    assert "about 7%" in checked and "about 34%" in checked and "residents only" in checked
    treated = exposure.estimate([spot], [], "EPSG:32643", region.manifest["population"], treated={"d_1"},
                                region=region, bbox=BBOX)
    assert treated["people_in_worst_spots"] == 0 and treated["people_in_box"] == 8000


def test_a_region_without_a_grid_still_uses_the_footprint_method(tmp_path):
    region = _Region(tmp_path)                                                  # no pop.tif written
    spot = {"type": "Feature", "geometry": BIG.__geo_interface__,
            "properties": {"id": "d_1", "pooling_index": 5, "fills_at_rainfall": True}}
    pop = dict(region.manifest["population"], built_footprint_m2=1e6)
    out = exposure.estimate([spot], [BIG, SMALL], "EPSG:32643", pop, region=region, bbox=BBOX)
    assert out["available"] is True and out["method"] == "footprint"


def test_density_for_falls_back_to_footprints_where_no_grid_is_staged(tmp_path):
    region = _Region(tmp_path)
    region.manifest["population"]["built_footprint_m2"] = 1e6
    out = popgrid.density_for(region, BBOX)
    assert out["available"] is True and "footprints" in out["method"]
    assert popgrid.density_for(_stage(tmp_path), BBOX)["method"].startswith("A modelled population grid")
