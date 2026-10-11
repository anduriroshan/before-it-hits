import numpy as np
from pyproj import CRS
from rasterio.transform import from_origin

from bih_engine.terrain import Grid


def make_grid(dem: np.ndarray, res_m: float = 10.0, source_res_m: float = 30.0) -> Grid:
    """A synthetic grid on UTM 43N. Row 0 is north, like the real reader."""
    return Grid(
        dem=dem.astype(np.float32),
        transform=from_origin(500_000.0, 1_100_000.0, res_m, res_m),
        crs=CRS.from_epsg(32643),
        res_m=res_m,
        source_res_m=source_res_m,
        meta={"source": "synthetic"},
    )


def tilted_plane(h=100, w=100, slope_per_cell=0.05):
    """East is higher, so water runs west along each row."""
    return 10.0 + slope_per_cell * np.tile(np.arange(w, dtype=float), (h, 1))


def dig(dem, r0, r1, c0, c1, below):
    """Dig a flat-bottomed pit `below` metres under the terrain at its centre."""
    out = dem.copy()
    out[r0:r1, c0:c1] = dem[(r0 + r1) // 2, (c0 + c1) // 2] - below
    return out
