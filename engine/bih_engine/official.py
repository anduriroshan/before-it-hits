"""The validated tier: somebody else's flood depth map, shown as published.

This is not our output and we never recompute it. The only things done here are clipping to the
drawn box and grouping cells into depth bands so they can be drawn in colour; the published values
themselves are untouched, and the band edges are returned alongside so the encoding is visible.

Kept deliberately separate from the screening in `analyse.py`. The two are shown side by side and
never merged: one says how deep an authority thinks it gets, the other says which local low points
fill first and why.
"""
from __future__ import annotations

import numpy as np
import rasterio
import shapely
from pyproj import Transformer
from rasterio.features import shapes
from rasterio.windows import from_bounds
from shapely.geometry import mapping, shape

# Depth bands for display, in the published units. Chosen to separate nuisance water from
# "a car is stranded" from "a single-storey room is flooded".
BANDS_CM = [(0, 30), (30, 100), (100, 200), (200, 500), (500, None)]
BAND_LABELS = ["under 30 cm", "30 cm – 1 m", "1 – 2 m", "2 – 5 m", "over 5 m"]


def available(region) -> bool:
    return bool(region.manifest.get("official"))


def _band_index(v: np.ndarray) -> np.ndarray:
    """0-based band per cell; -1 where there is no data."""
    out = np.full(v.shape, -1, dtype=np.int16)
    for i, (lo, hi) in enumerate(BANDS_CM):
        m = np.isfinite(v) & (v >= lo) & ((v < hi) if hi is not None else True)
        out[m] = i
    return out


def read_official(region, bbox, return_period: int) -> dict | None:
    """Clip the published raster for `return_period` to `bbox` and return it as banded GeoJSON.

    Returns None when the region has no official layer at all, which is the normal case almost
    everywhere and must be said on screen rather than hidden.
    """
    meta = region.manifest.get("official")
    if not meta:
        return None
    if return_period not in meta["return_periods"]:
        raise ValueError(f"no official layer for a {return_period}-year event; "
                         f"available: {meta['return_periods']}")

    path = region.root / "official" / meta["layers"][str(return_period)]["file"]
    with rasterio.open(path) as ds:
        win = from_bounds(*bbox, ds.transform).round_offsets().round_lengths()
        arr = ds.read(1, window=win).astype("float64")
        transform = ds.window_transform(win)
        nodata = ds.nodata
        crs = ds.crs
    arr[(arr == nodata) | (arr < -1e30)] = np.nan
    if arr.size == 0:
        return None

    bands = _band_index(arr)
    to_ll = Transformer.from_crs(crs, 4326, always_xy=True) if crs.to_epsg() != 4326 else None

    feats = []
    for geom, val in shapes(bands, mask=bands >= 0, transform=transform):
        g = shape(geom)
        if to_ll is not None:
            g = shapely.transform(g, lambda c: np.column_stack(to_ll.transform(c[:, 0], c[:, 1])))
        feats.append({
            "type": "Feature", "geometry": mapping(g),
            "properties": {"band": int(val), "label": BAND_LABELS[int(val)]},
        })

    wet = np.isfinite(arr)
    share = float(wet.mean())
    return {
        "return_period_years": return_period,
        "scenario": meta.get("scenario", "historical"),
        "source": meta["source"],
        "title": meta["title"],
        "page": meta.get("page"),
        "units": meta["units"],
        "native_res_m_approx": meta.get("native_res_m_approx"),
        "licence": meta.get("licence"),
        "staged": meta.get("staged"),
        "available_return_periods": meta["return_periods"],
        "bands_cm": [[lo, hi] for lo, hi in BANDS_CM],
        "band_labels": BAND_LABELS,
        "inundated_share": round(share, 4),
        "deepest": None if not wet.any() else round(float(np.nanmax(arr)), 1),
        "median_where_wet": None if not wet.any() else round(float(np.nanmedian(arr)), 1),
        "caveats": meta.get("caveats", []),
        "geojson": {"type": "FeatureCollection", "features": feats},
    }


def compare_to_screening(official: dict | None, screening_geojson: dict) -> dict | None:
    """How much of our screening sits inside the authority's inundated area, against chance.

    The raw overlap share on its own is misleading: at a 500-year return period two thirds of a
    Kochi box is inundated, so almost anything overlaps. What matters is the overlap relative to
    how much of the box is wet at all - the enrichment. Above 1 means our top-ranked spots fall
    inside the official extent more often than placing them at random would.

    This is agreement, not accuracy. The official layer is coarser (~92 m against our 10 m) and
    models river and backwater flooding, while the screening finds local low points that trap
    rainfall. They answer different questions, so neither perfect agreement nor disagreement would
    prove either one right.
    """
    if not official or not screening_geojson["features"]:
        return None
    wet = [shape(f["geometry"]) for f in official["geojson"]["features"]]
    if not wet:
        return {"checked": 0, "inside": 0, "share": None, "baseline": 0.0,
                "enrichment": None, "discriminating": False,
                "note": "The official layer is dry across this box, so there is nothing to compare."}
    tree = shapely.STRtree(wet)
    top = [f for f in screening_geojson["features"] if f["properties"]["pooling_index"] >= 4]
    hit = sum(1 for f in top if len(tree.query(shape(f["geometry"]), predicate="intersects")))
    baseline = official["inundated_share"]  # share of the box the authority calls wet
    share = hit / len(top) if top else None
    enrichment = (share / baseline) if (share is not None and baseline > 0) else None
    # Once most of the box is inundated, "inside the extent" stops separating anything.
    discriminating = baseline < 0.4
    return {
        "checked": len(top),
        "inside": hit,
        "share": None if share is None else round(share, 3),
        "baseline": round(baseline, 3),
        "enrichment": None if enrichment is None else round(enrichment, 2),
        "discriminating": discriminating,
        "note": ("Agreement between two different questions, not a measure of accuracy. "
                 "The official layer is coarser and models river and backwater flooding; the "
                 "screening finds local low points that trap rainfall."
                 if discriminating else
                 "At this return period the authority calls most of the box inundated, so overlap "
                 "no longer separates a good ranking from a bad one. Compare at 10-50 years."),
    }
