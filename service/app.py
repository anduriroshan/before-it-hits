"""Engine HTTP service: a drawn box in, a screening result out.

Stateless. Every request reads its own window of the staged region, so there is no per-box
preparation and nothing to invalidate. The staged region is opened once at startup because the
manifest is small; the terrain and vector reads themselves still happen per request.
"""
from __future__ import annotations

import os
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "engine"))

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from bih_engine import Region, analyse_area
from bih_engine.terrain import TerrainUnavailable

ROOT = Path(__file__).resolve().parents[1]
REGION_DIR = Path(os.environ.get("BIH_REGION", ROOT / "data" / "regions" / "kochi"))
WEB_DIR = ROOT / "web"

# A box this big is a different product: it stops being "what happens on this street" and the
# 10 m grid stops fitting in a request. The UI should stop the user before this.
MAX_AREA_KM2 = 120.0

app = FastAPI(title="Before It Hits - engine", version="0.1")
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])

_region: Region | None = None


def region() -> Region:
    global _region
    if _region is None:
        _region = Region.load(REGION_DIR)
    return _region


class AnalyseRequest(BaseModel):
    bbox: list[float] = Field(..., min_length=4, max_length=4, description="west, south, east, north")
    rainfall_mm: float = Field(100.0, ge=0, le=1000)
    res_m: float = Field(10.0, ge=5, le=30)
    return_period: int | None = Field(50, description="official layer to show alongside; null for none")


def area_km2(bbox) -> float:
    import math
    w, s, e, n = bbox
    return abs(e - w) * abs(n - s) * 111.32**2 * math.cos(math.radians((s + n) / 2))


@app.get("/api/health")
def health() -> dict:
    try:
        r = region()
        return {"ok": True, "region": r.name, "staged": r.manifest.get("staged")}
    except Exception as exc:  # the region is the one thing the service cannot work without
        raise HTTPException(503, f"staged region unavailable: {exc}") from exc


@app.get("/api/region")
def region_info() -> dict:
    r = region()
    return {"name": r.name, "title": r.manifest.get("title"), "bbox_wsen": list(r.bounds),
            "staged": r.manifest.get("staged"), "terrain": r.manifest.get("terrain"),
            "vectors": r.manifest.get("vectors"), "not_available": r.manifest.get("not_available", []),
            "official": r.manifest.get("official"), "max_area_km2": MAX_AREA_KM2}


@app.get("/api/config")
def config() -> dict:
    """The browser needs the Maps key. It is referrer-restricted and public by design:
    a Maps browser key always ends up in the page source."""
    return {"maps_api_key": os.environ.get("MAPS_API_KEY", "")}


@app.post("/api/analyse")
def analyse(req: AnalyseRequest) -> dict:
    w, s, e, n = req.bbox
    if not (w < e and s < n):
        raise HTTPException(422, "bbox must be west < east and south < north")
    km2 = area_km2(req.bbox)
    if km2 > MAX_AREA_KM2:
        raise HTTPException(422, f"box is {km2:.0f} km2; the limit is {MAX_AREA_KM2:.0f} km2")
    r = region()
    if not r.covers((w, s, e, n)):
        raise HTTPException(
            422, f"that box is outside the staged region ({r.manifest.get('title')}). "
                 "The screening works anywhere in principle, but only staged regions are served.")
    t0 = time.perf_counter()
    try:
        res = analyse_area((w, s, e, n), rainfall_mm=req.rainfall_mm, res_m=req.res_m,
                           region=r, return_period=req.return_period)
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc
    except TerrainUnavailable as exc:
        raise HTTPException(422, str(exc)) from exc
    res["timings_s"]["total"] = time.perf_counter() - t0
    res.pop("debug", None)          # depths in metres: internal only, never leaves the engine
    res.pop("debug_excluded", None)
    return res


if WEB_DIR.exists():
    app.mount("/", StaticFiles(directory=WEB_DIR, html=True), name="web")

    @app.get("/")
    def index() -> FileResponse:
        return FileResponse(WEB_DIR / "index.html")
