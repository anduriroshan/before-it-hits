"""How many people per square kilometre, by area, as a raster the map can draw as a field.

There is no per-street population figure for either staged city, and there is not going to be one:
what exists is a published total for the city and a near-complete set of building footprints. So the
density here is that total spread across footprint area - the same self-consistent ratio exposure.py
uses - and it is an approximation *by area*, never a claim about a street or a home.

It is returned as a raster rather than as polygons because a hard-edged cell grid reads as a
measurement at the cell boundary, which this is not. A smooth field is both the honest picture and
the legible one: it says "more people around here", which is exactly as much as the method supports.

Two approximations are taken deliberately, because at this cell size neither is visible and the
exact versions turn a layer toggle into a half-minute wait:
  - A building is counted in the cell its centre falls in, rather than split across the cells it
    straddles. A footprint is metres across and a cell is hundreds, so the error is a rounding at
    the edges of a picture whose whole point is the broad shape.
  - Footprint area comes from the local metres-per-degree at the building's own latitude rather
    than from a projection. Over a city that is a fraction of a percent.
"""
from __future__ import annotations

import math

import numpy as np
import shapely

# A cell smaller than this is drawing detail the method does not have: the footprint ratio is a city
# average, and below a few blocks the error in it swamps the difference between neighbours. It binds
# before anything else - a small box gets few cells, not fine ones, and a box smaller than one cell
# honestly gets a single number.
MIN_CELL_M = 150.0
# The ceiling on how finely a whole city is drawn. More cells than this is detail nobody can read at
# city zoom, on a method that does not support it anyway.
TARGET_CELLS = 44

M_PER_DEG_LAT = 110574.0
M_PER_DEG_LON = 111320.0


def _side_m(bbox: tuple) -> float:
    w, s, e, n = bbox
    mid = math.radians((s + n) / 2)
    return max(abs(e - w) * M_PER_DEG_LON * math.cos(mid), abs(n - s) * M_PER_DEG_LAT)


def _cells_for(bbox: tuple) -> int:
    """How many cells a side. Never finer than the method supports, never slower than one request."""
    return max(1, min(TARGET_CELLS, int(_side_m(bbox) // MIN_CELL_M)))


def raster(bbox: tuple, population: dict, buildings, cells: int | None = None) -> dict:
    """People per km2 on a regular grid over the bbox.

    Returns {available, rows, cols, bbox, values (row-major, south row first), max, p95, source}.
    `values` is a flat list with null where a cell holds no building at all, so the browser can
    leave genuinely empty ground uncoloured instead of painting it the bottom of the ramp.
    """
    people = population.get("people", 0)
    region_fp = population.get("built_footprint_m2", 0)
    if not people or not region_fp or buildings is None or len(buildings) == 0:
        return {"available": False, "reason": "population or buildings not staged for this region"}

    w, s, e, n = bbox
    k = int(cells) if cells else _cells_for(bbox)
    cell_w, cell_h = (e - w) / k, (n - s) / k

    geoms = np.asarray(buildings, dtype=object)
    centres = shapely.centroid(geoms)
    cx = shapely.get_x(centres)
    cy = shapely.get_y(centres)
    # Footprint area from the local metres-per-degree at each building's own latitude.
    area_m2 = shapely.area(geoms) * M_PER_DEG_LAT * M_PER_DEG_LON * np.cos(np.radians(cy))

    col = np.floor((cx - w) / cell_w).astype(np.int64)
    row = np.floor((cy - s) / cell_h).astype(np.int64)
    inside = (col >= 0) & (col < k) & (row >= 0) & (row < k) & np.isfinite(area_m2)
    if not inside.any():
        return {"available": False, "reason": "no buildings fall inside this area"}

    roof = np.zeros((k, k), dtype=float)
    np.add.at(roof, (row[inside], col[inside]), area_m2[inside])

    # Cell area varies with latitude, so it is worked out per row rather than once for the box.
    lat_centres = s + (np.arange(k) + 0.5) * cell_h
    cell_km2 = (cell_h * M_PER_DEG_LAT) * (cell_w * M_PER_DEG_LON
                                           * np.cos(np.radians(lat_centres))) / 1e6
    dens = roof * (people / region_fp) / cell_km2[:, None]

    filled = dens[roof > 0]
    if not filled.size:
        return {"available": False, "reason": "no buildings fall inside this area"}

    values = [None if r <= 0 else round(float(d))
              for r, d in zip(roof.ravel(), dens.ravel())]
    # The ramp is cut at the 95th percentile, not the maximum: one dense block otherwise sets the
    # top of the scale and flattens the whole rest of the city into the same colour.
    return {
        "available": True,
        "rows": k, "cols": k,
        "bbox": [w, s, e, n],
        "cell_m": round(_side_m(bbox) / k),
        "values": values,
        "max": round(float(filled.max())),
        "p95": round(float(np.percentile(filled, 95))),
        "people_total": people,
        "source": population.get("source", ""),
        "method": "The city's published population spread across OpenStreetMap building footprints "
                  "by area. An approximation by area, never a count of any street or home.",
        "extent": population.get("extent", ""),
    }
