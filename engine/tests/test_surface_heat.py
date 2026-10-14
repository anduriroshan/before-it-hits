"""The surface-heat reading: offline, on a made-up composite.

The composite is a flat 35 degree plain with one hot patch, one cool patch and a strip of water, so
every assertion has a known right answer. What matters is less the arithmetic than what the reading
refuses to do: ground with no satellite value stays empty rather than becoming "cold", the colour
scale is the city's rather than the box's so a flat neighbourhood looks flat, and a box with one hot
patch is not padded out to three "hottest places" with ordinary ground.
"""
import json

import numpy as np
import pytest
import rasterio
from rasterio.transform import from_origin
from shapely.geometry import LineString

from bih_agents import coordinator, facts
from bih_engine import surface_heat as sh
from bih_engine.osm import OsmData, Road

W, S, E, N = 76.30, 10.00, 76.34, 10.04            # about 4.4 km square
PX = 1 / 3600
SIZE = int(round(0.04 / PX))                        # 144 pixels a side
HOT = (slice(20, 35), slice(100, 115))              # rows, cols: a 460 m patch in the north-east
COOL = (slice(100, 115), slice(10, 25))             # in the south-west
WATER = slice(60, 71)                               # columns: a strip with no reading
CITY = {"p2_c": 32.0, "p98_c": 40.0, "median_c": 35.0}
HOT_LON, HOT_LAT = W + 107.5 * PX, N - 27.5 * PX    # the middle of the hot patch


def _composite():
    a = np.full((SIZE, SIZE), 35.0, dtype="float32")
    a[HOT] = 41.0
    a[COOL] = 31.0
    a[:, WATER] = np.nan
    return a


class _Region:
    """The three things surface_heat asks of a region."""

    def __init__(self, root, roads=()):
        self.root = root
        self.bounds = (W, S, E, N)
        self._roads = list(roads)
        self.manifest = {"surface_heat": {
            "file": "lst.tif", **CITY, "source": "a satellite", "season": "the dry months",
            "scenes": 12, "passes_about_local_hour": 10.7, "composite": "median of clear views",
            "native_res_m": 100, "resolution": "sensor about 100 m, delivered on a 30 m grid",
            "unit": "degrees C of the SURFACE, not the air", "caveats": ["typical, not today"]}}

    def read_osm(self, bbox):
        out = OsmData()
        out.roads = self._roads
        return out


def _stage(tmp_path, roads=(), array=None):
    a = _composite() if array is None else array
    with rasterio.open(tmp_path / "lst.tif", "w", driver="GTiff", height=a.shape[0], width=a.shape[1],
                       count=1, dtype="float32", crs="EPSG:4326",
                       transform=from_origin(W, N, PX, PX), nodata=float("nan")) as dst:
        dst.write(a, 1)
    return _Region(tmp_path, roads)


def _index(out, row, col):
    """Where a source pixel lands in the output, which runs from the south edge up."""
    return (out["rows"] - 1 - row) * out["cols"] + col


def test_a_city_with_nothing_staged_says_so(tmp_path):
    region = _Region(tmp_path)
    region.manifest.pop("surface_heat")
    out = sh.read(region)
    assert out["available"] is False and "staged" in out["reason"]


def test_a_manifest_entry_without_its_file_is_not_available(tmp_path):
    region = _Region(tmp_path)                       # entry present, lst.tif never written
    assert sh.available(region) is False


def test_values_run_south_first_and_ground_with_no_reading_stays_empty(tmp_path):
    out = sh.read(_stage(tmp_path))
    assert (out["rows"], out["cols"]) == (SIZE, SIZE)
    hot_i, cool_i = _index(out, 27, 107), _index(out, 107, 17)
    assert out["values"][hot_i] == 41.0              # the hot patch, up in the north
    assert out["values"][cool_i] == 31.0             # the cool patch, down in the south
    assert cool_i < hot_i                            # so the southern one comes first in the list
    assert out["values"][_index(out, 50, 65)] is None   # water: null, never zero and never cold


def test_the_scale_is_the_citys_however_small_the_box(tmp_path):
    region = _stage(tmp_path)
    box = (W + 100 * PX, N - 35 * PX, W + 115 * PX, N - 20 * PX)       # exactly the hot patch
    out = sh.read(region, box)
    assert out["median_c"] == 41.0
    # All 41 degrees, yet the scale is still the city's 32 to 40, so it reads as the top of the range
    # and not as the whole range.
    assert (out["min"], out["max"]) == (CITY["p2_c"], CITY["p98_c"])
    assert out["vs_city_median_c"] == 6.0


def test_a_big_window_is_block_averaged_and_still_lines_up(tmp_path, monkeypatch):
    monkeypatch.setattr(sh, "MAX_CELLS", 48)
    out = sh.read(_stage(tmp_path))
    assert (out["rows"], out["cols"]) == (48, 48)
    w, s, e, n = out["bbox"]
    assert (w, round(e, 6), round(n, 6)) == (W, round(E, 6), round(N, 6))
    assert out["cell_m"] == pytest.approx(3 * 30.9, abs=3)
    # A block wholly inside the hot patch is that patch's value; one wholly in the water has none.
    assert out["values"][_index(out, 9, 35)] == 41.0
    assert out["values"][_index(out, 17, 21)] is None


def test_the_typical_range_is_the_boxs_own_percentiles(tmp_path):
    out = sh.read(_stage(tmp_path))
    assert out["median_c"] == 35.0
    assert out["typical_low_c"] <= 35.0 <= out["typical_high_c"]
    assert out["vs_city_median_c"] == 0.0
    assert 0 < out["land_share"] < 1                 # the water strip is not land


def test_a_box_that_is_all_water_has_no_reading(tmp_path):
    out = sh.read(_stage(tmp_path), (W + 62 * PX, S, W + 68 * PX, N))
    assert out["available"] is False and "no land" in out["reason"]


def test_hottest_and_coolest_places_are_named_by_the_road_beside_them(tmp_path):
    named = Road(geom=LineString([(HOT_LON, HOT_LAT - 0.004), (HOT_LON, HOT_LAT + 0.004)]),
                 highway="primary", name="Test Road")
    # Closer than the named road, but unnamed: it would win on distance and name nothing.
    unnamed = Road(geom=LineString([(HOT_LON + 0.0001, HOT_LAT - 0.004), (HOT_LON + 0.0001, HOT_LAT + 0.004)]),
                   highway="residential", name=None)
    out = sh.read(_stage(tmp_path, roads=[unnamed, named]), places=True)

    hot = out["hottest"]
    assert len(hot) == 1                             # one hot patch, so one place, not three
    assert hot[0]["surface_c"] == pytest.approx(41.0, abs=0.1)
    assert hot[0]["vs_box_median_c"] == pytest.approx(6.0, abs=0.1)
    assert W + 100 * PX <= hot[0]["lon"] <= W + 115 * PX     # and it is on the patch
    assert N - 35 * PX <= hot[0]["lat"] <= N - 20 * PX
    assert hot[0]["nearest_road"]["name"] == "Test Road"
    assert hot[0]["nearest_road"]["distance_m"] < sh.NEAREST_ROAD_MAX_M

    cool = out["coolest"]
    assert len(cool) == 1 and cool[0]["surface_c"] == pytest.approx(31.0, abs=0.1)
    assert cool[0]["nearest_road"] is None           # no road near the cool patch: said, not guessed


def test_a_flat_box_has_no_hottest_place_to_name(tmp_path):
    flat = np.full((SIZE, SIZE), 35.0, dtype="float32")
    out = sh.read(_stage(tmp_path, array=flat), places=True)
    assert out["hottest"] == [] and out["coolest"] == []


def test_places_are_only_worked_out_when_asked_for(tmp_path):
    out = sh.read(_stage(tmp_path))
    assert "hottest" not in out and "coolest" not in out


def test_the_reading_carries_what_it_is_not(tmp_path):
    out = sh.read(_stage(tmp_path))
    assert "SURFACE" in out["unit"] and "not the air" in out["unit"]
    assert any("not today" in c for c in out["caveats"])
    assert "not a forecast" in out["method"]
    # The grid is finer than the instrument, and the reading says so rather than implying 30 m detail.
    assert out["native_res_m"] == 100 and out["native_res_m"] > out["cell_m"]


def test_two_separate_hot_patches_are_two_places_and_one_patch_is_one(tmp_path):
    a = _composite()
    a[20:35, 20:35] = 41.0                           # a second patch, 80 px (about 2.4 km) west
    out = sh.read(_stage(tmp_path, array=a), places=True)
    assert len(out["hottest"]) == 2
    apart = abs(out["hottest"][0]["lon"] - out["hottest"][1]["lon"]) * sh.M_PER_DEG_LON
    assert apart >= sh.SEPARATION_M
    json.dumps(out)                                  # all of it has to survive the wire


# ---- what the agents are shown ---------------------------------------------------------------


def _reading(hot_delta=2.1, **kw):
    base = {"available": True, "typical_low_c": 33.4, "typical_high_c": 39.1, "median_c": 37.2,
            "city_median_c": 35.1, "vs_city_median_c": hot_delta, "season": "the dry months",
            "hottest": [{"surface_c": 41.6, "vs_box_median_c": 4.4,
                         "nearest_road": {"name": "Stadium Round", "distance_m": 70}},
                        {"surface_c": 40.5, "vs_box_median_c": 3.3, "nearest_road": None}],
            "coolest": [{"surface_c": 31.6, "vs_box_median_c": -5.6,
                         "nearest_road": {"name": "Towerline East Lane", "distance_m": 138}}]}
    base.update(kw)
    return base


def test_the_bundle_says_what_the_surface_picture_is_not_before_anything_else():
    bundle = {}
    facts.add_surface_heat(bundle, _reading())
    block = bundle["surface_heat"]
    text = block["what_this_is"].lower()
    assert "ground" in text and "not the air" in text and "not today" in text
    assert "never describes a single building" in text
    assert "can never be compared with the heat index" in text


def test_the_descriptive_text_adds_no_figures_to_the_set_a_briefing_may_quote():
    bundle = {}
    facts.add_surface_heat(bundle, _reading())
    block = bundle["surface_heat"]
    for key in ("what_this_is", "note", "season", "source"):
        assert facts._numbers_in(block[key]) == set(), key


def test_places_are_named_by_road_and_a_missing_road_is_said_not_invented():
    bundle = {}
    facts.add_surface_heat(bundle, _reading())
    hot, cool = bundle["surface_heat"]["hottest_places"], bundle["surface_heat"]["coolest_places"]
    assert hot[0]["near"] == "Stadium Round, about 70 m away"
    assert hot[1]["near"] == "no named road close by"
    assert hot[0]["hotter_than_the_box_typical_by_c"] == 4.4
    assert cool[0]["cooler_than_the_box_typical_by_c"] == 5.6       # a magnitude, not a minus sign


def test_the_box_is_said_hotter_or_cooler_than_the_city_never_both_and_never_when_equal():
    hotter, cooler, same = {}, {}, {}
    facts.add_surface_heat(hotter, _reading(hot_delta=2.1))
    facts.add_surface_heat(cooler, _reading(hot_delta=-1.4))
    facts.add_surface_heat(same, _reading(hot_delta=0.0))
    assert hotter["surface_heat"]["this_box_runs_hotter_than_the_whole_city_by_c"] == 2.1
    assert "this_box_runs_cooler_than_the_whole_city_by_c" not in hotter["surface_heat"]
    assert cooler["surface_heat"]["this_box_runs_cooler_than_the_whole_city_by_c"] == 1.4
    assert not any(k.startswith("this_box_runs") for k in same["surface_heat"])


def test_nothing_is_added_when_there_is_no_reading():
    for empty in (None, {}, {"available": False, "reason": "no land"}):
        bundle = {}
        facts.add_surface_heat(bundle, empty)
        assert bundle == {}


def test_every_figure_a_briefing_may_quote_traces_to_the_reading():
    bundle = {}
    facts.add_surface_heat(bundle, _reading())
    allowed = facts.allowed_numbers(bundle)
    for quoted in (33.4, 39.1, 37.2, 35.1, 41.6, 4.4, 5.6, 70):
        assert facts.traces_to_facts(quoted, allowed), quoted
    assert not facts.traces_to_facts(44.0, allowed)                 # a figure nobody supplied
    facts.assert_no_own_depth(bundle)


def test_the_coordinator_adds_the_picture_for_a_box_and_traces_it(tmp_path):
    region = _stage(tmp_path)
    bundle, trace = {}, coordinator.Trace()
    coordinator._add_surface_heat(bundle, region, (W, S, E, N), trace)
    assert "surface_heat" in bundle
    assert any(s["agent"] == "Surface heat" and s["status"] == "ok" for s in trace.steps)
    assert bundle["surface_heat"]["hottest_places"]                  # asked for a box, so it ranks


def test_a_city_with_nothing_staged_is_simply_without_the_picture(tmp_path):
    region = _Region(tmp_path)
    region.manifest.pop("surface_heat")
    bundle, trace = {}, coordinator.Trace()
    coordinator._add_surface_heat(bundle, region, (W, S, E, N), trace)
    assert bundle == {} and trace.steps == []
