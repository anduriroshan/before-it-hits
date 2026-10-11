"""Offline tests for reading a local GeoTIFF source (no network)."""
import numpy as np
import pytest
import rasterio
from rasterio.transform import from_origin

from bih_engine import read_dem
from bih_engine.terrain import TerrainUnavailable

BBOX = (76.30, 9.90, 76.32, 9.92)


@pytest.fixture
def tif(tmp_path):
    # 0.1 deg square at 1 arcsec, sloping west->east from 2 m to 12 m; the SW corner is "sea" (-inf).
    n = 360
    z = np.tile(np.linspace(2, 12, n, dtype=np.float32), (n, 1))
    z[n // 2:, : n // 2] = -np.inf
    path = tmp_path / "dtm.tif"
    with rasterio.open(path, "w", driver="GTiff", height=n, width=n, count=1, dtype="float32", crs="EPSG:4326",
                       transform=from_origin(76.25, 10.00, 1 / 3600, 1 / 3600), nodata=-np.inf) as ds:
        ds.write(z, 1)
    return path


def test_reads_a_local_file_onto_a_metric_grid(tif):
    g = read_dem(BBOX, res_m=10, pad_m=100, source=tif)
    assert g.crs.to_epsg() == 32643 and g.res_m == 10
    assert 25 < g.source_res_m < 35  # 1 arcsecond is about 30 m
    assert 2 < np.nanmin(g.dem) < np.nanmax(g.dem) < 12


def test_non_finite_cells_become_nan_so_they_act_as_outlets(tif):
    box_over_sea = (76.255, 9.905, 76.265, 9.915)  # inside the -inf corner
    g = read_dem(box_over_sea, res_m=10, pad_m=0, source=tif)
    assert np.isnan(g.dem).mean() > 0.9
    assert not np.isinf(g.dem).any()


def test_box_outside_the_file_is_reported(tif):
    with pytest.raises(TerrainUnavailable):
        read_dem((77.0, 9.0, 77.01, 9.01), source=tif)


def test_sidecar_provenance_is_used_as_the_source_label(tif):
    tif.with_suffix(".json").write_text('{"source": "DeltaDTM v1.1 test"}', encoding="utf-8")
    assert read_dem(BBOX, res_m=10, pad_m=100, source=tif).meta["source"] == "DeltaDTM v1.1 test"
