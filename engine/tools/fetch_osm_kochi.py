"""Fetch OpenStreetMap water and waterways for the Kochi extent from Overpass.

Writes data/osm/water.json (raw Overpass JSON with geometry) and data/osm/water.meta.json
(provenance). The extent matches data/deltadtm_kochi.tif. OSM data is ODbL: attribute it.
Run:  python engine/tools/fetch_osm_kochi.py
"""
import json
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

SOUTH, WEST, NORTH, EAST = 9.85, 76.20, 10.10, 76.45  # Overpass wants (south, west, north, east)
OUT = Path(__file__).resolve().parents[2] / "data" / "osm"
ENDPOINT = "https://overpass-api.de/api/interpreter"
UA = "before-it-hits-engine/0.1 (hackathon prototype)"  # Overpass rejects anonymous clients

QUERY = f"""[out:json][timeout:180];
(
  way["waterway"~"^(river|canal|stream|drain|ditch)$"]({SOUTH},{WEST},{NORTH},{EAST});
  way["natural"="water"]({SOUTH},{WEST},{NORTH},{EAST});
  relation["natural"="water"]({SOUTH},{WEST},{NORTH},{EAST});
  way["landuse"="reservoir"]({SOUTH},{WEST},{NORTH},{EAST});
);
out geom;"""


def post(query: str, tries: int = 3) -> bytes:
    body = urllib.parse.urlencode({"data": query}).encode()
    for attempt in range(1, tries + 1):
        req = urllib.request.Request(ENDPOINT, body, {"User-Agent": UA, "Accept": "*/*"})
        try:
            return urllib.request.urlopen(req, timeout=240).read()
        except urllib.error.HTTPError as e:
            if e.code in (429, 502, 503, 504) and attempt < tries:
                wait = 20 * attempt
                print(f"overpass busy (HTTP {e.code}); retrying in {wait}s", file=sys.stderr)
                time.sleep(wait)
                continue
            raise


raw = post(QUERY)
data = json.loads(raw)
OUT.mkdir(parents=True, exist_ok=True)
(OUT / "water.json").write_bytes(raw)
kinds = {}
for el in data["elements"]:
    t = el.get("tags", {})
    k = t.get("waterway") or ("water:" + (t.get("water") or t.get("natural") or t.get("landuse") or "?"))
    kinds[f"{el['type']}/{k}"] = kinds.get(f"{el['type']}/{k}", 0) + 1
(OUT / "water.meta.json").write_text(json.dumps({
    "source": "OpenStreetMap via Overpass API", "licence": "ODbL 1.0, attribution: OpenStreetMap contributors",
    "osm_data_timestamp": data.get("osm3s", {}).get("timestamp_osm_base"),
    "bbox_wsen": [WEST, SOUTH, EAST, NORTH], "elements": len(data["elements"]), "kinds": kinds,
    "query": QUERY,
}, indent=2), encoding="utf-8")
print(f"wrote {OUT / 'water.json'} ({len(raw) / 1e6:.1f} MB, {len(data['elements'])} elements)")
for k, v in sorted(kinds.items(), key=lambda kv: -kv[1])[:12]:
    print(f"  {k:28s} {v}")
