"""Stage a region's seismic inputs: the published design-level shaking, and the ground it shakes.

Two global products, each clipped to the region with a margin and otherwise left exactly as published
(no resampling, so no value in the staged file is one nobody published):

  hazard  GEM Global Seismic Hazard Map v2023.1 - peak ground acceleration with a 10% chance of being
          exceeded in 50 years (the "475-year" level), on reference rock (Vs30 760-800 m/s). Computed at
          points about 6 km apart and interpolated onto a 3 arcminute grid, so across a drawn box it is
          nearly flat: the variation inside a box comes from the ground, not from this.
          https://doi.org/10.5281/zenodo.8409647   CC BY-NC-SA 4.0 (non-commercial: fine for this
          competition, and must be re-checked before any commercial use).

  ground  USGS global hybrid Vs30 mosaic (Heath et al. 2020): the average shear-wave speed of the top
          30 m, the standard proxy for how much a site amplifies shaking. Default is Allen & Wald's
          topographic-slope estimate, at 30 arcseconds (about 900 m); regional maps are inserted where
          they exist. https://doi.org/10.5066/P96HFVXM   US public domain.
          Water is stored as exactly 600 m/s, a placeholder and not a measurement; the runtime treats it
          as "no ground estimate" rather than as firm ground.

Neither source has a cloud-hosted window to read, so this takes local files. The hazard map is a 35 MB
zip on Zenodo (md5 aa62c4c9d940637afac8cfe934b64742); the Vs30 grid is a 610 MB GMT/netCDF-4 file at
https://apps.usgs.gov/shakemap_geodata/vs30/global_vs30.grd. Download each into its own empty directory.

Run:  python engine/tools/fetch_seismic.py <region> --hazard <v2023_1_pga_475_rock_3min.tif> --ground <global_vs30.grd>
"""
from __future__ import annotations

import argparse
import datetime
import hashlib
import json
import sys
from pathlib import Path

import numpy as np
import rasterio
from rasterio.windows import from_bounds

ROOT = Path(__file__).resolve().parents[2]


def md5(path: Path) -> str:
    h = hashlib.md5()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def clip(src_path: Path, dst_path: Path, bbox, margin_deg: float) -> dict:
    """Cut a window with a margin and write it untouched, at the source's own grid and values."""
    w, s, e, n = bbox
    with rasterio.open(src_path) as ds:
        win = from_bounds(w - margin_deg, s - margin_deg, e + margin_deg, n + margin_deg,
                          transform=ds.transform).round_offsets().round_lengths()
        win = win.intersection(rasterio.windows.Window(0, 0, ds.width, ds.height))
        arr = ds.read(1, window=win).astype("float64")   # GEM's no-data is the float64 maximum
        tf = ds.window_transform(win)
        nodata = ds.nodata
    if nodata is not None and np.isfinite(nodata):
        arr[arr == nodata] = np.nan
    arr = arr.astype("float32")
    if not np.isfinite(arr).any():
        sys.exit(f"{src_path.name} holds no values inside this region")
    with rasterio.open(dst_path, "w", driver="GTiff", height=arr.shape[0], width=arr.shape[1], count=1,
                       dtype="float32", crs="EPSG:4326", transform=tf, nodata=float("nan"),
                       compress="deflate", predictor=3) as dst:
        dst.write(arr, 1)
    return {"shape": list(arr.shape), "cell_deg": [abs(tf.a), abs(tf.e)],
            "min": round(float(np.nanmin(arr)), 4), "max": round(float(np.nanmax(arr)), 4),
            "cells_without_a_value": int((~np.isfinite(arr)).sum())}


def main(name: str, hazard: Path, ground: Path) -> None:
    out = ROOT / "data" / "regions" / name
    manifest_path = out / "region.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    bbox = manifest["bbox_wsen"]

    print(f"{name}: clipping the hazard map and the Vs30 grid ...")
    h = clip(hazard, out / "pga.tif", bbox, margin_deg=0.1)
    g = clip(ground, out / "vs30.tif", bbox, margin_deg=0.05)

    manifest["seismic"] = {
        "hazard": {
            "file": "pga.tif",
            "source": "GEM Global Seismic Hazard Map v2023.1 (Johnson et al. 2023), GEM Foundation",
            "doi": "10.5281/zenodo.8409647",
            "licence": "CC BY-NC-SA 4.0 (non-commercial)",
            "quantity": "peak ground acceleration, as a fraction of g",
            "exceedance": "10% chance of being exceeded in 50 years (the 475-year level)",
            "site_condition": "reference rock, Vs30 760-800 m/s",
            "native_res": "3 arcminute grid (about 5.5 km), interpolated from points about 6 km apart",
            "source_md5": md5(hazard),
            "in_region": h,
        },
        "ground": {
            "file": "vs30.tif",
            "source": "USGS global hybrid Vs30 mosaic (Heath, Wald, Worden, Thompson and Smoczyk 2020)",
            "doi": "10.5066/P96HFVXM",
            "licence": "US government public domain",
            "quantity": "Vs30, the average shear-wave speed of the top 30 m, in m/s",
            "method": "topographic slope (Allen and Wald 2007) as the default, regional maps inserted "
                      "where they exist",
            "native_res": "30 arcseconds (about 900 m)",
            "water_placeholder_m_s": 600.0,
            "source_md5": md5(ground),
            "in_region": g,
        },
        "amplification": "Borcherdt (1994) short-period factors, in the form and values USGS tabulate in "
                         "Open-File Report 2007-1357, Table 1",
        "intensity": "Worden et al. (2012) peak-ground-acceleration relation, as USGS ShakeMap implements it",
        "staged": datetime.date.today().isoformat(),
        "caveats": [
            "The published design-level shaking: the level with a one in ten chance of being exceeded in "
            "fifty years. It is a hazard level, not a forecast and not an earthquake.",
            "The ground is estimated from the shape of the land at about 900 m. In a flat city almost "
            "everything looks alike to that method, and it cannot see a pocket of soft clay or made ground.",
            "No data on what buildings stand here or how they are built is held, so nothing here says "
            "anything about damage, injury or death.",
        ],
    }
    manifest_path.write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    print(f"{name}: pga.tif {h['shape']} ({h['min']} to {h['max']} g) | vs30.tif {g['shape']} "
          f"({g['min']} to {g['max']} m/s, {g['cells_without_a_value']} cells without a value)")


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("region")
    ap.add_argument("--hazard", required=True, type=Path)
    ap.add_argument("--ground", required=True, type=Path)
    a = ap.parse_args()
    main(a.region, a.hazard, a.ground)
