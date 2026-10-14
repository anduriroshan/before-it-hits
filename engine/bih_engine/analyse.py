"""Screening tier: where does this box trap water, and which spots fill first?

Output is a *relative* pooling index (1-5), contributing area and fill order. Depths in metres
are used internally (to flag depressions too shallow to trust) and are never emitted: at 30 m
the terrain error is the same size as the depths we would otherwise report.
"""
from __future__ import annotations

import time
from pathlib import Path

import numpy as np
from pyproj import Transformer
from rasterio.features import shapes
from scipy import ndimage
from scipy.stats import rankdata
import shapely
from shapely.geometry import Point, mapping, shape

from .hydrology import exit_accumulation, fill_and_route
from .official import compare_to_screening, read_official
from .osm import Layers, OsmData, load_overpass, rasterize_layers
from .routes import (affected_summary, build_graph, flooded_edges, nearest_named_roads,
                     road_flood_thresholds)
from .store import Region
from .terrain import Grid, read_dem

# Bare-earth DEM error we expect (DeltaDTM MAE ~0.45 m; GLO-30 is no better inland).
DEFAULT_DEM_ERROR_M = 0.45
DEFAULT_RUNOFF_C = 0.8  # placeholder until land cover (L8) feeds a per-cell coefficient
# Deeper than this, in the flat ground a city sits on, is a water body, a quarry or a terrain
# artifact rather than street-level ponding. In Kochi's DeltaDTM that is about 0.2% of pits.
DEFAULT_MAX_PLAUSIBLE_DEPTH_M = 5.0
# Canals are cut into the ground, but a 30 m terrain model sees the bank. Lowering the waterway
# cells lets land beside a canal drain into it. Kochi's named problem canals are roughly this deep.
DEFAULT_BURN_WATERWAY_M = 2.0
# Burning roads as trenches is off by default. Measured on Kochi: a 0.2 m trench raises the pit
# count from 291 to 480 in a 35 km2 box and pushes the share of pits inside the terrain error from
# 76% to 83% - it manufactures shallow pits along every street. The justification for it (water
# runs along streets, not through plots) only holds once buildings are burned in as walls too.
DEFAULT_BURN_ROAD_M = 0.0

# A road is only called affected where it runs through a top-ranked pooling spot. Every spot that
# holds any water at all would mark most of a city's streets, which is not a useful claim.
AFFECTED_MIN_INDEX = 4
EXCLUDED_REASON = "deeper than street-level ponding: possible water body or terrain artifact"

CAVEAT = (
    "30 m terrain screening. Relative ranking only, not depths. "
    "No storm-drain data exists, so pooling is overstated where drains work."
)


def _pooling_index(ponded_m3: np.ndarray) -> np.ndarray:
    """1-5 by quintile of how much water ends up stuck in each depression (most = 5).

    Mid-rank percentiles, so equal values share an index and a lone depression lands in the
    middle rather than at an extreme. Relative to this box by design, never absolute.
    """
    n = ponded_m3.size
    if n == 0:
        return np.zeros(0, dtype=int)
    if ponded_m3.max() <= 0:  # no rain: nothing ponds, nothing to rank
        return np.ones(n, dtype=int)
    pct = (rankdata(ponded_m3, method="average") - 0.5) / n
    return 1 + np.minimum((pct * 5).astype(int), 4)


def analyse_grid(
    grid: Grid,
    rainfall_mm: float = 100.0,
    *,
    runoff_c: float = DEFAULT_RUNOFF_C,
    min_depth_m: float = 0.1,
    min_area_m2: float | None = None,
    dem_error_m: float = DEFAULT_DEM_ERROR_M,
    max_plausible_depth_m: float = DEFAULT_MAX_PLAUSIBLE_DEPTH_M,
    water_level_m: float | None = None,
    layers: Layers | None = None,
    burn_waterway_m: float = DEFAULT_BURN_WATERWAY_M,
    burn_road_m: float = DEFAULT_BURN_ROAD_M,
    roads: list | None = None,
    treated: set | None = None,
) -> dict:
    """Run the screening on an already-read grid. Pure function of its inputs.

    `treated` is a set of spot ids to treat as dealt with when counting which roads a flood cuts
    (the best-case "after" of F10). The terrain, the ranking and the map are untouched: only the
    road count leaves those spots out, so "with the plan" differs from "without it" in exactly
    the way the plan claims and nowhere else."""
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
    if layers is not None:
        # Buildings are walls: water routes around them rather than through them.
        dem = np.where(layers.building, dem + 5.0, dem)
        # Roads are shallow trenches: water runs along a street rather than across the plots.
        dem = np.where(layers.road & ~layers.building, dem - burn_road_m, dem)
        # Open water and waterways are where rain leaves the land. Waterway cells are lowered
        # further so land beside them drains in rather than ponding against the bank.
        dem = np.where(layers.waterway, dem - burn_waterway_m, dem)
        outlet |= layers.water | layers.waterway
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

    # Ids follow position (north to south, then west to east) so they stay put when rainfall or the
    # exclusion threshold changes.
    for n_id, d in enumerate(sorted(deps, key=lambda d: (d["row"], d["col"])), 1):
        d["id"] = f"d_{n_id:02d}"
    excluded = sorted((d for d in deps if d["max_depth_m"] > max_plausible_depth_m), key=lambda d: d["id"])
    deps = [d for d in deps if d["max_depth_m"] <= max_plausible_depth_m]

    # Two different questions, kept apart:
    #  fill_order    - which spot fills first (least rain needed). This is the animation sequence.
    #  pooling_index - how much water ends up stuck there at this rainfall: what arrives, capped
    #                  by what the depression can hold. A tiny pit with a huge catchment fills
    #                  first but holds little, so it does not top the hazard ranking.
    for d in deps:
        d["inflow_m3"] = rainfall_mm / 1000.0 * runoff_c * d["contrib_m2"]
        d["ponded_m3"] = min(d["inflow_m3"], d["volume_m3"])
    for order, d in enumerate(sorted(deps, key=lambda d: (d["rain_to_fill_mm"], -d["contrib_m2"], d["row"], d["col"])), 1):
        d["fill_order"] = order
    idx_arr = _pooling_index(np.array([d["ponded_m3"] for d in deps]))
    for i, d in enumerate(deps):
        d["pooling_index"] = int(idx_arr[i])
    # Report most severe first; ties go to whichever fills sooner.
    deps.sort(key=lambda d: (-d["ponded_m3"], d["fill_order"]))
    to_ll = Transformer.from_crs(grid.crs, 4326, always_xy=True)
    kept = {d["label"]: d for d in deps}
    for d in deps:
        d["fills_at_rainfall"] = bool(rainfall_mm >= d["rain_to_fill_mm"])
        d["low_confidence"] = bool(d["max_depth_m"] < dem_error_m)
        x, y = grid.transform @ (d["col"] + 0.5, d["row"] + 0.5)
        d["lon"], d["lat"] = to_ll.transform(x, y)
        d["point_m"] = Point(x, y)      # kept to find the road this spot sits on, below
        d["nearest_road"] = None

    t0 = time.perf_counter()
    feats, pooling_m = [], []
    if kept:
        keep_mask = np.isin(labels, list(kept))
        for geom, val in shapes(labels.astype(np.int32), mask=keep_mask, transform=grid.transform):
            d = kept[int(val)]
            metric = shape(geom)
            pooling_m.append(metric)
            g = shapely.transform(metric, lambda c: np.column_stack(to_ll.transform(c[:, 0], c[:, 1])))
            props = {k: d[k] for k in ("id", "pooling_index", "fill_order",
                                       "fills_at_rainfall", "low_confidence")}
            props["contributing_ha"] = round(d["contrib_m2"] / 1e4, 2)
            # How much rain it takes to fill this spot. A rainfall amount in mm, not a water depth
            # in metres, so it is safe to surface - it is what drives the timeline playback.
            props["rain_to_fill_mm"] = round(d["rain_to_fill_mm"], 1)
            feats.append({"type": "Feature", "geometry": mapping(g), "properties": props})
    t["vectorise_s"] = time.perf_counter() - t0

    t0 = time.perf_counter()
    routes_affected = []
    roads_timeline = {"type": "FeatureCollection", "features": []}
    if roads:
        # Only top-ranked spots that actually hold water at this rainfall can cut a road.
        wet = [m for m, f in zip(pooling_m, feats)
               if f["properties"]["fills_at_rainfall"]
               and f["properties"]["pooling_index"] >= AFFECTED_MIN_INDEX
               and f["properties"]["id"] not in (treated or ())]
        graph = build_graph(roads, grid.crs)
        flooded = flooded_edges(graph, wet)
        routes_affected = affected_summary(graph, flooded)
        # Name where each spot actually is. An id is useless to anyone standing in the street.
        for d, road in zip(deps, nearest_named_roads(graph, [d["point_m"] for d in deps])):
            d["nearest_road"] = road
        # Every ground-level road that crosses a spot, with the rainfall threshold at which it
        # floods and how bad the worst spot on it is. The UI plays this back against a rainfall
        # profile - a rainfall-accumulation clock, not a drainage one (see road_flood_thresholds).
        spots = [(m, f["properties"]["rain_to_fill_mm"], f["properties"]["pooling_index"])
                 for m, f in zip(pooling_m, feats)]
        for e in road_flood_thresholds(graph, spots):
            g = shapely.transform(e["geom"],
                                  lambda c: np.column_stack(to_ll.transform(c[:, 0], c[:, 1])))
            roads_timeline["features"].append({
                "type": "Feature", "geometry": mapping(g),
                "properties": {k: e[k] for k in ("name", "highway", "length_m",
                                                  "rain_to_flood_mm", "severity")}})
    for d in deps:
        d.pop("point_m", None)          # a geometry has no business in a JSON response
    t["routes_s"] = time.perf_counter() - t0

    screening = {
        "depressions": [
            {"id": d["id"], "pooling_index": d["pooling_index"],
             "contributing_ha": round(d["contrib_m2"] / 1e4, 2),
             "fill_order": d["fill_order"], "nearest_road": d["nearest_road"],
             "rain_to_fill_mm": round(d["rain_to_fill_mm"], 1)}
            for d in deps
        ],
        "excluded": [{"id": d["id"], "reason": EXCLUDED_REASON} for d in excluded],
        "routes_affected": routes_affected,
        "confidence": {"terrain": "weak" if grid.source_res_m >= 20 else "ok",
                       "drains": "gap", "water_bodies": "osm" if layers is not None else "terrain_only",
                       "validated_layer": "not_checked"},
        "caveat": CAVEAT,
    }
    return {
        "screening": screening,
        "geojson": {"type": "FeatureCollection", "features": feats},
        "roads_timeline": roads_timeline,
        "grid": {"shape": [h, w], "res_m": grid.res_m, "area_km2": round(h * w * cell_m2 / 1e6, 2),
                 "osm": None if layers is None else {"water_cells": int(layers.water.sum()),
                                                      "waterway_cells": int(layers.waterway.sum()),
                                                      "road_cells": int(layers.road.sum()),
                                                      "building_cells": int(layers.building.sum()),
                                                      "roads_in_graph": len(roads or [])}},
        "timings_s": t,
        # Internal only: raw depths and locations. Never surface depth to the user (hard limit #1).
        "debug": deps,
        "debug_excluded": excluded,
    }


def analyse_area(
    bbox: tuple[float, float, float, float],
    rainfall_mm: float = 100.0,
    *,
    res_m: float = 10.0,
    pad_m: float = 200.0,
    source: str | Path | None = None,
    osm: str | Path | OsmData | None = None,
    region: Region | None = None,
    return_period: int | None = None,
    **kw,
) -> dict:
    """Read the box, pull its staged layers, then screen it.

    `region` is a staged `Region` and is the normal path: it supplies both terrain and vectors.
    `source` / `osm` override either one, for tests and benchmarks. `timings_s` covers every stage.
    """
    cap = kw.pop("cap_value", None)
    if region is not None:
        source = source or region.terrain
        cap = region.terrain_cap if cap is None else cap
    grid = read_dem(bbox, res_m=res_m, pad_m=pad_m, source=source, cap_value=cap)

    t0 = time.perf_counter()
    data = None
    if osm is not None:
        data = osm if isinstance(osm, OsmData) else load_overpass(osm)
    elif region is not None:
        # Pad the vector read like the terrain read, so a canal just outside the box still drains
        # it. One degree of latitude is about 111 km.
        w, s, e, n = bbox
        d = pad_m / 111_000.0
        data = region.read_osm((w - d, s - d, e + d, n + d))
    layers = rasterize_layers(data, grid) if data is not None else None
    osm_s = time.perf_counter() - t0

    res = analyse_grid(grid, rainfall_mm, layers=layers, roads=data.roads if data else None, **kw)
    res["timings_s"] = dict(grid.meta["timings"], osm_s=osm_s, **res["timings_s"])
    res["grid"]["source"] = grid.meta["source"]
    res["grid"]["despiked_px"] = grid.meta["despiked_px"]
    res["grid"]["capped_px"] = grid.meta["capped_px"]
    if region is not None and data is not None and region.manifest.get("population"):
        from . import exposure
        t0 = time.perf_counter()
        res["exposure"] = exposure.estimate(res["geojson"]["features"], data.buildings,
                                             grid.crs, region.manifest["population"],
                                             treated=kw.get("treated"), region=region, bbox=bbox)
        res["timings_s"]["exposure_s"] = time.perf_counter() - t0
    if region is not None and return_period is not None:
        t0 = time.perf_counter()
        official = read_official(region, bbox, return_period)
        res["official"] = official
        res["agreement"] = compare_to_screening(official, res["geojson"])
        if official is not None:
            # The validated layer is somebody else's, which is exactly why it is worth more than
            # ours. Say which one is on screen rather than implying our ranking was checked.
            res["screening"]["confidence"]["validated_layer"] = (
                f"{official['return_period_years']}-year layer shown alongside ({official['source']})")
        res["timings_s"]["official_s"] = time.perf_counter() - t0
    if region is not None:
        res["region"] = {"name": region.name, "title": region.manifest.get("title"),
                         "staged": region.manifest.get("staged"),
                         "vector_source": region.manifest["vectors"]["source"],
                         "vector_licence": region.manifest["vectors"]["licence"],
                         "not_available": region.manifest.get("not_available", []),
                         "official": region.manifest.get("official", {}).get("return_periods")}
    return res
