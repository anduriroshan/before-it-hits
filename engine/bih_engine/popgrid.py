"""Where people live, from a modelled population grid, scaled to the published total.

This replaced spreading a city's total over its mapped building footprints. Footprint area is not
people: measured against Singapore's 2020 census by planning area, footprint-only put about a third of
the city in the wrong planning area (industrial Tuas got 6.5% of everyone and has no residents), and
this grid, on the same test, misplaced about a fifth of that. See tools/validate_population_sg.py.

The method has two parts, and it is worth being exact about which says what:
  - HOW MANY: the published census figure. One factor scales the whole staged grid so it adds up to it.
  - WHERE: the grid (WorldPop, about 100 m). It is a modelled estimate from settlement and satellite
    covariates, not a head count, and it is never a claim about one home or street.

Inside a flood spot the question is narrower: who lives on the ground the water reaches. A cell's people
are shared out over the mapped buildings in it by footprint, so a spot over a canal, a park or a field
holds nobody. Where a cell has people but no mapped building (OpenStreetMap is partial in Kochi: about
half of the grid's people are in such cells) the people are spread evenly over the cell instead, which is
the only fair prior and is said so. Nothing here attaches a number to a building.
"""
from __future__ import annotations

import math

import numpy as np
import rasterio
import shapely
from rasterio.features import rasterize
from rasterio.transform import Affine, from_bounds
from rasterio.warp import Resampling, reproject

from . import density

M_PER_DEG_LAT = 110574.0
M_PER_DEG_LON = 111320.0
SUB = 8                       # sub-pixels a side used to measure how much of a cell a spot covers

_cache: dict = {}


def available(region) -> bool:
    meta = region.manifest.get("population_grid")
    return (bool(meta) and (region.root / meta["file"]).exists()
            and bool((region.manifest.get("population") or {}).get("people")))


def _grid(region) -> tuple[np.ndarray, Affine]:
    """People per pixel over the whole staged region, NaN where the grid has no value, scaled so the
    staged area adds up to the published total."""
    meta = region.manifest["population_grid"]
    path = region.root / meta["file"]
    key = (str(path), path.stat().st_mtime, region.manifest["population"]["people"])
    hit = _cache.get(key)
    if hit is not None:
        return hit
    with rasterio.open(path) as ds:
        raw, transform, nodata = ds.read(1).astype("float64"), ds.transform, ds.nodata
    raw[~np.isfinite(raw)] = np.nan
    if nodata is not None and np.isfinite(nodata):
        raw[raw == nodata] = np.nan
    raw[raw < 0] = np.nan
    scale = region.manifest["population"]["people"] / float(np.nansum(raw))
    _cache.clear()                                   # one region's grid at a time is plenty
    _cache[key] = (raw * scale, transform)
    return _cache[key]


def _km2_by_row(transform: Affine, rows: int) -> np.ndarray:
    """Area of a pixel in each row, which shrinks towards the poles."""
    lat = transform.f + (np.arange(rows) + 0.5) * transform.e
    return (abs(transform.e) * M_PER_DEG_LAT) * (transform.a * M_PER_DEG_LON * np.cos(np.radians(lat))) / 1e6


def _two_figures(x: float) -> int:
    return int(float(f"{x:.2g}"))


def raster(region, bbox: tuple, cells: int | None = None) -> dict:
    """People per km2 on a regular grid over `bbox`, in the same shape density.raster returns, so the
    map layer and the agent do not care which method produced it. Null where the grid has no people."""
    people, T = _grid(region)
    # A pixel with no value holds nobody. Left as nodata it would drop out of the average below and a
    # cell half over empty ground would be credited with its populated half's density across the lot.
    dens = np.nan_to_num(people) / _km2_by_row(T, people.shape[0])[:, None]
    w, s, e, n = bbox
    k = int(cells) if cells else density._cells_for(bbox)
    dst = np.full((k, k), np.nan)
    # Average, not nearest: a cell wider than a pixel is the area-weighted mean of the pixels under it.
    reproject(dens, dst, src_transform=T, src_crs="EPSG:4326",
              dst_transform=from_bounds(w, s, e, n, k, k), dst_crs="EPSG:4326", dst_nodata=np.nan,
              resampling=Resampling.average)
    d = dst[::-1]                                    # south row first, as the density layer is
    filled = np.isfinite(d) & (d > 0)
    if not filled.any():
        return {"available": False, "reason": "the population grid holds no people inside this area"}
    cell_w, cell_h = (e - w) / k, (n - s) / k
    lat_c = s + (np.arange(k) + 0.5) * cell_h
    cell_km2 = (cell_h * M_PER_DEG_LAT) * (cell_w * M_PER_DEG_LON * np.cos(np.radians(lat_c))) / 1e6
    in_area = float(np.where(filled, d * cell_km2[:, None], 0.0).sum())
    meta = region.manifest["population_grid"]
    vals = d[filled]
    return {
        "available": True,
        "rows": k, "cols": k,
        "bbox": [w, s, e, n],
        "cell_m": round(density._side_m(bbox) / k),
        "values": [round(float(x)) if f else None for x, f in zip(d.ravel(), filled.ravel())],
        "max": round(float(vals.max())),
        "p95": round(float(np.percentile(vals, 95))),
        "people_in_area": _two_figures(in_area),
        "city_people": region.manifest["population"]["people"],
        "source": f"{meta['source']}; scaled to {region.manifest['population'].get('source', 'the published total')}",
        "method": "A modelled population grid of about 100 m, scaled so the city adds up to its published "
                  "total. An approximation by area, never a count of any street or home.",
        "extent": region.manifest["population"].get("extent", ""),
    }


def cell_people(r: dict) -> np.ndarray:
    """People in each cell of a density raster (what `density_for` returns), south row first.

    For anything that has to split a box's people by some other map - which ground they stand on, how
    hard it shakes there. The density is stored whole, so a cell's people are that density times the
    cell's own area, which shrinks towards the poles."""
    w, s, e, n = r["bbox"]
    rows, cols = r["rows"], r["cols"]
    lat_c = s + (np.arange(rows) + 0.5) * ((n - s) / rows)
    km2 = ((n - s) / rows * M_PER_DEG_LAT) * ((e - w) / cols * M_PER_DEG_LON
                                              * np.cos(np.radians(lat_c))) / 1e6
    dens = np.array([0.0 if v is None else v for v in r["values"]], float).reshape(rows, cols)
    return dens * km2[:, None]


def density_for(region, bbox: tuple, cells: int | None = None) -> dict:
    """The density raster for a box by whichever method this region has: the grid where one is staged,
    else the published total spread over mapped footprints. One door, so the map layer and the agent
    can never quote two different methods for the same box."""
    if available(region):
        return raster(region, bbox, cells)
    pop = region.manifest.get("population")
    if not pop:
        return {"available": False, "reason": "no population figure is staged for this city"}
    osm = region.read_osm(bbox)
    if not osm.buildings:
        return {"available": False, "reason": "no buildings are staged for this area"}
    return density.raster(bbox, pop, osm.buildings, cells)


def people_in(region, spots: list) -> dict:
    """People on the ground the given polygons (lon/lat) cover.

    Returns {"people": float, "mapped": float, "unmapped": float, "buildings": int, "footprint_m2": float}.
    `mapped` is people placed on buildings by footprint; `unmapped` is people spread evenly over cells
    that have people but no mapped building."""
    out = {"people": 0.0, "mapped": 0.0, "unmapped": 0.0, "buildings": 0, "footprint_m2": 0.0}
    if not spots:
        return out
    people, T = _grid(region)
    rows, cols = people.shape
    union = shapely.union_all(spots)
    if union.is_empty:
        return out
    minx, miny, maxx, maxy = union.bounds
    c0 = max(0, math.floor((minx - T.c) / T.a))
    c1 = min(cols, math.ceil((maxx - T.c) / T.a))
    r0 = max(0, math.floor((maxy - T.f) / T.e))
    r1 = min(rows, math.ceil((miny - T.f) / T.e))
    if c1 <= c0 or r1 <= r0:
        return out
    wh, ww = r1 - r0, c1 - c0
    P = np.nan_to_num(people[r0:r1, c0:c1])
    win = (T.c + c0 * T.a, T.f + r1 * T.e, T.c + c1 * T.a, T.f + r0 * T.e)      # west, south, east, north

    # Every building in the window's cells, not only those a spot touches: a cell's people are shared
    # among all of its buildings, so leaving some out would give the rest too many.
    bs = region.read_osm(win).buildings
    footprint = np.zeros((wh, ww))
    if bs:
        geoms = np.asarray(bs, dtype=object)
        cen = shapely.centroid(geoms)
        cx, cy = shapely.get_x(cen), shapely.get_y(cen)
        fac = M_PER_DEG_LAT * M_PER_DEG_LON * np.cos(np.radians(cy))
        area = shapely.area(geoms) * fac
        col = np.floor((cx - win[0]) / T.a).astype(int)
        row = np.floor((win[3] - cy) / abs(T.e)).astype(int)
        ok = (col >= 0) & (col < ww) & (row >= 0) & (row < wh) & (area > 0)
        np.add.at(footprint, (row[ok], col[ok]), area[ok])

        tree = shapely.STRtree(geoms)
        for i in tree.query(union, predicate="intersects"):
            i = int(i)
            if not ok[i] or footprint[row[i], col[i]] <= 0:
                continue
            inter = float(geoms[i].intersection(union).area * fac[i])
            if inter <= 0:
                continue
            share = area[i] / footprint[row[i], col[i]]
            out["mapped"] += P[row[i], col[i]] * share * (inter / area[i])
            out["buildings"] += 1
            out["footprint_m2"] += inter

    # Cells with people and no mapped building: the people are spread evenly, so a spot gets the
    # fraction of the cell it covers.
    unmapped = (P > 0) & (footprint == 0)
    if unmapped.any():
        sub = Affine(T.a / SUB, 0, win[0], 0, T.e / SUB, win[3])
        covered = rasterize([(g, 1) for g in np.atleast_1d(shapely.get_parts(union))],
                            out_shape=(wh * SUB, ww * SUB), transform=sub, fill=0, dtype="uint8")
        frac = covered.reshape(wh, SUB, ww, SUB).mean(axis=(1, 3))
        out["unmapped"] = float((P * frac)[unmapped].sum())
    out["people"] = out["mapped"] + out["unmapped"]
    return out
