"""Screening tier: where does this box trap water, and which spots fill first?

Output is a *relative* pooling index (1-5), contributing area and fill order. Depths in metres
are used internally (to flag depressions too shallow to trust) and are never emitted: at 30 m
the terrain error is the same size as the depths we would otherwise report.
"""
from __future__ import annotations

import time

import numpy as np
from pyproj import Transformer
from rasterio.features import shapes
from scipy import ndimage
import shapely
from shapely.geometry import mapping, shape

from .hydrology import exit_accumulation, fill_and_route
from .terrain import Grid, read_dem

# Bare-earth DEM error we expect (DeltaDTM MAE ~0.45 m; GLO-30 is no better inland).
DEFAULT_DEM_ERROR_M = 0.45
DEFAULT_RUNOFF_C = 0.8  # placeholder until land cover (L8) feeds a per-cell coefficient

CAVEAT = (
    "30 m terrain screening. Relative ranking only, not depths. "
    "No storm-drain data exists, so pooling is overstated where drains work."
)


def _pooling_index(rain_to_fill_mm: np.ndarray) -> np.ndarray:
    """1-5 by quintile of rain-needed-to-fill (least rain = most urgent = 5)."""
    n = rain_to_fill_mm.size
    if n == 0:
        return np.zeros(0, dtype=int)
    rank = np.argsort(np.argsort(rain_to_fill_mm))  # 0 = fills with least rain
    pct = rank / max(n - 1, 1)
    return (5 - np.minimum((pct * 5).astype(int), 4)).astype(int)


def analyse_grid(
    grid: Grid,
    rainfall_mm: float = 100.0,
    *,
    runoff_c: float = DEFAULT_RUNOFF_C,
    min_depth_m: float = 0.1,
    min_area_m2: float | None = None,
    dem_error_m: float = DEFAULT_DEM_ERROR_M,
    water_level_m: float | None = None,
) -> dict:
    """Run the screening on an already-read grid. Pure function of its inputs."""
    t = {}
    dem = grid.dem.astype(np.float64)
    h, w = dem.shape
    cell_m2 = grid.res_m**2
    if min_area_m2 is None:
        # Features smaller than one source pixel are not resolved by the terrain at all.
        min_area_m2 = grid.source_res_m**2

    nodata = np.isnan(dem)
    outlet = nodata.copy()
    outlet[0, :] = outlet[-1, :] = outlet[:, 0] = outlet[:, -1] = True
    if water_level_m is not None:
        outlet |= dem <= water_level_m  # open water drains the land to it
    if nodata.any():
        dem = np.where(nodata, np.nanmin(dem) - 1.0, dem)

    t0 = time.perf_counter()
    filled, fdir, acc = fill_and_route(dem, outlet)
    t["fill_route_s"] = time.perf_counter() - t0

    t0 = time.perf_counter()
    depth = np.where(outlet, 0.0, filled - dem)
    labels, n = ndimage.label(depth > min_depth_m, structure=np.ones((3, 3), dtype=int))
    deps = []
    if n:
        idx = np.arange(1, n + 1)
        ones = np.ones_like(depth)
        cells = ndimage.sum(ones, labels, idx)
        max_depth = ndimage.maximum(depth, labels, idx)
        volume = ndimage.sum(depth, labels, idx) * cell_m2
        contrib_cells = exit_accumulation(labels, fdir, acc, n)
        com = ndimage.center_of_mass(ones, labels, idx)
        for i in range(n):
            area = cells[i] * cell_m2
            if area < min_area_m2:
                continue
            contrib_m2 = contrib_cells[i] * cell_m2
            rain_to_fill_mm = volume[i] / (runoff_c * contrib_m2) * 1000.0
            deps.append(
                dict(label=int(idx[i]), area_m2=float(area), volume_m3=float(volume[i]),
                     max_depth_m=float(max_depth[i]), contrib_m2=float(contrib_m2),
                     rain_to_fill_mm=float(rain_to_fill_mm), row=com[i][0], col=com[i][1])
            )
    t["depressions_s"] = time.perf_counter() - t0

    # Rank: fills with the least rain first. Ties broken by larger catchment, then position.
    deps.sort(key=lambda d: (d["rain_to_fill_mm"], -d["contrib_m2"], d["row"], d["col"]))
    idx_arr = _pooling_index(np.array([d["rain_to_fill_mm"] for d in deps]))
    to_ll = Transformer.from_crs(grid.crs, 4326, always_xy=True)
    kept = {d["label"]: d for d in deps}
    for order, d in enumerate(deps, start=1):
        d["id"] = f"d_{order:02d}"
        d["fill_order"] = order
        d["pooling_index"] = int(idx_arr[order - 1])
        d["fills_at_rainfall"] = bool(rainfall_mm >= d["rain_to_fill_mm"])
        d["low_confidence"] = bool(d["max_depth_m"] < dem_error_m)
        x, y = grid.transform @ (d["col"] + 0.5, d["row"] + 0.5)
        d["lon"], d["lat"] = to_ll.transform(x, y)

    t0 = time.perf_counter()
    feats = []
    if kept:
        keep_mask = np.isin(labels, list(kept))
        for geom, val in shapes(labels.astype(np.int32), mask=keep_mask, transform=grid.transform):
            d = kept[int(val)]
            g = shapely.transform(shape(geom), lambda c: np.column_stack(to_ll.transform(c[:, 0], c[:, 1])))
            props = {k: d[k] for k in ("id", "pooling_index", "fill_order",
                                       "fills_at_rainfall", "low_confidence")}
            props["contributing_ha"] = round(d["contrib_m2"] / 1e4, 2)
            feats.append({"type": "Feature", "geometry": mapping(g), "properties": props})
    t["vectorise_s"] = time.perf_counter() - t0

    screening = {
        "depressions": [
            {"id": d["id"], "pooling_index": d["pooling_index"],
             "contributing_ha": round(d["contrib_m2"] / 1e4, 2),
             "fill_order": d["fill_order"], "nearest_road": None}
            for d in deps
        ],
        "routes_affected": [],
        "confidence": {"terrain": "weak" if grid.source_res_m >= 20 else "ok",
                       "drains": "gap", "validated_layer": "not_checked"},
        "caveat": CAVEAT,
    }
    return {
        "screening": screening,
        "geojson": {"type": "FeatureCollection", "features": feats},
        "grid": {"shape": [h, w], "res_m": grid.res_m, "area_km2": round(h * w * cell_m2 / 1e6, 2)},
        "timings_s": t,
        # Internal only: raw depths and locations. Never surface depth to the user (hard limit #1).
        "debug": deps,
    }


def analyse_area(
    bbox: tuple[float, float, float, float],
    rainfall_mm: float = 100.0,
    *,
    res_m: float = 10.0,
    pad_m: float = 200.0,
    **kw,
) -> dict:
    """Read the box, then screen it. `timings_s` covers both stages."""
    grid = read_dem(bbox, res_m=res_m, pad_m=pad_m)
    res = analyse_grid(grid, rainfall_mm, **kw)
    res["timings_s"] = {**grid.meta["timings"], **res["timings_s"]}
    res["grid"]["source"] = grid.meta["source"]
    return res
