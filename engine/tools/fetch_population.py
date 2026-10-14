"""Stage a region's population grid: where people live, from GHS-POP, on a grid of our own choosing.

Until this, a city's published population was spread over its mapped building footprints by area.
Measured against Singapore's 2020 census by planning area (tools/validate_population_sg.py) that put
about a third of the city's people in the wrong planning area, because footprint area is not people:
factories and warehouses have a lot of roof and nobody home, and a tower has little roof and thousands.
A modelled grid of where people live does that job far better.

Why GHS-POP and not WorldPop (WorldPop/GP/100m/pop): both were measured on the same census test and on
how many of their people sit in a cell with no mapped building, in a one-off comparison on 15 Oct (the
script was not kept; the choice is recorded here and in docs/handoff). GHS-POP fits the census better
(planning-area median error 12% against 18%) and puts fewer people on ground with no building (19%
against 33% in Singapore; 34% against 47% in Kochi), because it shares out the census only over built-up
ground seen from satellite, where WorldPop's unconstrained product spreads some of it over parks and water.

What this stores, and what it is not:
  - JRC's GHS-POP R2023A, 2020 epoch, 100 m population count, resampled onto a 3 arcsecond lat/lon grid.
    The source is in an equal-area projection, so the resampling goes through people per square metre
    (never a count smeared as if it were a density); the staged total is checked against the source's
    own total and the difference is recorded.
  - It says WHERE people are. How MANY is still the published census figure in the manifest: the
    runtime scales the grid so the staged box adds up to that. The raw grid total is recorded beside it
    so the size of that scaling is never hidden.
  - It is a modelled estimate (census disaggregated over built-up ground), at about 100 m. It is never a
    claim about a home or a street.

It also measures one thing the runtime relies on being honest about: how many of the grid's people sit in
a cell with no mapped building. Exposure inside a spot spreads those evenly over their cell instead.

Uses the active gcloud login, as fetch_terrain.py does.

Run:  python engine/tools/fetch_population.py <region>
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
import numpy as np
import pyogrio
import rasterio
import shapely
from google.oauth2.credentials import Credentials

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "engine"))
import regions  # noqa: E402

PROJECT = "before-it-hits"
ASSET = "JRC/GHSL/P2023A/GHS_POP"
PER_DEG = 1200                       # 3 arcseconds, about 93 m: the grid the runtime reads
M_PER_DEG_LAT, M_PER_DEG_LON = 110574.0, 111320.0


def main(name: str) -> None:
    spec = regions.get(name)
    cfg = spec.get("population_grid")
    if not cfg:
        sys.exit(f"{name} has no 'population_grid' entry in engine/regions.py")
    out = ROOT / "data" / "regions" / name
    manifest_path = out / "region.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    published = (manifest.get("population") or {}).get("people")
    if not published:
        sys.exit(f"{name} has no published population in its manifest; run stage_region.py first")
    w, s, e, n = manifest["bbox_wsen"]

    x0, x1 = math.floor(w * PER_DEG), math.ceil(e * PER_DEG)
    y0, y1 = math.floor(s * PER_DEG), math.ceil(n * PER_DEG)
    cols, rows = x1 - x0, y1 - y0
    px = 1 / PER_DEG
    transform = [px, 0, x0 / PER_DEG, 0, -px, y1 / PER_DEG]

    gcloud = "gcloud.cmd" if sys.platform == "win32" else "gcloud"
    token = subprocess.run([gcloud, "auth", "print-access-token"],
                           capture_output=True, text=True, check=True).stdout.strip()
    ee.Initialize(credentials=Credentials(token), project=PROJECT)

    src = ee.Image(f"{ASSET}/{cfg['year']}").select("population_count")
    # The product marks no-data with a large negative number rather than a mask; left in, it drags every
    # sum down, so it is masked here and nothing below can see it.
    src = src.updateMask(src.gte(0))
    region = ee.Geometry.Rectangle([x0 / PER_DEG, y0 / PER_DEG, x1 / PER_DEG, y1 / PER_DEG],
                                   proj="EPSG:4326", geodesic=False)
    # People per square metre in the source's own equal-area pixels, sampled onto our grid, then back to
    # a count with the area of OUR pixel. Empty ground is zero, not missing: no people.
    # The area of a SOURCE pixel has to be pinned to the source projection: reproject() below sets the
    # projection of everything upstream of it, so a bare pixelArea() would be measured on our grid and
    # inflate every count by the ratio of the two pixel areas (it did, by 16% to 18%, before this).
    ours = ee.Projection("EPSG:4326", transform)
    proj = src.projection()
    dens = src.divide(ee.Image.pixelArea().reproject(proj)).unmask(0, False).reproject(ours)
    img = dens.multiply(ee.Image.pixelArea()).toFloat()
    print(f"{name}: asking Earth Engine for GHS-POP {cfg['year']}, {cols} x {rows} pixels ...")
    url = img.getDownloadURL({"name": f"pop_{name}", "region": region, "crs": "EPSG:4326",
                              "crs_transform": transform, "format": "GEO_TIFF"})
    (out / "pop.tif").write_bytes(urllib.request.urlopen(url, timeout=600).read())
    native_sum = src.reduceRegion(ee.Reducer.sum(), region, crs=proj, scale=proj.nominalScale(),
                                  maxPixels=1e10).get("population_count").getInfo()

    with rasterio.open(out / "pop.tif") as ds:
        arr, tf, crs = ds.read(1).astype("float64"), ds.transform, ds.crs
    if arr.shape != (rows, cols):
        # Earth Engine may add an edge row or column (as with the terrain). Anything beyond one is a
        # real mismatch, and is not papered over.
        if arr.shape[0] - rows not in (0, 1) or arr.shape[1] - cols not in (0, 1):
            sys.exit(f"grid mismatch: got {arr.shape}, expected {(rows, cols)}")
        arr = arr[:rows, :cols]
    arr[~np.isfinite(arr)] = 0.0
    arr[arr < 0] = 0.0
    raster_sum = float(arr.sum())
    if raster_sum <= 0:
        sys.exit("the grid is empty: nothing survived for this region")
    with rasterio.open(out / "pop.tif", "w", driver="GTiff", height=rows, width=cols, count=1,
                       dtype="float32", crs=crs, transform=tf, nodata=float("nan"),
                       compress="deflate", predictor=3) as dst:
        dst.write(arr.astype("float32"), 1)

    # How much of the grid's population sits in a pixel with no mapped building.
    _, _, geometry, _ = pyogrio.raw.read(str(out / "buildings.fgb"))
    geoms = np.asarray(shapely.from_wkb(geometry), dtype=object)
    cen = shapely.centroid(geoms)
    cx, cy = shapely.get_x(cen), shapely.get_y(cen)
    col = np.floor((cx - tf.c) / tf.a).astype(int)
    row = np.floor((cy - tf.f) / tf.e).astype(int)
    inside = (col >= 0) & (col < cols) & (row >= 0) & (row < rows)
    mapped = np.zeros((rows, cols), dtype=bool)
    mapped[row[inside], col[inside]] = True
    has_people = arr > 0
    unplaced = float(arr[has_people & ~mapped].sum()) / raster_sum

    block = {
        "file": "pop.tif",
        "source": "GHS-POP R2023A, 2020 epoch, 100 m population count (European Commission JRC), "
                  "via Earth Engine",
        "asset": f"{ASSET}/{cfg['year']}", "band": "population_count", "year": cfg["year"],
        "licence": "CC BY 4.0",
        "native_res_m": 100,
        "resolution": "100 m equal-area source, resampled through people per square metre onto a "
                      "3 arcsecond (about 93 m) lat/lon grid; a modelled estimate, not a head count",
        "unit": "people per pixel (a count, not a density)",
        "raster_sum": round(raster_sum),
        "source_sum": round(native_sum),
        "resampling_difference": round(raster_sum / native_sum - 1, 3),
        "published_total": published,
        "scale_to_published": round(published / raster_sum, 3),
        "pixels_with_people": int(has_people.sum()),
        "people_in_cells_with_no_mapped_building_share": round(unplaced, 3),
        "bbox_wsen": [x0 / PER_DEG, y0 / PER_DEG, x1 / PER_DEG, y1 / PER_DEG], "shape": [rows, cols],
        "fetched": datetime.date.today().isoformat(),
        "caveats": [
            "A modelled estimate of where people live, at about 100 m. It is not a head count.",
            "Scaled so the staged area adds up to the published figure: the grid says where, the "
            "census says how many.",
            "Inside a spot, people are placed on the mapped buildings in their cell, so a spot over "
            "open ground holds nobody. Where a cell has people and no mapped building they are "
            "spread evenly over the cell instead.",
        ],
    }
    # A measurement made once by tools/validate_population_sg.py belongs to the data, so it is kept
    # across a re-stage rather than quietly dropped.
    if (manifest.get("population_grid") or {}).get("validation"):
        block["validation"] = manifest["population_grid"]["validation"]
    manifest["population_grid"] = block
    manifest_path.write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    print(f"{name}: pop.tif {rows}x{cols} | grid total {raster_sum:,.0f} (source says {native_sum:,.0f}) vs "
          f"published {published:,} (scale {block['scale_to_published']}) | "
          f"{block['pixels_with_people']:,} pixels with people | "
          f"{unplaced:.1%} of people are in cells with no mapped building")


if __name__ == "__main__":
    if len(sys.argv) != 2:
        sys.exit(__doc__)
    main(sys.argv[1])
