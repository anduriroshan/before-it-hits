"""Run the 28 May 2024 Kochi replay and write the precomputed result the service serves.

The scoring runs the screening over two city boxes, which is a few seconds and reads the staged
region - a staging-time job, not a request. So it is precomputed once and shipped as JSON, the same
staged-vs-live split the rest of the product uses. The service just reads the result.

Run:  python engine/tools/replay_kochi.py
      python engine/tools/replay_kochi.py replay/kochi_2024_05_28.json
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "engine"))

from bih_engine import Region       # noqa: E402
from bih_engine import replay       # noqa: E402


def main() -> None:
    event_path = Path(sys.argv[1]) if len(sys.argv) > 1 else ROOT / "replay" / "kochi_2024_05_28.json"
    event = json.loads(event_path.read_text(encoding="utf-8"))
    region = Region.load(ROOT / "data" / "regions" / event["region"])

    out = replay.run(region, event)
    out_path = event_path.with_suffix(".result.json")
    out_path.write_text(json.dumps(out, indent=1, ensure_ascii=False), encoding="utf-8")

    h = out["pooled"]
    print(f"wrote {out_path.relative_to(ROOT)}")
    print(f"detected {h['detected']}/{h['positives']} reported localities, "
          f"baseline {h['baseline']:.1%}, enrichment {h['enrichment']}x -> {h['verdict'][:60]}")
    for b in out["boxes"]:
        print(f"\n  box {b['box']}: {b['spots_total']} spots, {b['spots_filling']} filling, "
              f"baseline {b['baseline']:.0%}")
        for loc in b["per_locality"]:
            nf = loc["nearest_filling_spot"]
            d = f"{nf['distance_m']} m (rank {nf['pooling_rank']})" if nf else "no filling spot"
            mark = "x" if loc["detected"] else ("." if loc["flooded"] else "-")
            print(f"    [{mark}] {loc['name']:<28} nearest filling spot {d}")
    if out["localities_not_placed"]:
        print("\n  NOT PLACED in any box:", ", ".join(out["localities_not_placed"]))


if __name__ == "__main__":
    main()
