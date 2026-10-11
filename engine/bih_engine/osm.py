"""OpenStreetMap layers: parse Overpass JSON and rasterise it onto an analysis grid.

Water bodies (lakes, ponds, backwaters) and waterways (rivers, canals, streams, drains, ditches)
are where rain can leave the land. OSM carries no storm-drain network, so this is the open water
only; the screening still overstates pooling where buried drains work.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np
import shapely
from pyproj import Transformer
from rasterio.features import rasterize
from shapely.geometry import LineString, Polygon, box
from shapely.ops import polygonize, unary_union

from .terrain import Grid

WATERWAY_LINES = {"river", "canal", "stream", "drain", "ditch"}

# Roads worth routing on. Footpaths and tracks are skipped: they neither carry vehicles nor
# channel meaningful runoff.
ROAD_CLASSES = {
    "motorway", "motorway_link", "trunk", "trunk_link", "primary", "primary_link",
    "secondary", "secondary_link", "tertiary", "tertiary_link",
    "unclassified", "residential", "living_street", "service",
}
# Rough carriageway width per class, for burning roads into the terrain.
ROAD_WIDTH_M = {"motorway": 20.0, "trunk": 18.0, "primary": 15.0, "secondary": 12.0,
                "tertiary": 10.0, "residential": 8.0, "unclassified": 8.0,
                "living_street": 6.0, "service": 5.0}


def road_width_m(highway: str) -> float:
    return ROAD_WIDTH_M.get(highway.removesuffix("_link"), 8.0)


@dataclass
class Road:
    """One road segment. `elevated` means a bridge or an embankment: water does not run along it."""

    geom: LineString
    highway: str
    name: str | None = None
    elevated: bool = False
    tunnel: bool = False
    osm_id: int | None = None

    @property
    def width_m(self) -> float:
        return road_width_m(self.highway)


@dataclass
class OsmData:
    water: list = field(default_factory=list)       # shapely polygons, lon/lat
    waterways: list = field(default_factory=list)   # shapely linestrings, lon/lat
    roads: list = field(default_factory=list)       # Road, lon/lat
    buildings: list = field(default_factory=list)   # shapely polygons, lon/lat
    stats: dict = field(default_factory=dict)


@dataclass
class Layers:
    """OSM features rasterised on one grid (same shape as `Grid.dem`)."""

    water: np.ndarray      # bool: open water
    waterway: np.ndarray   # bool: waterway centrelines
    road: np.ndarray       # bool: road surface, excluding bridges and tunnels
    building: np.ndarray   # bool: building footprints


def _is_water(tags: dict) -> bool:
    return tags.get("natural") == "water" or tags.get("landuse") == "reservoir"


def _truthy(v: str | None) -> bool:
    return v is not None and v not in ("no", "false", "0")


def _pts(geometry: list) -> list[tuple[float, float]]:
    return [(n["lon"], n["lat"]) for n in geometry]


def _multipolygon(rel: dict) -> list[Polygon]:
    """Assemble a water relation's rings. Returns [] if its outer ring is incomplete, which happens
    when Overpass clips a large lake to the query box; the caller counts those."""
    outers, inners = [], []
    for m in rel.get("members", []):
        pts = _pts(m["geometry"]) if m.get("type") == "way" and m.get("geometry") else []
        if len(pts) >= 2:
            (inners if m.get("role") == "inner" else outers).append(LineString(pts))
    if not outers:
        return []
    result = unary_union(list(polygonize(unary_union(outers))))
    if result.is_empty:
        return []
    if inners:
        holes = unary_union(list(polygonize(unary_union(inners))))
        if not holes.is_empty:
            result = result.difference(holes)
    return [g for g in getattr(result, "geoms", [result]) if not g.is_empty]


def parse_overpass(data: dict) -> OsmData:
    out = OsmData()
    skipped = 0
    for el in data.get("elements", []):
        tags = el.get("tags", {})
        if el["type"] == "way":
            pts = _pts(el.get("geometry", []))
            if len(pts) < 2:
                continue
            closed = len(pts) >= 4 and pts[0] == pts[-1]
            if tags.get("waterway") in WATERWAY_LINES:
                out.waterways.append(LineString(pts))
            elif tags.get("highway") in ROAD_CLASSES:
                out.roads.append(Road(
                    geom=LineString(pts), highway=tags["highway"], name=tags.get("name"),
                    elevated=_truthy(tags.get("bridge")) or tags.get("embankment") == "yes",
                    tunnel=_truthy(tags.get("tunnel")), osm_id=el.get("id"),
                ))
            elif (_is_water(tags) or "building" in tags) and closed:
                poly = Polygon(pts)
                poly = poly if poly.is_valid else poly.buffer(0)
                if not poly.is_empty:
                    (out.buildings if "building" in tags else out.water).append(poly)
        elif el["type"] == "relation" and _is_water(tags) and tags.get("type") == "multipolygon":
            polys = _multipolygon(el)
            if polys:
                out.water.extend(polys)
            else:
                skipped += 1
    out.stats = {"water_polygons": len(out.water), "waterways": len(out.waterways),
                 "roads": len(out.roads), "buildings": len(out.buildings),
                 "relations_skipped_incomplete": skipped}
    return out


def load_overpass(path: str | Path) -> OsmData:
    return parse_overpass(json.loads(Path(path).read_text(encoding="utf-8")))


def _burn(geoms: list, grid: Grid, all_touched: bool) -> np.ndarray:
    shape = grid.dem.shape
    if not geoms:
        return np.zeros(shape, dtype=bool)
    to_utm = Transformer.from_crs(4326, grid.crs, always_xy=True)
    shapes = [(shapely.transform(g, lambda c: np.column_stack(to_utm.transform(c[:, 0], c[:, 1]))), 1)
              for g in geoms]
    return rasterize(shapes, out_shape=shape, transform=grid.transform, fill=0, dtype="uint8",
                     all_touched=all_touched).astype(bool)


def _geom_of(x):
    return x.geom if isinstance(x, Road) else x


def _within_grid(geoms: list, grid: Grid) -> list:
    """Cheap lon/lat prefilter so we only project what can touch the grid."""
    if not geoms:
        return []
    h, w = grid.dem.shape
    to_ll = Transformer.from_crs(grid.crs, 4326, always_xy=True)
    xs, ys = zip(*[grid.transform @ (c, r) for c, r in ((0, 0), (w, 0), (w, h), (0, h))])
    lons, lats = to_ll.transform(xs, ys)
    window = box(min(lons), min(lats), max(lons), max(lats))
    keep = shapely.intersects(np.array([_geom_of(g) for g in geoms], dtype=object), window)
    return [g for g, k in zip(geoms, keep) if k]


def rasterize_layers(osm: OsmData, grid: Grid) -> Layers:
    """Open water as polygons (cell centre inside); waterways as lines, with every touched cell
    marked so a canal narrower than a cell still forms a continuous path.

    Roads are buffered to their carriageway width, so a wide road occupies the cells it really
    covers. Bridges and tunnels are excluded: water does not run along a road that is not on the
    ground. Tunnels are also excluded from the burn, but they stay in the routing graph.
    """
    on_ground = [r.geom.buffer(r.width_m / 2 / 111_320.0)  # degrees; the distortion at 10N is under 2%
                 for r in _within_grid([r for r in osm.roads if not (r.elevated or r.tunnel)], grid)]
    return Layers(
        water=_burn(_within_grid(osm.water, grid), grid, all_touched=False),
        waterway=_burn(_within_grid(osm.waterways, grid), grid, all_touched=True),
        # all_touched would double the area a road covers: the buffer already gives it its width.
        road=_burn(on_ground, grid, all_touched=False),
        building=_burn(_within_grid(osm.buildings, grid), grid, all_touched=False),
    )
