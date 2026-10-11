"""Latency + sanity benchmark on real Kochi terrain.  Run: python engine/bench/kochi_bench.py"""
import sys
import time
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tests"))

from bih_engine import analyse_area, analyse_grid, read_dem  # noqa: E402
from helpers import make_grid, tilted_plane  # noqa: E402

BOXES = {
    "5 km2 plan reference (Palarivattom, straddles 10N seam)": (76.2968, 9.9967, 76.3168, 10.0167),
    "30 km2 plan AreaSpec box (Edappally)": (76.27, 9.96, 76.32, 10.01),
    "68 km2 stress (Edappally..Vyttila)": (76.27, 9.96, 76.34, 10.04),
}


def fmt(t):
    return "  ".join(f"{k.removesuffix('_s')}={v:5.2f}" for k, v in t.items())


t0 = time.perf_counter()
analyse_grid(make_grid(tilted_plane(40, 40)))
print(f"numba JIT warm-up (one-off per process; cached on disk after first ever run): {time.perf_counter() - t0:.2f}s\n")

for name, bbox in BOXES.items():
    print(f"=== {name}")
    for label in ("cold", "warm"):
        t0 = time.perf_counter()
        res = analyse_area(bbox, rainfall_mm=100)
        total = time.perf_counter() - t0
        g = res["grid"]
        print(f"  {label}: total={total:5.2f}s  grid={g['shape'][0]}x{g['shape'][1]} ({g['area_km2']} km2)")
        print(f"        {fmt(res['timings_s'])}")
    deps = res["debug"]
    print(f"  depressions: {len(deps)}  (low-confidence: {sum(d['low_confidence'] for d in deps)})")
    grid = read_dem(bbox)
    v = grid.dem[~np.isnan(grid.dem)]
    print(f"  GLO-30 elevation m: min={v.min():.1f} p5={np.percentile(v, 5):.1f} median={np.median(v):.1f} "
          f"p95={np.percentile(v, 95):.1f} max={v.max():.1f}  | cells <=0 m: {100 * (v <= 0).mean():.1f}%")
    for d in deps[:5]:
        print(f"    #{d['fill_order']} idx={d['pooling_index']} contrib={d['contrib_m2'] / 1e4:7.1f} ha  "
              f"fills at {d['rain_to_fill_mm']:6.1f} mm  ({d['lat']:.4f}, {d['lon']:.4f})")
    print()
