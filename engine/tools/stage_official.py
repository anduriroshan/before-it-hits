"""Stage the official KSDMA flood hazard rasters for a region.

Source: Kerala State Disaster Management Authority, "Flood Hazard Probability of Kerala based on
Historic Data (Raster File Zip)", produced with UNEP. Kerala-wide GeoTIFFs at 3 arcsec (~92 m),
one per return period. We clip each to the region and keep the published values untouched.

What the data is, checked rather than assumed (see the notes written into the manifest):
  - Values rise monotonically with return period at every shared wet cell (100.0% over Kochi) and
    the wet area grows 11.4% -> 21.4% from 10-yr to 500-yr. That is depth-and-extent behaviour;
    a probability surface would not do this.
  - Magnitude (median ~1.4-2.2, max ~10-18 over Kochi) reads as centimetres, which matches the
    description this project was planned against. Not confirmed at source, so it is marked
    inferred and the UI must say so.

Run:  python engine/tools/stage_official.py <dir-with-extracted-tifs>
"""
from __future__ import annotations

import datetime
import json
import re
import sys
from pathlib import Path

import numpy as np
import rasterio
from rasterio.windows import from_bounds

ROOT = Path(__file__).resolve().parents[2]
REGION_DIR = ROOT / "data" / "regions" / "kochi"
RETURN_PERIODS = [10, 25, 50, 100, 200, 500]
PAGE = "https://sdma.kerala.gov.in/hazard-maps/"


def clip_one(src_path: Path, bbox, out_path: Path) -> dict:
    """Window-read the region out of the Kerala raster and write it unchanged, as a COG."""
    with rasterio.open(src_path) as ds:
        win = from_bounds(*bbox, ds.transform).round_offsets().round_lengths()
        arr = ds.read(1, window=win)
        profile = ds.profile | {
            "height": arr.shape[0], "width": arr.shape[1],
            "transform": ds.window_transform(win),
            "driver": "GTiff", "tiled": True, "blockxsize": 256, "blockysize": 256,
            "compress": "deflate", "predictor": 2,
        }
        nodata = ds.nodata
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with rasterio.open(out_path, "w", **profile) as dst:
        dst.write(arr, 1)

    v = arr.astype("float64")
    v[(v == nodata) | (v < -1e30)] = np.nan
    wet = np.isfinite(v)
    return {
        "file": out_path.name,
        "shape": list(arr.shape),
        "wet_share": round(float(wet.mean()), 4),
        "median": None if not wet.any() else round(float(np.nanmedian(v)), 1),
        "p95": None if not wet.any() else round(float(np.nanpercentile(v, 95)), 1),
        "max": None if not wet.any() else round(float(np.nanmax(v)), 1),
    }


def main(src_dir: Path) -> None:
    region = json.loads((REGION_DIR / "region.json").read_text(encoding="utf-8"))
    bbox = region["bbox_wsen"]
    tifs = {}
    for p in src_dir.rglob("*.tif"):
        m = re.search(r"_(\d+)_yr_Historical\.tif$", p.name)
        if m:
            tifs[int(m.group(1))] = p
    missing = [rp for rp in RETURN_PERIODS if rp not in tifs]
    if missing:
        sys.exit(f"missing return periods {missing} under {src_dir}")

    layers = {}
    for rp in RETURN_PERIODS:
        info = clip_one(tifs[rp], bbox, REGION_DIR / "official" / f"flood_{rp}yr_historical.tif")
        layers[str(rp)] = info
        print(f"  {rp:>3}-yr: {info['shape'][0]}x{info['shape'][1]}  wet {100*info['wet_share']:4.1f}%  "
              f"median {info['median']}  max {info['max']}")

    # Monotonicity is the evidence that these are depths; re-check it on what we actually staged.
    stacks = []
    for rp in RETURN_PERIODS:
        with rasterio.open(REGION_DIR / "official" / f"flood_{rp}yr_historical.tif") as ds:
            a = ds.read(1).astype("float64")
            a[(a == ds.nodata) | (a < -1e30)] = np.nan
            stacks.append(a)
    checks = []
    for i in range(1, len(stacks)):
        both = np.isfinite(stacks[i]) & np.isfinite(stacks[i - 1])
        checks.append(float((stacks[i][both] >= stacks[i - 1][both] - 1e-6).mean()))
    monotone = round(min(checks), 4) if checks else None
    print(f"  monotonic across return periods at {100*monotone:.1f}% of shared wet cells")

    region["official"] = {
        "hazard": "flood",
        "source": "Kerala State Disaster Management Authority (KSDMA), produced with UNEP",
        "title": "Flood Hazard Probability of Kerala based on Historic Data",
        "page": PAGE,
        "scenario": "historical",
        "return_periods": RETURN_PERIODS,
        "units": "cm (inferred from magnitude; not stated at source)",
        "native_res_deg": 0.000833,
        "native_res_m_approx": 92,
        "crs": "EPSG:4326",
        "staged": datetime.date.today().isoformat(),
        "licence": "Published by KSDMA for public use; terms not stated on the download page",
        "layers": layers,
        "evidence_it_is_depth": {
            "min_monotone_share_across_return_periods": monotone,
            "wet_share_by_return_period": {str(rp): layers[str(rp)]["wet_share"] for rp in RETURN_PERIODS},
        },
        "caveats": [
            "Displayed as published: clipped only, never resampled or reinterpreted.",
            "Coarser than our screening grid (~92 m against 10 m), so it shows the floodplain, "
            "not which street corner fills.",
            "Units are inferred from magnitude, not stated at source.",
            "RCP8.5 climate-scenario rasters also exist and are not staged.",
        ],
    }
    (REGION_DIR / "region.json").write_text(json.dumps(region, indent=2), encoding="utf-8")
    print(f"\nstaged {len(layers)} official layers -> {REGION_DIR / 'official'}")


if __name__ == "__main__":
    if len(sys.argv) != 2:
        sys.exit(__doc__)
    main(Path(sys.argv[1]))
