"""Fetch a region's DeltaDTM terrain clip from the Earth Engine community catalog.

Uses the active gcloud login, so there is no browser step. The requested box is snapped to
DeltaDTM's native 1-arcsecond grid before export: asking for any other grid would make Earth Engine
resample the published elevations, and the whole point of the validated/screening split is that
nobody silently reinterprets somebody else's data.

Run:  python engine/tools/fetch_terrain.py <region>
"""
from __future__ import annotations

import datetime
import json
import math
import subprocess
import sys
import urllib.request
from pathlib import Path

import ee
from google.oauth2.credentials import Credentials

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "engine"))
import regions  # noqa: E402

PROJECT = "before-it-hits"
ASSET = "projects/sat-io/open-datasets/DELTARES/deltadtm_v1-1"  # an ee.Image, band b1
PX = 1 / 3600
# DeltaDTM's pixel edges sit half a pixel off the integer degrees.
GRID_ORIGIN_LON, GRID_ORIGIN_LAT = -180.0 - PX / 2, 84.0 + PX / 2


def snap(region_bbox):
    """Snap a bbox outwards onto the source grid, returning (west, north, width_px, height_px)."""
    w, s, e, n = region_bbox
    west = GRID_ORIGIN_LON + math.floor((w - GRID_ORIGIN_LON) / PX) * PX
    north = GRID_ORIGIN_LAT - math.floor((GRID_ORIGIN_LAT - n) / PX) * PX
    return west, north, math.ceil((e - west) / PX), math.ceil((north - s) / PX)


def main(name: str) -> None:
    spec = regions.get(name)
    west, north, cols, rows = snap(spec["bbox_wsen"])
    if cols * rows > 40_000_000:
        sys.exit(f"{cols}x{rows} px is too large for a single getDownloadURL; split the region")

    gcloud = "gcloud.cmd" if sys.platform == "win32" else "gcloud"
    token = subprocess.run([gcloud, "auth", "print-access-token"],
                           capture_output=True, text=True, check=True).stdout.strip()
    ee.Initialize(credentials=Credentials(token), project=PROJECT)

    img = ee.Image(ASSET).select("b1")
    region = ee.Geometry.Rectangle(
        [west, north - rows * PX, west + cols * PX, north], proj="EPSG:4326", geodesic=False)
    url = img.toFloat().getDownloadURL({
        "name": f"deltadtm_{name}", "region": region, "crs": "EPSG:4326",
        "crs_transform": [PX, 0, west, 0, -PX, north], "format": "GEO_TIFF",
    })

    out = ROOT / "data" / "regions" / name
    out.mkdir(parents=True, exist_ok=True)
    data = urllib.request.urlopen(url, timeout=600).read()
    (out / "dtm.tif").write_bytes(data)
    (out / "dtm.json").write_text(json.dumps({
        "source": "DeltaDTM v1.1 (Pronk et al. 2024), via Earth Engine community catalog",
        "asset": ASSET, "band": "b1", "licence": "CC BY 4.0",
        "grid": "native 1 arcsec, not resampled",
        "cap_value": 30.0,
        "bbox_wsen": [west, north - rows * PX, west + cols * PX, north],
        "shape": [rows, cols], "fetched": datetime.date.today().isoformat(),
        "note": "Raw band. Non-finite is sea or outside coverage; the value 30 is the model's cap.",
    }, indent=2), encoding="utf-8")
    print(f"{name}: {cols}x{rows} px, {len(data) / 1e6:.1f} MB -> {out / 'dtm.tif'}")


if __name__ == "__main__":
    if len(sys.argv) != 2:
        sys.exit(__doc__)
    main(sys.argv[1])
