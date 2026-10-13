"""Population density grid for the layer overlay.

Divides the region bbox into cells and counts building footprint area in each,
then converts to people/km2 using the same self-consistent ratio as exposure.py.
Returns a GeoJSON FeatureCollection of grid cells with a `density` property.
"""
from __future__ import annotations

import json

import numpy as np
from pyproj import CRS, Transformer
from shapely import transform as shp_transform
from shapely.geometry import box, mapping


def grid(bbox: tuple, population: dict, buildings, crs,
         cell_deg: float = 0.005) -> dict:
    """Build a density grid over the bbox.

    Returns {geojson: FeatureCollection, max_density: float, source: str}.
    """
    people = population.get("people", 0)
    region_fp = population.get("built_footprint_m2", 0)
    if not people or not region_fp or not buildings:
        return {"available": False, "reason": "population or buildings not staged"}

    w, s, e, n = bbox
    tr = Transformer.from_crs(4326, crs, always_xy=True)
    to_m = lambda g: shp_transform(g, lambda c: np.column_stack(tr.transform(c[:, 0], c[:, 1])))  # noqa: E731

    b_m = [to_m(b) for b in buildings]
    scale = people / region_fp

    cols = max(1, int((e - w) / cell_deg))
    rows = max(1, int((n - s) / cell_deg))
    cell_w = (e - w) / cols
    cell_h = (n - s) / rows

    import shapely
    tree = shapely.STRtree(b_m)

    features = []
    max_density = 0.0

    for r in range(rows):
        for c in range(cols):
            cw = w + c * cell_w
            cs = s + r * cell_h
            ce = cw + cell_w
            cn = cs + cell_h
            cell_geo = box(cw, cs, ce, cn)
            cell_m = to_m(cell_geo)
            cell_area_km2 = cell_m.area / 1e6
            if cell_area_km2 < 1e-6:
                continue

            fp_area = 0.0
            for i in tree.query(cell_m, predicate="intersects"):
                inter = b_m[int(i)].intersection(cell_m).area
                fp_area += inter

            density = (fp_area * scale) / cell_area_km2
            if density < 1:
                continue

            max_density = max(max_density, density)
            features.append({
                "type": "Feature",
                "geometry": mapping(cell_geo),
                "properties": {"density": round(density, 1)},
            })

    geojson = {"type": "FeatureCollection", "features": features}
    return {
        "available": True,
        "geojson": geojson,
        "max_density": round(max_density, 1),
        "source": population.get("source", ""),
    }
