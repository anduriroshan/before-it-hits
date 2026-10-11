"""Offline tests for the validated tier: banding, clipping, and the agreement statistic."""
import json

import numpy as np
import pytest
import rasterio
from rasterio.transform import from_origin

from bih_engine.official import BANDS_CM, compare_to_screening, read_official
from bih_engine.store import Region

BBOX = (76.30, 9.99, 76.32, 10.01)
BBOX_WITH_DRY = (76.292, 9.99, 76.32, 10.01)


@pytest.fixture
def region(tmp_path):
    """A region whose official raster is a known staircase: deeper towards the east."""
    (tmp_path / "official").mkdir()
    n, px = 60, 1 / 1200
    depth = np.tile(np.linspace(5, 600, n, dtype="float32"), (n, 1))
    depth[:, :10] = -9999.0  # a dry western strip
    for rp in (10, 50):
        a = depth * (1.0 if rp == 10 else 1.4)   # deeper at the longer return period
        a[depth == -9999.0] = -9999.0
        with rasterio.open(tmp_path / "official" / f"flood_{rp}yr_historical.tif", "w", driver="GTiff",
                           height=n, width=n, count=1, dtype="float32", crs="EPSG:4326",
                           transform=from_origin(76.29, 10.02, px, px), nodata=-9999.0) as ds:
            ds.write(a, 1)
    (tmp_path / "region.json").write_text(json.dumps({
        "name": "test", "title": "Test", "bbox_wsen": [76.28, 9.97, 76.34, 10.03],
        "terrain": {"file": "dtm.tif"}, "vectors": {"source": "x", "licence": "y", "counts": {}},
        "official": {
            "source": "Test authority", "title": "Test flood layer", "scenario": "historical",
            "return_periods": [10, 50], "units": "cm (inferred)", "native_res_m_approx": 92,
            "layers": {"10": {"file": "flood_10yr_historical.tif"},
                       "50": {"file": "flood_50yr_historical.tif"}},
            "caveats": ["Displayed as published."],
        },
    }), encoding="utf-8")
    return Region.load(tmp_path)


def test_reads_the_requested_return_period_and_reports_provenance(region):
    o = read_official(region, BBOX, 10)
    assert o["return_period_years"] == 10
    assert o["source"] == "Test authority" and "inferred" in o["units"]
    assert o["geojson"]["features"] and o["caveats"]


def test_a_longer_return_period_is_deeper_and_the_layers_stay_separate(region):
    a, b = read_official(region, BBOX, 10), read_official(region, BBOX, 50)
    assert b["deepest"] > a["deepest"]
    assert b["median_where_wet"] > a["median_where_wet"]


def test_dry_cells_are_not_banded(region):
    o = read_official(region, BBOX_WITH_DRY, 10)
    # The western strip is nodata, so the inundated share must be well under the whole box.
    assert 0 < o["inundated_share"] < 1.0
    assert read_official(region, BBOX, 10)["inundated_share"] == 1.0  # all wet east of the strip


def test_every_feature_lands_in_a_declared_band(region):
    o = read_official(region, BBOX, 10)
    bands = {f["properties"]["band"] for f in o["geojson"]["features"]}
    assert bands and bands <= set(range(len(BANDS_CM)))
    for f in o["geojson"]["features"]:
        assert f["properties"]["label"] == o["band_labels"][f["properties"]["band"]]


def test_an_unpublished_return_period_is_refused(region):
    with pytest.raises(ValueError, match="77"):
        read_official(region, BBOX, 77)


def test_a_region_with_no_official_layer_returns_nothing(region):
    region.manifest.pop("official")
    assert read_official(region, BBOX, 10) is None


def square(lon, lat, d=0.002, index=5):
    return {"type": "Feature", "properties": {"pooling_index": index}, "geometry": {
        "type": "Polygon", "coordinates": [[[lon, lat], [lon + d, lat], [lon + d, lat + d],
                                            [lon, lat + d], [lon, lat]]]}}


def test_agreement_is_measured_against_chance_not_raw_overlap(region):
    o = read_official(region, BBOX, 10)
    # Two top-ranked spots in the wet east, one in the dry west.
    fc = {"type": "FeatureCollection",
          "features": [square(76.315, 10.00), square(76.317, 10.00), square(76.2905, 10.00)]}
    a = compare_to_screening(o, fc)
    assert a["checked"] == 3 and a["inside"] >= 2
    assert a["enrichment"] == pytest.approx(a["share"] / a["baseline"], abs=0.005)  # reported to 2 dp


def test_low_ranked_spots_are_not_counted(region):
    o = read_official(region, BBOX, 10)
    fc = {"type": "FeatureCollection", "features": [square(76.315, 10.00, index=2)]}
    assert compare_to_screening(o, fc)["checked"] == 0


def test_a_mostly_inundated_box_is_flagged_as_not_discriminating(region):
    o = read_official(region, BBOX, 10)
    o["inundated_share"] = 0.8
    a = compare_to_screening(o, {"type": "FeatureCollection", "features": [square(76.315, 10.00)]})
    assert a["discriminating"] is False and "no longer separates" in a["note"]


def test_no_official_layer_means_no_agreement_claim(region):
    assert compare_to_screening(None, {"type": "FeatureCollection", "features": [square(76.315, 10.0)]}) is None
