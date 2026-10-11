"""The road network as a graph: which routes run through pooling spots, and what that isolates.

The graph is built per box rather than stored. A drawn box is arbitrary, so a city-wide graph would
have to be clipped on every request anyway, and clipping costs about as much as building. Building
it keeps the service stateless. For a 75 km2 box this is a few thousand edges and takes milliseconds.

Planning only. This says which roads sit in water, never which way to drive or evacuate.
"""
from __future__ import annotations

import networkx as nx
import numpy as np
import shapely
from pyproj import Transformer
from shapely.geometry import LineString

from .osm import Road

# Endpoints within this distance are the same junction. OSM ways that meet usually share a node
# exactly; this absorbs the rounding in reprojection.
SNAP_M = 1.0


def _key(x: float, y: float) -> tuple[int, int]:
    return (int(round(x / SNAP_M)), int(round(y / SNAP_M)))


def build_graph(roads: list[Road], crs) -> nx.MultiGraph:
    """Junction graph in metres. Edges carry the road's name, class and length.

    Bridges and tunnels stay in the graph - they still carry traffic - but they are excluded from
    the flooded test, because a bridge over a pooling spot is not cut off by it.
    """
    to_m = Transformer.from_crs(4326, crs, always_xy=True)
    g = nx.MultiGraph()
    for i, r in enumerate(roads):
        xs, ys = to_m.transform(*r.geom.coords.xy)
        line = LineString(np.column_stack([xs, ys]))
        a, b = _key(xs[0], ys[0]), _key(xs[-1], ys[-1])
        if a == b and line.length < SNAP_M:
            continue  # degenerate stub
        g.add_edge(a, b, key=i, name=r.name, highway=r.highway, length_m=line.length,
                   geom=line, elevated=r.elevated, tunnel=r.tunnel, osm_id=r.osm_id)
    return g


def flooded_edges(graph: nx.MultiGraph, pooling_m: list) -> set:
    """Edge keys whose road runs through a pooling spot (geometries in the graph's metric CRS)."""
    if not pooling_m:
        return set()
    tree = shapely.STRtree(pooling_m)
    hit = set()
    for u, v, k, d in graph.edges(keys=True, data=True):
        if d["elevated"] or d["tunnel"]:
            continue  # not at ground level, so pooling beneath it does not cut it
        if len(tree.query(d["geom"], predicate="intersects")):
            hit.add((u, v, k))
    return hit


def cut_off(graph: nx.MultiGraph, flooded: set) -> list[dict]:
    """Parts of the network that lose their connection to the rest once flooded roads are removed.

    NOT WIRED INTO THE RESULT YET, on purpose. Removing edges from a dense urban graph shatters it:
    on a 35 km2 Kochi box this returns ~400 "isolated areas", which is graph noise rather than a
    finding. The question only becomes meaningful with somewhere to be cut off *from* - "can this
    block still reach a hospital" - which needs the facilities layer (L13). Kept for that.
    """
    if not flooded:
        return []
    dry = nx.MultiGraph(graph)
    dry.remove_edges_from(flooded)
    parts = sorted(nx.connected_components(dry), key=len, reverse=True)
    if len(parts) < 2:
        return []
    out = []
    for comp in parts[1:]:
        names = sorted({d["name"] for _, _, d in dry.subgraph(comp).edges(data=True) if d["name"]})
        if len(comp) < 3:
            continue  # a stub or two is noise, not an isolated area
        out.append({"junctions": len(comp), "roads": names[:6],
                    "unnamed_roads": sum(1 for _, _, d in dry.subgraph(comp).edges(data=True) if not d["name"])})
    return out


def affected_summary(graph: nx.MultiGraph, flooded: set) -> list[dict]:
    """One row per road name: how many of its segments sit in water, and how much of its length."""
    by_name: dict[str, dict] = {}
    for u, v, k in flooded:
        d = graph.edges[u, v, k]
        name = d["name"] or "unnamed " + d["highway"].replace("_", " ")
        row = by_name.setdefault(name, {"road": name, "highway": d["highway"], "segments": 0, "length_m": 0.0})
        row["segments"] += 1
        row["length_m"] += d["length_m"]
    rows = sorted(by_name.values(), key=lambda r: -r["length_m"])
    for r in rows:
        r["length_m"] = round(r["length_m"])
    return rows


# A spot more than this from the nearest named road is not "on" that road. Two blocks in a dense
# Indian or Singaporean neighbourhood; beyond it the name would be decoration rather than a
# location.
NEAREST_ROAD_MAX_M = 120.0


def nearest_named_roads(graph: nx.MultiGraph, points_m, max_m: float = NEAREST_ROAD_MAX_M):
    """For each point, the nearest road that actually has a name, or None.

    A pooling spot called `d_73` tells an officer nothing. "The low point beside Pipeline Road,
    40 m away" tells them where to stand. Only named roads are candidates: OSM's unnamed
    residential ways are the majority by count and would win on distance almost every time while
    naming nothing.
    """
    named = [(d["geom"], d["name"], d["highway"])
             for _, _, d in graph.edges(data=True) if d["name"]]
    if not named or not len(points_m):
        return [None] * len(points_m)
    tree = shapely.STRtree([g for g, _, _ in named])
    idx = tree.nearest(points_m)
    out = []
    for point, i in zip(points_m, np.atleast_1d(idx)):
        geom, name, highway = named[int(i)]
        dist = point.distance(geom)
        out.append(None if dist > max_m else
                   {"name": name, "highway": highway, "distance_m": round(float(dist))})
    return out
