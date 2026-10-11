"""Engine HTTP service: a drawn box in, a screening result out.

Stateless per request. Every request reads its own window of a staged region, so there is no
per-box preparation and nothing to invalidate. Region manifests are small and are read once;
the terrain and vector reads themselves still happen per request.

Health is at /api/health, not /healthz: Cloud Run's frontend reserves that exact path and never
forwards it to the container.
"""
from __future__ import annotations

import json
import math
import os
import queue
import sys
import threading
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "engine"))

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from bih_agents import coordinator, llm
from bih_engine import Region, analyse_area, forecast
from bih_engine.forecast import ForecastUnavailable
from bih_engine.terrain import TerrainUnavailable

ROOT = Path(__file__).resolve().parents[1]
REGION_ROOT = Path(os.environ.get("BIH_REGION_ROOT", ROOT / "data" / "regions"))
WEB_DIR = ROOT / "web"

# A box this big is a different product: it stops being "what happens on this street" and the
# 10 m grid stops fitting in a request. The UI should stop the user before this.
MAX_AREA_KM2 = 120.0

app = FastAPI(title="Before It Hits - engine", version="0.2")
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])

_regions: dict[str, Region] | None = None


def regions() -> dict[str, Region]:
    """Every staged region, keyed by name. A directory without a manifest is simply not staged."""
    global _regions
    if _regions is None:
        found = {}
        for d in sorted(REGION_ROOT.glob("*")):
            if (d / "region.json").exists():
                try:
                    found[d.name] = Region.load(d)
                except Exception as exc:  # one broken manifest must not take the service down
                    print(f"skipping region {d.name}: {exc}", file=sys.stderr)
        _regions = found
    return _regions


def region_or_404(name: str | None) -> Region:
    rs = regions()
    if not rs:
        raise HTTPException(503, "no staged region is available")
    if name is None:
        return rs[next(iter(rs))]
    if name not in rs:
        raise HTTPException(404, f"unknown region {name!r}; staged: {', '.join(sorted(rs))}")
    return rs[name]


def describe(r: Region) -> dict:
    m = r.manifest
    return {
        "name": r.name, "title": m.get("title"), "country": m.get("country"),
        "bbox_wsen": list(r.bounds), "note": m.get("note"), "presets": r.presets,
        "staged": m.get("staged"), "terrain": m.get("terrain"), "vectors": m.get("vectors"),
        "not_available": m.get("not_available", []),
        "official": m.get("official"), "max_area_km2": MAX_AREA_KM2,
    }


class AnalyseRequest(BaseModel):
    bbox: list[float] = Field(..., min_length=4, max_length=4, description="west, south, east, north")
    rainfall_mm: float = Field(100.0, ge=0, le=1000)
    res_m: float = Field(10.0, ge=5, le=30)
    region: str | None = Field(None, description="staged region name; defaults to the first")
    return_period: int | None = Field(50, description="official layer to show alongside; null for none")


def area_km2(bbox) -> float:
    w, s, e, n = bbox
    return abs(e - w) * abs(n - s) * 111.32**2 * math.cos(math.radians((s + n) / 2))


@app.get("/api/health")
def health() -> dict:
    rs = regions()
    if not rs:
        raise HTTPException(503, "no staged region is available")
    return {"ok": True, "regions": {n: r.manifest.get("staged") for n, r in rs.items()}}


@app.get("/api/regions")
def list_regions() -> dict:
    return {"regions": [describe(r) for r in regions().values()]}


@app.get("/api/region")
def region_info(name: str | None = None) -> dict:
    return describe(region_or_404(name))


class ForecastRequest(BaseModel):
    bbox: list[float] = Field(..., min_length=4, max_length=4)
    hours: int = Field(48, ge=1, le=240)


@app.post("/api/forecast")
def forecast_for_box(req: ForecastRequest) -> dict:
    """Rainfall forecast at the centre of the box, reduced to worst-case windows.

    Deliberately a separate call from /api/analyse: the forecast is live and can fail or be slow,
    and a weather outage must not stop the terrain screening from answering.
    """
    w, s, e, n = req.bbox
    if not (w < e and s < n):
        raise HTTPException(422, "bbox must be west < east and south < north")
    try:
        return forecast.for_bbox((w, s, e, n), hours=req.hours).as_dict()
    except ForecastUnavailable as exc:
        raise HTTPException(503, str(exc)) from exc


@app.get("/api/config")
def config() -> dict:
    """The browser needs the Maps key. It is referrer-restricted and public by design:
    a Maps browser key always ends up in the page source."""
    return {"maps_api_key": os.environ.get("MAPS_API_KEY", ""), "agents": llm.available()}


class AskRequest(BaseModel):
    question: str = Field(..., min_length=1, max_length=500)
    bbox: list[float] = Field(..., min_length=4, max_length=4)
    region: str | None = None
    rainfall_mm: float = Field(100.0, ge=0, le=1000, description="whatever the slider shows")
    return_period: int | None = 50
    res_m: float = Field(10.0, ge=5, le=30)


@app.post("/api/ask")
def ask(req: AskRequest) -> StreamingResponse:
    """Ask about the drawn box in plain language.

    Streams newline-delimited JSON rather than answering once at the end. The run takes about ten
    seconds and most of that is two model calls, so watching the agents hand over - including the
    Verifier sending a draft back - is both the honest view of what is happening and the only way
    the wait is bearable. Newline-delimited JSON rather than server-sent events because the
    browser reads it with a plain fetch; EventSource cannot POST.

    Every failure is in-band: once the first line is sent the status code is already 200, so the
    request is validated up front and anything after that arrives as an event.
    """
    w, s, e, n = req.bbox
    if not (w < e and s < n):
        raise HTTPException(422, "bbox must be west < east and south < north")
    km2 = area_km2(req.bbox)
    if km2 > MAX_AREA_KM2:
        raise HTTPException(422, f"box is {km2:.0f} km2; the limit is {MAX_AREA_KM2:.0f} km2")
    r = region_or_404(req.region)
    if not r.covers((w, s, e, n)):
        raise HTTPException(422, f"that box is outside the staged region ({r.manifest.get('title')})")
    if not llm.available():
        raise HTTPException(503, "the agent layer is not configured on this deployment "
                                 "(no GEMINI_API_KEY); the screening itself still works")

    events: queue.Queue = queue.Queue()
    DONE = object()

    def work():
        t0 = time.perf_counter()
        try:
            out = coordinator.run(question=req.question, bbox=(w, s, e, n), region=r,
                                  slider_mm=req.rainfall_mm, return_period=req.return_period,
                                  res_m=req.res_m,
                                  emit=lambda step: events.put({"event": "step", **step}))
            out["result"]["timings_s"]["total"] = time.perf_counter() - t0
            events.put({"event": "done", "briefing": out["briefing"],
                        "withheld": out["withheld"], "removed": out["removed"],
                        "scenario": out["scenario"], "result": out["result"]})
        except Exception as exc:                 # a thread dying silently would hang the stream
            events.put({"event": "error", "detail": f"{type(exc).__name__}: {exc}"})
        finally:
            events.put(DONE)

    threading.Thread(target=work, daemon=True).start()

    def stream():
        while True:
            item = events.get()
            if item is DONE:
                return
            yield json.dumps(item) + "\n"

    return StreamingResponse(stream(), media_type="application/x-ndjson",
                             headers={"Cache-Control": "no-store", "X-Accel-Buffering": "no"})


@app.post("/api/analyse")
def analyse(req: AnalyseRequest) -> dict:
    w, s, e, n = req.bbox
    if not (w < e and s < n):
        raise HTTPException(422, "bbox must be west < east and south < north")
    km2 = area_km2(req.bbox)
    if km2 > MAX_AREA_KM2:
        raise HTTPException(422, f"box is {km2:.0f} km2; the limit is {MAX_AREA_KM2:.0f} km2")
    r = region_or_404(req.region)
    if not r.covers((w, s, e, n)):
        raise HTTPException(
            422, f"that box is outside the staged region ({r.manifest.get('title')}). "
                 "The screening works anywhere in principle, but only staged regions are served.")
    # Asking for an official layer where none is published is not an error, it is the normal case
    # almost everywhere. Run the screening and say plainly that no validated layer exists.
    rp = req.return_period if req.return_period in r.official_return_periods else None
    t0 = time.perf_counter()
    try:
        res = analyse_area((w, s, e, n), rainfall_mm=req.rainfall_mm, res_m=req.res_m,
                           region=r, return_period=rp)
    except (TerrainUnavailable, ValueError) as exc:
        raise HTTPException(422, str(exc)) from exc
    res["timings_s"]["total"] = time.perf_counter() - t0
    if req.return_period is not None and rp is None:
        res["screening"]["confidence"]["validated_layer"] = (
            f"none published for {r.manifest.get('title')}")
    res.pop("debug", None)          # depths in metres: internal only, never leaves the engine
    res.pop("debug_excluded", None)
    return res


if WEB_DIR.exists():
    app.mount("/", StaticFiles(directory=WEB_DIR, html=True), name="web")

    @app.get("/")
    def index() -> FileResponse:
        return FileResponse(WEB_DIR / "index.html")
