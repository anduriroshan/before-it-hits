"""Windowed terrain reads: pull just the requested box out of cloud-optimised tiles and
resample it onto a local metric grid.

The tile base URL is configurable so the same code reads the public Copernicus bucket today
and our own re-hosted store (same naming) later: set BIH_TERRAIN_BASE.
"""
from __future__ import annotations

import math
import os
import time
from dataclasses import dataclass, field

import numpy as np
import rasterio
from rasterio.errors import RasterioIOError
from rasterio.merge import merge
from rasterio.transform import Affine, from_origin
from rasterio.warp import Resampling, reproject
from pyproj import CRS, Transformer

COPERNICUS_BASE = "https://copernicus-dem-30m.s3.amazonaws.com"
_NODATA = -9999.0

_GDAL_OPTS = {
    "GDAL_DISABLE_READDIR_ON_OPEN": "EMPTY_DIR",
    "CPL_VSIL_CURL_ALLOWED_EXTENSIONS": ".tif",
    "GDAL_HTTP_MULTIPLEX": "YES",
    "GDAL_HTTP_MERGE_CONSECUTIVE_RANGES": "YES",
    "VSI_CACHE": "TRUE",
}


class TerrainUnavailable(RuntimeError):
    """No terrain tile covers the requested box (e.g. open ocean)."""


@dataclass
class Grid:
    """Terrain on a local metric grid. Row 0 is north."""

    dem: np.ndarray  # float32, NaN where there is no data
    transform: Affine
    crs: CRS
    res_m: float
    source_res_m: float
    meta: dict = field(default_factory=dict)


def _tile_url(lat: int, lon: int, base: str) -> str:
    ns = "N" if lat >= 0 else "S"
    ew = "E" if lon >= 0 else "W"
    name = f"Copernicus_DSM_COG_10_{ns}{abs(lat):02d}_00_{ew}{abs(lon):03d}_00_DEM"
    return f"{base}/{name}/{name}.tif"


def tiles_for_bounds(west: float, south: float, east: float, north: float, base: str) -> list[str]:
    lats = range(math.floor(south), math.ceil(north))
    lons = range(math.floor(west), math.ceil(east))
    return [_tile_url(lat, lon, base) for lat in lats for lon in lons]


def utm_crs_for(lon: float, lat: float) -> CRS:
    zone = int((lon + 180) // 6) + 1
    return CRS.from_epsg((32600 if lat >= 0 else 32700) + zone)


def read_dem(
    bbox: tuple[float, float, float, float],
    res_m: float = 10.0,
    pad_m: float = 200.0,
    base: str | None = None,
) -> Grid:
    """Read `bbox` (west, south, east, north in degrees) plus `pad_m` of margin onto a UTM grid.

    The margin lets water that arrives from just outside the box be counted, and keeps
    resampling edge effects out of the box itself.
    """
    base = base or os.environ.get("BIH_TERRAIN_BASE", COPERNICUS_BASE)
    west, south, east, north = bbox
    if not (west < east and south < north):
        raise ValueError(f"bbox must be (west, south, east, north) with west<east, south<north: {bbox}")

    utm = utm_crs_for((west + east) / 2, (south + north) / 2)
    to_utm = Transformer.from_crs(4326, utm, always_xy=True)
    to_ll = Transformer.from_crs(utm, 4326, always_xy=True)

    xs, ys = to_utm.transform([west, east, east, west], [south, south, north, north])
    minx = math.floor((min(xs) - pad_m) / res_m) * res_m
    maxx = math.ceil((max(xs) + pad_m) / res_m) * res_m
    miny = math.floor((min(ys) - pad_m) / res_m) * res_m
    maxy = math.ceil((max(ys) + pad_m) / res_m) * res_m
    width = round((maxx - minx) / res_m)
    height = round((maxy - miny) / res_m)
    dst_transform = from_origin(minx, maxy, res_m, res_m)

    # Geographic window to read: the padded UTM extent, back-projected, plus a few source pixels.
    cx, cy = to_ll.transform([minx, maxx, maxx, minx], [miny, miny, maxy, maxy])
    margin = 0.002
    rb = (min(cx) - margin, min(cy) - margin, max(cx) + margin, max(cy) + margin)

    timings: dict[str, float] = {}
    t0 = time.perf_counter()
    with rasterio.Env(**_GDAL_OPTS):
        datasets = []
        missing = []
        for url in tiles_for_bounds(*rb, base):
            try:
                datasets.append(rasterio.open(url))
            except RasterioIOError:
                missing.append(url)  # open ocean has no tile
        if not datasets:
            raise TerrainUnavailable(f"no terrain tile covers {bbox}")
        timings["open_s"] = time.perf_counter() - t0
        try:
            t1 = time.perf_counter()
            src, src_t = merge(datasets, bounds=rb, nodata=_NODATA)
            timings["read_s"] = time.perf_counter() - t1
            src_crs = datasets[0].crs
            source_res_deg = abs(datasets[0].res[0])
        finally:
            for ds in datasets:
                ds.close()

    t2 = time.perf_counter()
    dem = np.full((height, width), np.nan, dtype=np.float32)
    reproject(
        source=src[0].astype(np.float32),
        destination=dem,
        src_transform=src_t,
        src_crs=src_crs,
        src_nodata=_NODATA,
        dst_transform=dst_transform,
        dst_crs=utm,
        dst_nodata=np.nan,
        resampling=Resampling.bilinear,
    )
    timings["reproject_s"] = time.perf_counter() - t2

    return Grid(
        dem=dem,
        transform=dst_transform,
        crs=utm,
        res_m=res_m,
        source_res_m=source_res_deg * 111_320.0 * math.cos(math.radians((south + north) / 2)),
        meta={
            "source": "Copernicus GLO-30",
            "tiles": len(datasets),
            "tiles_missing": len(missing),
            "bbox": bbox,
            "pad_m": pad_m,
            "timings": timings,
        },
    )
