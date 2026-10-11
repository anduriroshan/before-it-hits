"""GLO-30 (surface model) vs DeltaDTM (bare earth) on identical Kochi grids.
Run: python engine/bench/compare_sources.py"""
import sys
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "engine"))
from bih_engine import analyse_grid, load_overpass, rasterize_layers, read_dem  # noqa: E402

DTM = ROOT / "data" / "deltadtm_kochi.tif"
OSM = load_overpass(ROOT / "data" / "osm" / "water.json")
BOXES = {
    "7 km2  Palarivattom": (76.2968, 9.9967, 76.3168, 10.0167),
    "35 km2 Edappally": (76.27, 9.96, 76.32, 10.01),
    "75 km2 Edappally-Vyttila": (76.27, 9.96, 76.34, 10.04),
}


def summarise(grid, layers=None):
    r = analyse_grid(grid, 100, layers=layers)
    d = r["debug"]
    total_ha = grid.dem.size * grid.res_m**2 / 1e4
    depth = np.array([x["max_depth_m"] for x in d]) if d else np.zeros(1)
    return {
        "pits": len(d),
        "pits/km2": len(d) / (total_ha / 100),
        "depth med/p95 m": f"{np.median(depth):.2f}/{np.percentile(depth, 95):.2f}",
        "<0.45m %": 100 * np.mean([x["low_confidence"] for x in d]) if d else 0,
        "catch>25% box": sum(x["contrib_m2"] / 1e4 > 0.25 * total_ha for x in d),
        "top5 catchment ha": [round(x["contrib_m2"] / 1e4) for x in d[:5]],
        "top5 depth m": [round(x["max_depth_m"], 1) for x in d[:5]],
        "idx>=4 low-conf %": 100 * np.mean([x["low_confidence"] for x in d if x["pooling_index"] >= 4] or [0]),
        "full at 100mm %": 100 * np.mean([x["fills_at_rainfall"] for x in d] or [0]),
        "compute s": round(sum(r["timings_s"].values()), 2),
    }


for name, bbox in BOXES.items():
    glo = read_dem(bbox)
    dtm = read_dem(bbox, source=DTM)
    both = np.isfinite(glo.dem) & np.isfinite(dtm.dem)
    diff = (glo.dem - dtm.dem)[both]
    print(f"=== {name}  grid {glo.dem.shape}   nodata: GLO-30 {100 * np.isnan(glo.dem).mean():.1f}%  "
          f"DeltaDTM {100 * np.isnan(dtm.dem).mean():.1f}%")
    print(f"    GLO-30 minus DeltaDTM where both exist: mean {diff.mean():+.2f} m, median {np.median(diff):+.2f} m, "
          f"p5/p95 {np.percentile(diff, 5):+.1f}/{np.percentile(diff, 95):+.1f} m")
    lay = rasterize_layers(OSM, dtm)
    print(f"    OSM on this grid: water {100 * lay.water.mean():.1f}% of cells, waterway {100 * lay.waterway.mean():.1f}%")
    for label, g, ly in (("GLO-30      ", glo, None), ("DeltaDTM     ", dtm, None), ("DeltaDTM+OSM ", dtm, lay)):
        s = summarise(g, ly)
        print(f"    {label}: " + "  ".join(f"{k}={v:.1f}" if isinstance(v, float) else f"{k}={v}" for k, v in s.items()))
    print()
