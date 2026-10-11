"""Stage one region: download its OSM layers once and write them as indexed FlatGeobuf.

This is the only code that talks to Overpass. The serving path never does: Overpass rate-limits
(4 slots), rejects clients without a descriptive User-Agent, and takes seconds to minutes for a
city-sized query - none of which belongs in a request a judge is watching.

Run:  python engine/tools/stage_region.py [--buildings]
"""
from __future__ import annotations

import argparse
import datetime
import json
import shutil
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

import geopandas as gpd

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "engine"))
from bih_engine.osm import parse_overpass  # noqa: E402

ENDPOINT = "https://overpass-api.de/api/interpreter"
UA = "before-it-hits-engine/0.1 (hackathon prototype)"  # Overpass returns 406 without one

REGION = {
    "name": "kochi",
    "title": "Kochi, Kerala",
    "bbox_wsen": [76.20, 9.85, 76.45, 10.10],
    "terrain_source": ROOT / "data" / "deltadtm_kochi.tif",
}
WATER_SELECTORS = [
    'way["waterway"~"^(river|canal|stream|drain|ditch)$"]',
    'way["natural"="water"]',
    'relation["natural"="water"]',
    'way["landuse"="reservoir"]',
]
SELECTORS = {"water": WATER_SELECTORS, "roads": ['way["highway"]'], "buildings": ['way["building"]']}


def overpass(selectors: list, bbox, tries: int = 4) -> dict:
    w, s, e, n = bbox
    parts = "".join(sel + "(" + str(s) + "," + str(w) + "," + str(n) + "," + str(e) + ");" for sel in selectors)
    body = "[out:json][timeout:300];(" + parts + ");out geom;"
    data = urllib.parse.urlencode({"data": body}).encode()
    for attempt in range(1, tries + 1):
        try:
            req = urllib.request.Request(ENDPOINT, data, {"User-Agent": UA, "Accept": "*/*"})
            return json.loads(urllib.request.urlopen(req, timeout=900).read())
        except (urllib.error.HTTPError, urllib.error.URLError, TimeoutError) as exc:
            code = getattr(exc, "code", None)
            if attempt == tries or (code is not None and code not in (429, 502, 503, 504)):
                raise
            wait = 30 * attempt
            print("  overpass unavailable (" + str(code or exc) + "); retrying in " + str(wait) + "s", file=sys.stderr)
            time.sleep(wait)


def write_fgb(path: Path, geoms: list, attrs: dict | None = None) -> int:
    """FlatGeobuf with a packed R-tree, so a later bbox read fetches only the bytes it needs."""
    if not geoms:
        print("  " + path.name + ": nothing to write")
        return 0
    gdf = gpd.GeoDataFrame(attrs or {}, geometry=geoms, crs="EPSG:4326")
    gdf.to_file(path, driver="FlatGeobuf", spatial_index=True)
    print("  " + path.name + ": " + str(len(gdf)) + " features, %.1f MB" % (path.stat().st_size / 1e6))
    return len(gdf)


def main(with_buildings: bool) -> None:
    out = ROOT / "data" / "regions" / REGION["name"]
    out.mkdir(parents=True, exist_ok=True)
    bbox = REGION["bbox_wsen"]
    wanted = ["water", "roads"] + (["buildings"] if with_buildings else [])
    counts, fetched = {}, {}

    for layer in wanted:
        print("fetching " + layer + " ...")
        t0 = time.perf_counter()
        osm = parse_overpass(overpass(SELECTORS[layer], bbox))
        fetched[layer] = "%.0fs" % (time.perf_counter() - t0)
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

    terrain = Path(REGION["terrain_source"])
    sidecar = {}
    if terrain.exists():
        shutil.copy2(terrain, out / "dtm.tif")
        if terrain.with_suffix(".json").exists():
            sidecar = json.loads(terrain.with_suffix(".json").read_text(encoding="utf-8"))

    manifest = {
        "name": REGION["name"], "title": REGION["title"], "bbox_wsen": bbox,
        "staged": datetime.date.today().isoformat(),
        "terrain": dict({"file": "dtm.tif"}, **sidecar),
        "vectors": {"format": "FlatGeobuf", "crs": "EPSG:4326", "counts": counts,
                    "source": "OpenStreetMap via Overpass API",
                    "licence": "ODbL 1.0, attribution: OpenStreetMap contributors",
                    "fetch_seconds": fetched},
        "not_available": ["storm-drain network (not public anywhere)",
                          "bathymetry (neither terrain source has it)"],
    }
    (out / "region.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    print("\nstaged " + REGION["name"] + " -> " + str(out))


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--buildings", action="store_true", help="also stage 150k+ building footprints (slow)")
    main(ap.parse_args().buildings)
