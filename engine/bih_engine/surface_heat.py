"""Where the ground runs hot: the staged Landsat surface-temperature composite, read for a box.

A weather forecast has no streets. It is a model of the air on a grid of kilometres, so inside a
neighbourhood it can only say one thing, give or take a degree. A thermal satellite sees the ground
itself, and that is where the block-to-block differences are: asphalt, roofs and bare ground run hot,
trees, water margins and wet soil run cool. Its sensor resolves patches about 100 m across, delivered
on a 30 m grid, so it shows a few blocks at best and never a single building.

What this is NOT is worth stating first, because every one of these gets misread:
  - It is a typical season, the median of every clear view over several years (see
    `tools/fetch_lst.py`). It is where surfaces usually run hotter than their neighbours. It is not a
    forecast and it is not today.
  - It is SURFACE temperature, the skin of the roof or the leaf, which in the sun runs far above the
    air a thermometer reads. It is never to be set against a forecast heat index as if comparable.
  - The satellite passes at mid-morning, so afternoon surfaces run hotter and spread further apart.

The colour scale is the whole city's, not the box's. A box rescaled to its own extremes would paint a
degree of difference in the full red-to-blue range and look like a heat emergency; on the city's scale
a flat neighbourhood looks flat.
"""
from __future__ import annotations

import math

import numpy as np
import rasterio
import shapely
from rasterio.windows import Window, from_bounds
from shapely.ops import nearest_points

M_PER_DEG_LAT = 110574.0
M_PER_DEG_LON = 111320.0

# The most cells on a side sent to the browser. A city at 30 m is 900 a side; the picture does not
# need it and the wire does not want it. A box smaller than this goes out at its native resolution.
MAX_CELLS = 160
# A downsampled cell, or a smoothed window, needs this share of its pixels to be land with a value.
MIN_COVER = 0.5
# Ranking looks at neighbourhood averages rather than single pixels, so one hot roof is not a hot
# place; and two places must be this far apart, so the top three are not three corners of one block.
SMOOTH_M = 150.0
SEPARATION_M = 500.0
PLACES = {"hottest": 3, "coolest": 2}
# A place only counts as hottest or coolest if it is inside this share of the box's ground at that end.
PLACE_TAIL_PCT = 10
NEAREST_ROAD_MAX_M = 300.0


def available(region) -> bool:
    meta = region.manifest.get("surface_heat")
    return bool(meta) and (region.root / meta["file"]).exists()


def _read(region, bbox) -> tuple[np.ndarray, tuple, tuple]:
    """The composite over `bbox` as a float array (NaN where there is no value), with the window's
    (west, south, east, north) and the pixel size in degrees."""
    meta = region.manifest["surface_heat"]
    with rasterio.open(region.root / meta["file"]) as ds:
        win = from_bounds(*bbox, ds.transform).round_offsets().round_lengths()
        win = win.intersection(Window(0, 0, ds.width, ds.height))
        arr = ds.read(1, window=win).astype("float64")
        bounds = ds.window_bounds(win)
        res = (abs(ds.res[0]), abs(ds.res[1]))
        nodata = ds.nodata
    if nodata is not None:
        arr[arr == nodata] = np.nan
    arr[~np.isfinite(arr)] = np.nan
    return arr, bounds, res


def _blocks(arr: np.ndarray, f: int) -> np.ndarray:
    """Mean over f x f blocks, only where enough of the block is land with a value."""
    h, w = (arr.shape[0] // f) * f, (arr.shape[1] // f) * f
    a = arr[:h, :w].reshape(h // f, f, w // f, f)
    ok = np.isfinite(a)
    n = ok.sum(axis=(1, 3))
    s = np.where(ok, a, 0.0).sum(axis=(1, 3))
    return np.where(n >= MIN_COVER * f * f, s / np.maximum(n, 1), np.nan)


def _smooth(arr: np.ndarray, k: int) -> np.ndarray:
    """A k x k moving mean that ignores gaps, via a summed-area table so it costs one pass."""
    ok = np.isfinite(arr)
    h, w = arr.shape
    r = k // 2
    y0 = np.clip(np.arange(h) - r, 0, h)
    y1 = np.clip(np.arange(h) + r + 1, 0, h)
    x0 = np.clip(np.arange(w) - r, 0, w)
    x1 = np.clip(np.arange(w) + r + 1, 0, w)

    def box(a):
        c = np.pad(a, ((1, 0), (1, 0))).cumsum(0).cumsum(1)
        return c[np.ix_(y1, x1)] - c[np.ix_(y0, x1)] - c[np.ix_(y1, x0)] + c[np.ix_(y0, x0)]

    s, n = box(np.where(ok, arr, 0.0)), box(ok.astype(float))
    return np.where(n >= MIN_COVER * k * k, s / np.maximum(n, 1), np.nan)


def _local_max(a: np.ndarray, r: int) -> np.ndarray:
    """The largest value within r cells in each direction (a square window), one axis at a time so it
    costs two passes rather than a window's worth of work per cell. NaN counts as nothing."""
    from numpy.lib.stride_tricks import sliding_window_view as windows
    out = np.where(np.isfinite(a), a, -np.inf)
    for axis in (0, 1):
        pad = [(0, 0), (0, 0)]
        pad[axis] = (r, r)
        out = windows(np.pad(out, pad, constant_values=-np.inf), 2 * r + 1, axis=axis).max(axis=-1)
    return out


def _peaks(sm: np.ndarray, count: int, hot: bool, sep_px: float, bound: float) -> list[tuple[int, int]]:
    """Up to `count` of the most extreme smoothed cells, strictly beyond `bound`, each the highest
    (or lowest) ground within `sep_px` of itself, and no two closer than that.

    The bound keeps a box with one hot patch from being padded out to three "hottest places" with
    ordinary ground, which would be a ranking of nothing. Being the extreme of its own surroundings
    keeps the smooth ring around one hot block from being reported as a second place at its far edge.
    """
    signed = sm if hot else -sm
    qualifies = np.isfinite(sm) & ((sm > bound) if hot else (sm < bound))
    peak = qualifies & (signed >= _local_max(signed, max(1, int(sep_px))))
    flat = np.where(peak, signed, -np.inf).ravel()
    order = np.argsort(-flat, kind="stable")
    out: list[tuple[int, int]] = []
    for i in order:
        if not np.isfinite(flat[i]):
            break
        r, c = divmod(int(i), sm.shape[1])
        if all(math.hypot(r - r2, c - c2) >= sep_px for r2, c2 in out):
            out.append((r, c))
            if len(out) == count:
                break
    return out


def _roads_near(region, bbox, points) -> list[dict | None]:
    """The nearest NAMED road to each (lon, lat), or None. Only named roads are candidates: OSM's
    unnamed ways are the majority by count and would win on distance every time while naming
    nothing. Distances are worked out in local metres."""
    pad = 0.004
    w, s, e, n = bbox
    roads = [r for r in region.read_osm((w - pad, s - pad, e + pad, n + pad)).roads if r.name]
    if not roads or not points:
        return [None] * len(points)
    tree = shapely.STRtree([r.geom for r in roads])
    out = []
    for lon, lat in points:
        p = shapely.Point(lon, lat)
        road = roads[int(tree.nearest(p))]
        q = nearest_points(p, road.geom)[1]
        dx = (q.x - lon) * M_PER_DEG_LON * math.cos(math.radians(lat))
        dy = (q.y - lat) * M_PER_DEG_LAT
        dist = math.hypot(dx, dy)
        out.append(None if dist > NEAREST_ROAD_MAX_M else
                   {"name": road.name, "highway": road.highway, "distance_m": round(dist)})
    return out


def _places(region, arr, bounds, res, median) -> dict[str, list[dict]]:
    """The hottest and coolest few places in the window, named by the road beside them."""
    west, _, _, north = bounds
    lat_mid = north - arr.shape[0] * res[1] / 2
    px_x = res[0] * M_PER_DEG_LON * math.cos(math.radians(lat_mid))
    px_y = res[1] * M_PER_DEG_LAT
    k = max(3, int(round(SMOOTH_M / ((px_x + px_y) / 2))) // 2 * 2 + 1)
    sm = _smooth(arr, k)
    seen = sm[np.isfinite(sm)]
    if not seen.size:
        return {kind: [] for kind in PLACES}
    # "Hottest" means in the hottest tenth of the ground here, "coolest" the coolest tenth: a rule
    # about this box's own spread rather than a number of degrees chosen by us.
    bounds_of = {"hottest": float(np.percentile(seen, 100 - PLACE_TAIL_PCT)),
                 "coolest": float(np.percentile(seen, PLACE_TAIL_PCT))}
    sep = SEPARATION_M / ((px_x + px_y) / 2)
    found: dict[str, list[dict]] = {}
    for kind, count in PLACES.items():
        cells = _peaks(sm, count, kind == "hottest", sep, bounds_of[kind])
        pts = [(west + (c + 0.5) * res[0], north - (r + 0.5) * res[1]) for r, c in cells]
        roads = _roads_near(region, (min(p[0] for p in pts), min(p[1] for p in pts),
                                     max(p[0] for p in pts), max(p[1] for p in pts)), pts) if pts else []
        found[kind] = [{
            "lon": round(lon, 5), "lat": round(lat, 5),
            "surface_c": round(float(sm[r, c]), 1),
            "vs_box_median_c": round(float(sm[r, c]) - median, 1),
            "nearest_road": road,
        } for (r, c), (lon, lat), road in zip(cells, pts, roads)]
    return found


def read(region, bbox=None, *, places: bool = False) -> dict:
    """The surface-heat picture for `bbox` (or the whole staged region), ready to draw.

    `values` run south edge first, the order the map's field renderer takes, with None where there is
    no land or no clear view. `min`/`max` are the CITY's 2nd and 98th percentile so the scale is the
    same however small the box. `places` adds the hottest and coolest spots, which needs the roads
    and so is only worth asking for a drawn box."""
    if not available(region):
        return {"available": False, "reason": "no surface-temperature composite is staged for this city"}
    meta = region.manifest["surface_heat"]
    box = tuple(bbox) if bbox else tuple(region.bounds)
    arr, bounds, res = _read(region, box)
    ok = np.isfinite(arr)
    if arr.size == 0 or not ok.any():
        return {"available": False, "reason": "no land with a satellite reading in this area"}

    f = max(1, math.ceil(max(arr.shape) / MAX_CELLS))
    grid = _blocks(arr, f) if f > 1 else arr
    west, south, east, north = bounds
    cols, rows = grid.shape[1], grid.shape[0]
    east = west + cols * f * res[0]
    south = north - rows * f * res[1]
    lat_mid = (north + south) / 2
    cell_m = round(f * (res[0] * M_PER_DEG_LON * math.cos(math.radians(lat_mid))
                        + res[1] * M_PER_DEG_LAT) / 2)

    low, median, high = (round(float(v), 1) for v in np.percentile(arr[ok], [5, 50, 95]))
    city_median = meta["median_c"]
    out = {
        "available": True,
        "rows": rows, "cols": cols, "bbox": [west, south, east, north], "cell_m": cell_m,
        "values": [None if not np.isfinite(v) else round(float(v), 1) for v in grid[::-1].ravel()],
        "min": meta["p2_c"], "max": meta["p98_c"],
        "typical_low_c": low, "median_c": median, "typical_high_c": high,
        "city_median_c": city_median, "vs_city_median_c": round(median - city_median, 1),
        "land_share": round(float(ok.mean()), 3),
        "source": meta["source"], "season": meta["season"], "scenes": meta["scenes"],
        "passes_about_local_hour": meta["passes_about_local_hour"],
        "composite": meta["composite"], "unit": meta["unit"],
        # The sensor's own resolution, which is coarser than `cell_m`: the grid is finer than the
        # instrument, and the browser has to be able to say so.
        "native_res_m": meta.get("native_res_m"), "resolution": meta.get("resolution"),
        "caveats": meta["caveats"],
        "method": "Median of every clear Landsat view over several seasons. A typical season, not a "
                  "forecast; the surface, not the air.",
    }
    if places:
        out.update(_places(region, arr, bounds, res, median))
    return out
