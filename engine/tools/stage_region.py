"""Stage one region's OSM layers: download once, write as indexed FlatGeobuf.

This is the only code that talks to Overpass. The serving path never does: Overpass rate-limits
(4 slots), rejects clients without a descriptive User-Agent, and takes seconds to minutes for a
city-sized query — none of which belongs in a request a judge is watching.

Terrain is fetched separately by fetch_terrain.py; this merges its sidecar into the manifest.

Run:  python engine/tools/stage_region.py <region> [--buildings] [--manifest-only]
"""
from __future__ import annotations

import argparse
import datetime
import json
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

import geopandas as gpd

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "engine"))
import regions as region_defs  # noqa: E402
from bih_engine.osm import parse_overpass  # noqa: E402

# Mirrors, tried in order. The main endpoint allows 4 concurrent slots across all users of an IP
# and starts returning 504 under load, which it did repeatedly while staging these regions.
ENDPOINTS = [
    "https://overpass-api.de/api/interpreter",
    "https://maps.mail.ru/osm/tools/overpass/api/interpreter",
    "https://overpass.private.coffee/api/interpreter",
]
UA = "before-it-hits-engine/0.1 (hackathon prototype)"  # Overpass returns 406 without one

WATER_SELECTORS = [
    'way["waterway"~"^(river|canal|stream|drain|ditch)$"]',
    'way["natural"="water"]',
    'relation["natural"="water"]',
    'way["landuse"="reservoir"]',
]
SELECTORS = {"water": WATER_SELECTORS, "roads": ['way["highway"]'], "buildings": ['way["building"]']}


def overpass(selectors: list, bbox, rounds: int = 3) -> dict:
    """Query every mirror in turn, then back off and go round again."""
    w, s, e, n = bbox
    parts = "".join(f"{sel}({s},{w},{n},{e});" for sel in selectors)
    body = "[out:json][timeout:300];(" + parts + ");out geom;"
    data = urllib.parse.urlencode({"data": body}).encode()
    last = None
    for rnd in range(1, rounds + 1):
        for url in ENDPOINTS:
            try:
                req = urllib.request.Request(url, data, {"User-Agent": UA, "Accept": "*/*"})
                return json.loads(urllib.request.urlopen(req, timeout=900).read())
            except (urllib.error.HTTPError, urllib.error.URLError, TimeoutError, ValueError) as exc:
                last = exc
                code = getattr(exc, "code", None)
                host = urllib.parse.urlparse(url).netloc
                print(f"  {host}: {code or type(exc).__name__}", file=sys.stderr)
                if code is not None and code not in (429, 500, 502, 503, 504):
                    raise
        if rnd < rounds:
            wait = 45 * rnd
            print(f"  all mirrors busy; waiting {wait}s", file=sys.stderr)
            time.sleep(wait)
    raise RuntimeError(f"every Overpass mirror failed; last error: {last}")


def write_fgb(path: Path, geoms: list, attrs: dict | None = None) -> int:
    """FlatGeobuf with a packed R-tree, so a later bbox read fetches only the bytes it needs."""
    if not geoms:
        print(f"  {path.name}: nothing to write")
        return 0
    gdf = gpd.GeoDataFrame(attrs or {}, geometry=geoms, crs="EPSG:4326")
    gdf.to_file(path, driver="FlatGeobuf", spatial_index=True)
    print(f"  {path.name}: {len(gdf)} features, {path.stat().st_size / 1e6:.1f} MB")
    return len(gdf)


def main(name: str, with_buildings: bool, manifest_only: bool = False) -> None:
    spec = region_defs.get(name)
    out = ROOT / "data" / "regions" / name
    out.mkdir(parents=True, exist_ok=True)
    bbox = spec["bbox_wsen"]
    wanted = [] if manifest_only else ["water", "roads"] + (["buildings"] if with_buildings else [])
    counts, fetched = {}, {}

    for layer in wanted:
        print(f"fetching {layer} ...")
        t0 = time.perf_counter()
        osm = parse_overpass(overpass(SELECTORS[layer], bbox))
        fetched[layer] = f"{time.perf_counter() - t0:.0f}s"
        if layer == "water":
            counts["water"] = write_fgb(out / "water.fgb", osm.water)
            counts["waterways"] = write_fgb(out / "waterways.fgb", osm.waterways)
        elif layer == "roads":
            counts["roads"] = write_fgb(out / "roads.fgb", [r.geom for r in osm.roads], {
                "highway": [r.highway for r in osm.roads], "name": [r.name for r in osm.roads],
                "elevated": [r.elevated for r in osm.roads], "tunnel": [r.tunnel for r in osm.roads],
                "osm_id": [r.osm_id for r in osm.roads]})
        else:
            counts["buildings"] = write_fgb(out / "buildings.fgb", osm.buildings)

    # Terrain provenance lives in the sidecar fetch_terrain.py wrote; merge rather than restate it.
    sidecar = {}
    if (out / "dtm.json").exists():
        sidecar = json.loads((out / "dtm.json").read_text(encoding="utf-8"))
    elif not (out / "dtm.tif").exists():
        print(f"  warning: no dtm.tif for {name}; run fetch_terrain.py first", file=sys.stderr)

    existing = {}
    if (out / "region.json").exists():
        existing = json.loads((out / "region.json").read_text(encoding="utf-8"))
    if manifest_only:
        # Rewrite the manifest from the region definition without touching Overpass, for when the
        # definition gains something (presets, a note) but the staged vectors are already correct.
        prev = existing.get("vectors", {})
        counts = prev.get("counts", counts)
        fetched = prev.get("fetch_seconds", fetched)

    manifest = {
        "name": name,
        "title": spec["title"],
        "country": spec.get("country"),
        "bbox_wsen": bbox,
        "note": spec.get("note"),
        "presets": spec.get("presets", []),
        "staged": datetime.date.today().isoformat(),
        "terrain": {"file": "dtm.tif", **sidecar},
        "vectors": {"format": "FlatGeobuf", "crs": "EPSG:4326", "counts": counts,
                    "source": "OpenStreetMap via Overpass API",
                    "licence": "ODbL 1.0, attribution: OpenStreetMap contributors",
                    "fetch_seconds": fetched},
        "not_available": ["storm-drain network (not public anywhere)",
                          "bathymetry (neither terrain source has it)"],
    }
    # stage_official.py writes this key; restaging the vectors must not wipe it.
    if existing.get("official"):
        manifest["official"] = existing["official"]
    (out / "region.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    print(f"\nstaged {name} -> {out}")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("region", help="region name from engine/regions.py")
    ap.add_argument("--buildings", action="store_true", help="also stage building footprints (slow)")
    ap.add_argument("--manifest-only", action="store_true",
                    help="rewrite region.json from engine/regions.py; no Overpass calls")
    args = ap.parse_args()
    main(args.region, args.buildings, args.manifest_only)
