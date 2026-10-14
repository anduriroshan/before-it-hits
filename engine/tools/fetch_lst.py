"""Stage a region's land-surface temperature: where the ground runs hotter, from Landsat.

A weather forecast cannot say which street is hotter than the next, because it has no streets: it is
a model of the air on a grid of kilometres. What does see the ground is a thermal satellite. Landsat 8
and 9 measure the radiance the surface itself gives off, and USGS publishes it already converted to
surface temperature (Collection 2 Level 2, band ST_B10). This takes those scenes and reduces them to
one picture of where, in a typical season, the surface runs hot and cool.

On resolution, because it is easy to overstate: the thermal sensor sees patches about 100 m across.
USGS resamples that onto a 30 m grid so it lines up with the optical bands, and the grid below is that
grid, but detail finer than about 100 m is smoothing and not measurement. The picture is a few blocks
wide at best, never a single building.

What the result is, and is not - these go into the manifest and onto the screen:
  - A MEDIAN of every clear view in a season, over several years. It is where surfaces typically run
    hotter or cooler than their neighbours. It is not a forecast and it is not today.
  - SURFACE temperature, the skin of the roof, road or leaf, which in the sun is far hotter than the
    air a thermometer reads. It must never be set beside an air-temperature figure as if comparable.
  - Landsat passes at mid-morning. The afternoon, when the heat peaks, is hotter and more spread out.
  - Water is masked out: this is a map of land.

The grid is the staged terrain's own (1 arcsecond, about 30 m), so one grid carries everything. Earth
Engine reprojects the composite onto it by nearest neighbour; the values are our composite, not a
published product, so there is no published number being resampled. The grid is finer than the sensor.

Uses the active gcloud login, as fetch_terrain.py does.

Run:  python engine/tools/fetch_lst.py <region>
"""
from __future__ import annotations

import datetime
import json
import subprocess
import sys
import urllib.request
from pathlib import Path

import ee
import numpy as np
import rasterio
from google.oauth2.credentials import Credentials

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "engine"))
import regions  # noqa: E402

PROJECT = "before-it-hits"
COLLECTIONS = ("LANDSAT/LC08/C02/T1_L2", "LANDSAT/LC09/C02/T1_L2")
PX = 1 / 3600
# A view whose scene is more than this cloudy is left out whole: the pixel mask catches the cloud
# itself, but haze beside it is not caught, and a mostly clouded scene is mostly haze beside cloud.
MAX_SCENE_CLOUD = 40
# A pixel seen clear fewer times than this has no median worth drawing. A quality floor on the
# picture, not a finding about any place.
MIN_VIEWS = 5

# QA_PIXEL bits (Landsat Collection 2): 0 fill, 1 dilated cloud, 2 cirrus, 3 cloud, 4 shadow, 7 water.
BAD_BITS = (0, 1, 2, 3, 4)
WATER_BIT = 7
# USGS's documented scale and offset for ST_B10, in kelvin.
ST_SCALE, ST_OFFSET_K = 0.00341802, 149.0
# What the thermal sensor actually resolves, as opposed to the 30 m grid it is delivered on.
SENSOR_RES_M = 100


def _clear(img):
    qa = img.select("QA_PIXEL")
    ok = ee.Image(1)
    for b in BAD_BITS:
        ok = ok.And(qa.bitwiseAnd(1 << b).eq(0))
    ok = ok.And(qa.bitwiseAnd(1 << WATER_BIT).eq(0))
    lst = img.select("ST_B10").multiply(ST_SCALE).add(ST_OFFSET_K).subtract(273.15).rename("lst_c")
    return lst.updateMask(ok)


def main(name: str) -> None:
    spec = regions.get(name)
    cfg = spec.get("surface_heat")
    if not cfg:
        sys.exit(f"{name} has no 'surface_heat' entry in engine/regions.py")
    out = ROOT / "data" / "regions" / name
    dtm = json.loads((out / "dtm.json").read_text(encoding="utf-8"))
    w, s, e, n = dtm["bbox_wsen"]
    rows, cols = dtm["shape"]

    gcloud = "gcloud.cmd" if sys.platform == "win32" else "gcloud"
    token = subprocess.run([gcloud, "auth", "print-access-token"],
                           capture_output=True, text=True, check=True).stdout.strip()
    ee.Initialize(credentials=Credentials(token), project=PROJECT)

    area = ee.Geometry.Rectangle([w, s, e, n], proj="EPSG:4326", geodesic=False)
    lo, hi = cfg["months"]
    scenes = ee.ImageCollection(COLLECTIONS[0]).merge(ee.ImageCollection(COLLECTIONS[1])) \
        .filterBounds(area).filterDate(cfg["from"], cfg["to"]) \
        .filter(ee.Filter.calendarRange(lo, hi, "month")) \
        .filter(ee.Filter.lt("CLOUD_COVER", MAX_SCENE_CLOUD))
    n_scenes = scenes.size().getInfo()
    if not n_scenes:
        sys.exit("no scenes matched; widen the months or the dates")
    stamps = scenes.aggregate_array("system:time_start").getInfo()
    # The hour on the wall clock of the city, so "mid-morning" is a measured claim, not a recalled one.
    ist = datetime.timezone(datetime.timedelta(hours=5, minutes=30)) if spec["timezone"] == "Asia/Kolkata" \
        else datetime.timezone(datetime.timedelta(hours=8))
    hours = [datetime.datetime.fromtimestamp(t / 1000, ist) for t in stamps]
    mean_hour = float(np.mean([d.hour + d.minute / 60 for d in hours]))
    print(f"{name}: {n_scenes} scenes {min(hours):%Y-%m-%d} to {max(hours):%Y-%m-%d}, "
          f"passing about {int(mean_hour)}:{int((mean_hour % 1) * 60):02d} local time")

    clear = scenes.map(_clear)
    img = clear.median().rename("lst_c").addBands(clear.count().rename("views")).toFloat()
    img = img.updateMask(img.select("views").gte(MIN_VIEWS))

    region = ee.Geometry.Rectangle([w, s, e, n], proj="EPSG:4326", geodesic=False)
    print("asking Earth Engine for the composite (this is the slow step) ...")
    url = img.getDownloadURL({
        "name": f"lst_{name}", "region": region, "crs": "EPSG:4326",
        "crs_transform": [PX, 0, w, 0, -PX, n], "format": "GEO_TIFF",
    })
    data = urllib.request.urlopen(url, timeout=900).read()
    (out / "lst.tif").write_bytes(data)

    with rasterio.open(out / "lst.tif") as ds:
        both, transform, crs = ds.read(), ds.transform, ds.crs
    extra = (both.shape[1] - rows, both.shape[2] - cols)
    if extra != (0, 0):
        # Earth Engine counts a pixel that merely touches the edge of the region, so a grid can come
        # back one row or column long. The origin is pinned to the terrain's corner, so anything extra
        # is on the south or east edge and cropping it restores the terrain's grid exactly. More than
        # one pixel out means something else is wrong, and that is not papered over.
        if extra[0] not in (0, 1) or extra[1] not in (0, 1):
            sys.exit(f"grid mismatch: got {both.shape[1:]}, terrain is {(rows, cols)}")
        both = both[:, :rows, :cols]
        with rasterio.open(out / "lst.tif", "w", driver="GTiff", height=rows, width=cols, count=2,
                           dtype="float32", crs=crs, transform=transform, nodata=float("nan"),
                           compress="deflate", predictor=3) as dst:
            dst.write(both.astype("float32"))
        print(f"  cropped {extra[0]} row and {extra[1]} column Earth Engine added at the edge")
    lst, views = both[0].astype("float64"), both[1].astype("float64")
    ok = np.isfinite(lst)
    if not ok.any():
        sys.exit("nothing survived the masks")
    p2, p50, p98 = (round(float(v), 1) for v in np.percentile(lst[ok], [2, 50, 98]))
    seen = views[ok]
    block = {
        "file": "lst.tif",
        "source": "Landsat 8 and 9 Collection 2 Level 2 surface temperature (USGS), via Earth Engine",
        "collections": list(COLLECTIONS), "band": "ST_B10",
        "licence": "USGS Landsat data are public domain",
        "season": cfg["season"], "months": cfg["months"],
        "dates": [cfg["from"], cfg["to"]],
        "scenes": n_scenes, "max_scene_cloud_pct": MAX_SCENE_CLOUD, "min_views_per_pixel": MIN_VIEWS,
        "views_per_pixel": {"median": int(np.median(seen)), "max": int(seen.max())},
        "passes_about_local_hour": round(mean_hour, 1),
        "composite": "median of every clear view; cloud, cirrus, shadow and water masked",
        "native_res_m": SENSOR_RES_M,
        "resolution": "the thermal sensor resolves patches about 100 m across; USGS delivers it "
                      "resampled to a 30 m grid, and this is on the terrain's 1 arcsec grid",
        "grid": "the staged terrain's own 1 arcsec grid, nearest neighbour from Landsat's 30 m",
        "unit": "degrees C of the SURFACE, not the air",
        "land_share": round(float(ok.mean()), 3),
        "p2_c": p2, "median_c": p50, "p98_c": p98,
        "bbox_wsen": dtm["bbox_wsen"], "shape": [rows, cols],
        "fetched": datetime.date.today().isoformat(),
        "caveats": [
            "A typical-season picture, not a forecast and not today.",
            "Surface temperature, the skin of roofs, roads and leaves. In sun it runs well above "
            "air temperature and is not comparable with a forecast figure.",
            "Landsat passes at mid-morning. Afternoon surfaces run hotter and spread further apart.",
            "The thermal sensor sees patches about 100 m across, so this shows a few blocks at best "
            "and never a single building or roof.",
            "Water is masked: this is a picture of land.",
        ],
    }
    manifest_path = out / "region.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["surface_heat"] = block
    manifest_path.write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    print(f"{name}: lst.tif {len(data) / 1e6:.1f} MB | land {block['land_share']:.0%} | "
          f"surface p2 {p2}  median {p50}  p98 {p98} C | views/pixel median {block['views_per_pixel']['median']}")


if __name__ == "__main__":
    if len(sys.argv) != 2:
        sys.exit(__doc__)
    main(sys.argv[1])
