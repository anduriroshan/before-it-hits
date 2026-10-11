"""Download a Kochi clip of DeltaDTM v1.1 from the Earth Engine community catalog.

Uses the active gcloud account (no browser step). Writes data/deltadtm_kochi.tif plus a provenance
sidecar. Run:  python engine/tools/fetch_deltadtm_kochi.py
"""
import datetime
import json
import subprocess
import sys
import urllib.request
from pathlib import Path

import ee
from google.oauth2.credentials import Credentials

PROJECT = "before-it-hits"
ASSET = "projects/sat-io/open-datasets/DELTARES/deltadtm_v1-1"  # an ee.Image, band b1
PX = 1 / 3600
# The native grid has its pixel EDGES half a pixel off the integer degrees (origin -180.000139, 84.000139),
# so snap the box to it: exporting on any other grid would resample the data.
WEST, NORTH = 76.20 - PX / 2, 10.10 + PX / 2
EAST, SOUTH = WEST + 900 * PX, NORTH - 900 * PX  # 900 x 900 px, about 25 km square
OUT = Path(__file__).resolve().parents[2] / "data"

gcloud = "gcloud.cmd" if sys.platform == "win32" else "gcloud"
token = subprocess.run([gcloud, "auth", "print-access-token"], capture_output=True, text=True, check=True).stdout.strip()
ee.Initialize(credentials=Credentials(token), project=PROJECT)

img = ee.Image(ASSET)
print("asset type:", img.getInfo()["type"], "| bands:", img.bandNames().getInfo())
proj = img.select("b1").projection().getInfo()
print("native projection:", proj["crs"], "transform:", proj["transform"])

region = ee.Geometry.Rectangle([WEST, SOUTH, EAST, NORTH], proj="EPSG:4326", geodesic=False)
url = img.select("b1").toFloat().getDownloadURL({
    "name": "deltadtm_kochi", "region": region, "crs": "EPSG:4326",
    "crs_transform": [PX, 0, WEST, 0, -PX, NORTH], "format": "GEO_TIFF",
})
OUT.mkdir(exist_ok=True)
tif = OUT / "deltadtm_kochi.tif"
data = urllib.request.urlopen(url, timeout=120).read()
tif.write_bytes(data)
print(f"wrote {tif} ({len(data) / 1e6:.1f} MB)")

(OUT / "deltadtm_kochi.json").write_text(json.dumps({
    "source": "DeltaDTM v1.1 (Pronk et al. 2024), via Earth Engine community catalog",
    "asset": ASSET, "band": "b1", "licence": "CC BY 4.0",
    "bbox": [WEST, SOUTH, EAST, NORTH], "fetched": datetime.date.today().isoformat(),
    "note": "Raw band, nothing masked. The catalog says the model is clipped at 30 m: value 30 means no data.",
}, indent=2), encoding="utf-8")
