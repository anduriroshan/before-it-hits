"""Check where people are placed against Singapore's census: footprint-only spreading vs the staged grid.

This is the measurement behind replacing "the published total spread over building footprints" with a
population grid. Singapore is the one staged city with an official count by small area, so it is the one
place the question can be answered with a number instead of an argument.

  Truth     SingStat, Census of Population 2020: resident population by planning area (data.gov.sg),
            with the Master Plan 2019 planning-area boundaries.
  Judged    each method's SHARE of the city in each planning area, so only where people are placed is
            tested, not how many. Two figures: the share of the city's people that sit in the wrong
            planning area (half the summed share differences), and the median planning area's error.
  Caveat    the census counts RESIDENTS (about 4.0 M of about 5.9 M people). Dormitory workers are
            missing from the truth and will sit in some areas, so no method can score perfectly.

The result is written into singapore/region.json (population_grid.validation), so the number the product
quotes travels with the data. Kochi has no small-area census staged, and that is said, not implied.

Needs network (data.gov.sg, keyless) and a staged Singapore (buildings.fgb, pop.tif).

Run:  python engine/tools/validate_population_sg.py
"""
from __future__ import annotations

import csv
import datetime
import io
import json
import sys
import urllib.request
from pathlib import Path

import geopandas as gpd
import numpy as np
import rasterio
from rasterio.features import rasterize
from rasterio.transform import Affine
from scipy.stats import spearmanr

ROOT = Path(__file__).resolve().parents[2]
REGION = ROOT / "data" / "regions" / "singapore"
CENSUS = "d_7f243956483d5901f237e6f87b096636"      # resident population by planning area/subzone, 2020
BOUNDARY = "d_4765db0e87b9c86336792efe8a1f7a66"    # Master Plan 2019 planning area boundary (no sea)
API = "https://api-open.data.gov.sg/v1/public/api/datasets/{}/poll-download"
SUB = 4                                            # sub-pixels a side for splitting a pixel between areas


def _get(url: str, timeout: int) -> bytes:
    # data.gov.sg answers 403 to urllib's default User-Agent.
    req = urllib.request.Request(url, headers={"User-Agent": "before-it-hits/0.3 (validation script)"})
    return urllib.request.urlopen(req, timeout=timeout).read()


def fetch(dataset: str) -> bytes:
    return _get(json.loads(_get(API.format(dataset), 60))["data"]["url"], 120)


def scores(estimate: np.ndarray, truth: np.ndarray) -> dict:
    est, tru = estimate / estimate.sum(), truth / truth.sum()
    return {"share_of_people_in_the_wrong_planning_area": round(float(0.5 * np.abs(est - tru).sum()), 3),
            "median_planning_area_error_pct": round(float(np.median(np.abs(est - tru) / tru) * 100)),
            "rank_correlation": round(float(spearmanr(estimate, truth).statistic), 3)}


def main() -> None:
    manifest_path = REGION / "region.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    pop, grid_meta = manifest["population"], manifest.get("population_grid")
    if not grid_meta:
        sys.exit("singapore has no population_grid; run fetch_population.py singapore first")

    bound = gpd.read_file(io.BytesIO(fetch(BOUNDARY))).to_crs(4326)
    bound["name"] = bound["PLN_AREA_N"].str.upper().str.strip()
    truth = {}
    for row in csv.reader(io.StringIO(fetch(CENSUS).decode("utf-8-sig"))):
        if row and row[0].endswith(" - Total") and row[1].replace(",", "").isdigit():
            truth[row[0][:-8].upper().strip()] = int(row[1].replace(",", ""))
    names = sorted(n for n in truth if truth[n] > 0 and n in set(bound.name))
    t = np.array([truth[n] for n in names], float)
    print(f"{len(names)} planning areas with residents; census residents {int(t.sum()):,}")

    # Footprint-only: the published total spread over mapped footprints, as the product used to.
    b = gpd.read_file(REGION / "buildings.fgb").to_crs(32648)
    cent = gpd.GeoDataFrame({"fp": b.geometry.area}, geometry=b.geometry.centroid, crs=32648).to_crs(4326)
    fp = gpd.sjoin(cent, bound[["name", "geometry"]], how="inner", predicate="within").groupby("name")["fp"].sum()
    by_fp = np.array([fp.get(n, 0.0) for n in names])

    # The staged grid, each pixel split between the planning areas that cover it.
    with rasterio.open(REGION / grid_meta["file"]) as ds:
        grid, tf = np.nan_to_num(ds.read(1).astype("float64")), ds.transform
    sub = Affine(tf.a / SUB, 0, tf.c, 0, tf.e / SUB, tf.f)
    ids = rasterize([(g, i + 1) for i, g in enumerate(bound.geometry)],
                    out_shape=(grid.shape[0] * SUB, grid.shape[1] * SUB), transform=sub, fill=0, dtype="int32")
    per_sub = np.repeat(np.repeat(grid, SUB, 0), SUB, 1) / (SUB * SUB)
    zonal = dict(zip(bound.name, np.bincount(ids.ravel(), weights=per_sub.ravel(), minlength=len(bound) + 1)[1:]))
    by_grid = np.array([zonal.get(n, 0.0) for n in names])

    result = {
        "against": "Singapore Census of Population 2020, resident population by planning area "
                   "(SingStat, via data.gov.sg), with Master Plan 2019 planning area boundaries",
        "datasets": [CENSUS, BOUNDARY],
        "planning_areas": len(names),
        "judged_on": "each method's share of the city in each planning area, so where people are placed "
                     "and not how many",
        "caveat": "The census counts residents only (about 4.0 million of about 5.9 million people), so "
                  "dormitory workers are missing from the truth and no method can score perfectly.",
        "footprint_only": scores(by_fp, t),
        "grid": scores(by_grid, t),
        "measured": datetime.date.today().isoformat(),
    }
    manifest["population_grid"]["validation"] = result
    manifest_path.write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    print(json.dumps({k: result[k] for k in ("footprint_only", "grid")}, indent=2))


if __name__ == "__main__":
    main()
