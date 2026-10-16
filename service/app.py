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
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from bih_agents import coordinator, llm
from bih_engine import Region, analyse_area, forecast
from bih_engine.forecast import ForecastUnavailable
from bih_engine.terrain import TerrainUnavailable

ROOT = Path(__file__).resolve().parents[1]
REGION_ROOT = Path(os.environ.get("BIH_REGION_ROOT", ROOT / "data" / "regions"))
REPLAY_ROOT = Path(os.environ.get("BIH_REPLAY_ROOT", ROOT / "replay"))
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


_replays: dict[str, dict] | None = None


def replays() -> dict[str, dict]:
    """Precomputed replay results, keyed by event id. Scored at staging time (two city-sized boxes
    is a few seconds), so the service only reads the result - the staged-vs-live split again."""
    global _replays
    if _replays is None:
        found = {}
        if REPLAY_ROOT.exists():
            import json as _json
            for p in sorted(REPLAY_ROOT.glob("*.result.json")):
                try:
                    found[p.stem.removesuffix(".result")] = _json.loads(p.read_text("utf-8"))
                except Exception as exc:
                    print(f"skipping replay {p.name}: {exc}", file=sys.stderr)
        _replays = found
    return _replays


def replays_for(region_name: str) -> list[str]:
    return [eid for eid, r in replays().items() if r.get("event", {}).get("region") == region_name]


def describe(r: Region) -> dict:
    m = r.manifest
    return {
        "name": r.name, "title": m.get("title"), "country": m.get("country"),
        "bbox_wsen": list(r.bounds), "note": m.get("note"), "presets": r.presets,
        "staged": m.get("staged"), "terrain": m.get("terrain"), "vectors": m.get("vectors"),
        "not_available": m.get("not_available", []),
        "official": m.get("official"), "max_area_km2": MAX_AREA_KM2,
        "replays": replays_for(r.name),
        "preparations": bool(m.get("preparations")),
        "live_rainfall": m.get("live_rainfall"),
        "timezone": m.get("timezone"),
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


_density_cache: dict[tuple, dict] = {}


def parse_bbox(raw: str | None) -> tuple | None:
    """A 'w,s,e,n' query string, or None. Invalid input is None, not an error: these are the
    conditions tabs, and falling back to the whole city beats failing the panel."""
    if not raw:
        return None
    try:
        w, s, e, n = (float(x) for x in raw.split(","))
    except ValueError:
        return None
    return (w, s, e, n) if w < e and s < n else None


def scope_for(r: Region, raw_bbox: str | None) -> tuple[tuple, dict]:
    """Which patch of ground a conditions answer is about, and how to say so.

    The weather and air-quality providers answer at a point. Without a drawn box that point is the
    middle of the whole staged city, which is a materially different claim from the box someone
    just drew - so the answer carries which one it is rather than leaving it to be assumed.
    """
    box = parse_bbox(raw_bbox)
    if box and r.covers(box):
        w, s, e, n = box
        return box, {"of": "box", "label": "the box you drew",
                     "note": "Taken at the centre of the box you drew. The provider answers at a "
                             "point, so this is one reading for the box, not a map of it.",
                     "km2": round(area_km2(box), 1)}
    w, s, e, n = r.bounds
    return r.bounds, {"of": "city", "label": r.manifest.get("title") or r.name,
                      "note": "Taken at the centre of the whole staged city, because no box is "
                              "drawn. Draw a box to ask about one neighbourhood instead.",
                      "km2": round(area_km2(r.bounds), 1)}


@app.on_event("startup")
def warm_the_news() -> None:
    """Fetch each city's news in the background so the first visit to the News tab is instant."""
    if os.environ.get("BIH_NO_NEWS_WARM"):
        return
    from bih_agents import news, news_agent
    flood = [(r.name, r.manifest.get("title") or r.name, r.manifest.get("news_query"))
             for r in regions().values()]
    # Flooding first, because the News tab opens on it; then the other topics the news agent can be
    # asked about, so a question that spans topics does not wait on a cold index.
    other = [(news_agent.cache_key(r.name, t), r.manifest.get("title") or r.name,
              news_agent.topic_query(t, news_agent.place_scope(r.manifest.get("title") or r.name,
                                                               r.manifest.get("news_place"))))
             for t in news_agent.TOPICS if t != "flood" for r in regions().values()]
    news.warm(flood + other, gap_s=news.MIN_GAP_S)


@app.on_event("startup")
def warm_the_density() -> None:
    """Build each city's population raster in the background.

    It is the one layer that has to read every staged building footprint, which is seconds on a
    city, and a layer toggle that takes seconds reads as broken. The footprints never change, so
    building it once at startup costs nothing later."""
    if os.environ.get("BIH_NO_DENSITY_WARM"):
        return

    def build() -> None:
        for name in list(regions()):
            try:
                population_density(name)
            except Exception:                 # noqa: BLE001 - a warm failure is not an outage
                pass

    threading.Thread(target=build, daemon=True).start()


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


@app.get("/api/replay")
def replay_result(id: str) -> dict:
    """A precomputed replay of a real past storm. Read-only: it is scored at staging time and
    shipped, because scoring it is a few city-sized screenings, not a per-request job."""
    rs = replays()
    if id not in rs:
        raise HTTPException(404, f"unknown replay {id!r}; available: {', '.join(sorted(rs))}")
    return rs[id]


class RoadOutlookRequest(BaseModel):
    bbox: list[float] = Field(..., min_length=4, max_length=4)
    region: str | None = None
    rainfall_mm: float = Field(100.0, ge=0, le=1000)
    duration_h: float = Field(6.0, ge=0.5, le=48)


@app.post("/api/road-outlook")
def road_outlook(req: RoadOutlookRequest) -> dict:
    """The agent's qualitative read on the road timeline, grounded in recent local news.

    Separate from the screening and the playback, both of which stand on their own. Returns an
    empty outlook rather than erroring if the agent layer is off or cannot produce a clean one -
    the map and counts are the real product; this only interprets them."""
    from bih_agents import road_outlook as ro
    if not llm.available():
        return {"outlook": None}
    w, s, e, n = req.bbox
    if not (w < e and s < n):
        raise HTTPException(422, "bbox must be west < east and south < north")
    r = region_or_404(req.region)
    if not r.covers((w, s, e, n)):
        raise HTTPException(422, "that box is outside the staged region")
    res = analyse_area((w, s, e, n), rainfall_mm=req.rainfall_mm, res_m=10.0, region=r)
    res.pop("debug", None)
    res.pop("debug_excluded", None)
    from bih_agents import news
    title = r.manifest.get("title") or r.name
    heads = news.peek(r.name, title, query=r.manifest.get("news_query")) or []
    out = ro.for_box(res, region_title=title, rainfall_mm=req.rainfall_mm, duration_h=req.duration_h,
                     news_query=r.manifest.get("news_query"), articles=heads)
    return out or {"outlook": None}


class PreparationsRequest(BaseModel):
    bbox: list[float] = Field(..., min_length=4, max_length=4)
    region: str | None = None
    rainfall_mm: float = Field(100.0, ge=0, le=1000)
    return_period: int | None = 50


@app.post("/api/preparations")
def preparations_for_box(req: PreparationsRequest) -> dict:
    """F9: preparations within the city's documented budget, ranked from the worst spot down.

    Deterministic: the ranked, costed list comes from the engine and the region's own budget lines,
    not from a model. If the agent layer is on, a short planner note on the trade-offs is added on
    top - and even that may use no figure the list does not already contain. Preparation only,
    never dispatch. Returns an honest 'not available' where no budget is documented for the city."""
    from bih_engine import preparations as prep
    from bih_agents import planner
    w, s, e, n = req.bbox
    if not (w < e and s < n):
        raise HTTPException(422, "bbox must be west < east and south < north")
    r = region_or_404(req.region)
    if not r.covers((w, s, e, n)):
        raise HTTPException(422, "that box is outside the staged region")
    rp = req.return_period if req.return_period in r.official_return_periods else None
    res = analyse_area((w, s, e, n), rainfall_mm=req.rainfall_mm, res_m=10.0, region=r,
                       return_period=rp)
    res.pop("debug", None)
    res.pop("debug_excluded", None)
    plan = prep.rank(res, r.preparations, rainfall_mm=req.rainfall_mm)
    if plan.get("available") and plan.get("items"):
        # F10: the same box again, with the spots the plan acts on taken out of the exposed set.
        ids = prep.treated_spot_ids(plan)
        if ids:
            after = analyse_area((w, s, e, n), rainfall_mm=req.rainfall_mm, res_m=10.0, region=r,
                                 treated=set(ids))
            plan["before_after"] = prep.before_after(res, after, plan)
        else:
            plan["before_after"] = prep.before_after(res, res, plan)
    if llm.available() and plan.get("available") and plan.get("items"):
        note = planner.write(r.manifest.get("title") or r.name, plan)
        if note:
            plan["planner_note"] = note["note"]
    return plan


@app.get("/api/news")
def news_for_region(region: str | None = None, topic: str = "flood") -> dict:
    """Live news about the city, for context only. A separate call from everything else: it is the
    grounded, display-only path and must never be able to slow or break the screening.

    `topic` is one of the news agent's topics (flooding by default); the query is ours, never the
    caller's."""
    from bih_agents import news, news_agent
    if topic not in news_agent.TOPICS:
        raise HTTPException(422, f"unknown topic {topic!r}; choose one of: {', '.join(news_agent.TOPICS)}")
    r = region_or_404(region)
    title = r.manifest.get("title") or r.name
    query = news_agent.topic_query(topic, news_agent.place_scope(title, r.manifest.get("news_place")),
                                   r.manifest.get("news_query"))
    got = news.get(news_agent.cache_key(r.name, topic), title, query=query)
    if got["status"] == "ok":
        return {**got["data"], "age_s": got["age_s"], "stale": got["stale"], "topic": topic}
    if got["status"] == "pending":
        # The index is slow; the browser asks again rather than hold a request open for a minute.
        return JSONResponse({"pending": True}, status_code=202)
    raise HTTPException(503, got.get("error") or "the news index is unavailable")


class NewsTurn(BaseModel):
    q: str = Field("", max_length=300)
    a: str = Field("", max_length=400)


class NewsAskRequest(BaseModel):
    question: str = Field(..., min_length=1, max_length=400)
    region: str | None = None
    place: str | None = Field(None, max_length=80)      # the area on the map, when the question is about one
    history: list[NewsTurn] = Field(default_factory=list, max_length=8)


@app.post("/api/news-ask")
def news_ask(req: NewsAskRequest) -> StreamingResponse:
    """Ask a question about what is being reported: what is happening and what happened.

    Streams the same newline-delimited JSON as /api/ask. The answer is built only from headlines
    the news index returned, every claim cites them, and a claim with a figure no headline carries
    is removed. Display-only: it reads the news cache and changes nothing else."""
    from bih_agents import news_agent
    r = region_or_404(req.region)
    if not llm.available():
        raise HTTPException(503, "the agent layer is not configured on this deployment "
                                 "(no GEMINI_API_KEY)")
    events: queue.Queue = queue.Queue()
    DONE = object()

    def work():
        try:
            out = news_agent.run(req.question, region=r.name, title=r.manifest.get("title") or r.name,
                                 place_query=r.manifest.get("news_query"),
                                 news_place=r.manifest.get("news_place"), place=req.place,
                                 history=[t.model_dump() for t in req.history],
                                 emit=lambda step: events.put({"event": "step", **step}))
            events.put({"event": "done", **out})
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


@app.get("/api/rainfall-now")
def rainfall_now(region: str | None = None, bbox: str | None = None) -> dict:
    """Live observed rainfall from the region's gauge network, where one is wired (Singapore/NEA).

    Display and context only - the screening and the agent run off the forecast. Returns an honest
    'not available' for a city with no open gauge feed (every city but Singapore, so far), rather
    than pretending. `bbox` is an optional 'w,s,e,n' to rank gauges by nearness to the drawn box."""
    from bih_engine import rainfall_obs
    r = region_or_404(region)
    if not r.manifest.get("live_rainfall"):
        return {"available": False,
                "reason": "no open rain-gauge network is wired for this city"}
    box = r.bounds
    if bbox:
        try:
            parts = [float(x) for x in bbox.split(",")]
            if len(parts) == 4:
                box = tuple(parts)
        except ValueError:
            raise HTTPException(422, "bbox must be four comma-separated numbers: w,s,e,n")
    try:
        return rainfall_obs.near(box)
    except rainfall_obs.RainfallObsUnavailable as exc:
        raise HTTPException(503, str(exc)) from exc


@app.get("/api/heat")
def heat_forecast(region: str | None = None, bbox: str | None = None) -> dict:
    """Heat conditions for the drawn box, or for the whole city when none is drawn.

    For the Heat tab. Uses the same Weather API as the rainfall forecast but extracts temperature,
    humidity, heat index and wind. Which patch of ground it is about comes back with it."""
    from bih_engine import heat
    r = region_or_404(region)
    box, scope = scope_for(r, bbox)
    try:
        out = heat.for_bbox(box).as_dict()
    except heat.HeatUnavailable as exc:
        raise HTTPException(503, str(exc)) from exc
    out["scope"] = scope
    out["caveats"] = [scope["note"], *out.get("caveats", [])]
    return out


@app.get("/api/weather-grid")
def weather_grid(region: str | None = None, type: str = "heat",
                 bbox: str | None = None, hours: int = 24) -> dict:
    """One weather variable across the region, at every forecast hour: the animated map field.

    Returns the grid points with a value per hour, plus the clock and the colour bounds, so the
    browser can interpolate between points to draw the field and between hours to move it. The
    whole window comes down at once - scrubbing the time slider must not cost a request."""
    from bih_engine import weather_grid as wg
    r = region_or_404(region)
    box = parse_bbox(bbox)
    area = box if (box and r.covers(box)) else r.bounds
    try:
        return wg.field(wg.grid(area, grid_size=4, hours=max(1, min(int(hours), 48))), type)
    except wg.WeatherUnavailable as exc:
        raise HTTPException(503, str(exc)) from exc


@app.get("/api/surface-heat")
def surface_heat_map(region: str | None = None, bbox: str | None = None) -> dict:
    """Where the ground runs hot, from the staged Landsat composite: the one heat layer that can say
    which block is hotter than the next, because the satellite sees patches about 100 m across where
    a forecast model sees kilometres. Over the drawn box when there is one, with its hottest and
    coolest places named by the road beside them; over the whole city otherwise, where there is no
    ranking to give.

    A typical dry-season mid-morning, not a forecast and not today, and the surface rather than the
    air. Both of those travel with the answer."""
    from bih_engine import surface_heat
    r = region_or_404(region)
    box, scope = scope_for(r, bbox)
    scope["note"] = ("The ground inside the box you drew, in patches about 100 m across."
                     if scope["of"] == "box"
                     else "The whole staged city, because no box is drawn. Draw a box to see which "
                          "parts of one neighbourhood run hottest.")
    out = surface_heat.read(r, box, places=scope["of"] == "box")
    out["scope"] = scope
    return out


@app.get("/api/population-density")
def population_density(region: str | None = None, bbox: str | None = None) -> dict:
    """People per km2 as a raster, over the drawn box or the whole city.

    A box gets a finer grid than a city does, because the cells are sized to the area asked about
    rather than fixed: the method is the same, the picture is simply drawn at a useful scale."""
    from bih_engine import popgrid
    r = region_or_404(region)
    box = parse_bbox(bbox)
    area = box if (box and r.covers(box)) else r.bounds
    key = (r.name, tuple(round(v, 5) for v in area))
    hit = _density_cache.get(key)
    if hit is not None:
        return hit
    out = popgrid.density_for(r, area)
    if not out.get("available"):
        return out
    out["scope"] = "box" if area is box else "city"
    # The footprints are staged and never change, so this is worth keeping for the session. A
    # city-wide grid is the slow one and the one most likely to be asked for twice.
    if len(_density_cache) < 64:
        _density_cache[key] = out
    return out


@app.get("/api/air-quality")
def air_quality(region: str | None = None) -> dict:
    """Air quality tile URL for the layer overlay.

    Returns a tile URL template that the browser fetches directly (browser key, referrer-restricted).
    The Air Quality API must be enabled in the GCP project for tiles to load."""
    r = region_or_404(region)
    return {"available": True, "source": "Google Air Quality API",
            "note": "Tiles are fetched by the browser using the Maps API key. "
                    "The Air Quality API must be enabled in the GCP project."}


@app.get("/api/air-quality-current")
def air_quality_current(region: str | None = None, bbox: str | None = None) -> dict:
    """Current AQI, dominant pollutant, forecast and health recommendations.

    At the centre of the drawn box when there is one, otherwise at the centre of the city. Which
    it was comes back with the reading - though at this provider's resolution the two will often
    agree, and the caveat says that rather than implying a box-level measurement."""
    from bih_engine import air_quality as aq
    r = region_or_404(region)
    box, scope = scope_for(r, bbox)
    w, s, e, n = box
    try:
        out = aq.for_region((s + n) / 2, (w + e) / 2)
    except aq.AirQualityUnavailable as exc:
        return {"aqi": None, "category": "Unavailable", "error": str(exc), "scope": scope,
                "source": "Google Air Quality API",
                "caveats": ["Air quality data is not available — the API may not be enabled."]}
    out["scope"] = scope
    out["caveats"] = [
        scope["note"],
        "Air quality is a regional reading. Asking about a box rarely changes it, and it does not "
        "vary at street or building level.",
        *out.get("caveats", []),
    ]
    return out


@app.get("/api/config")
def config() -> dict:
    """The browser needs the Maps key. It is referrer-restricted and public by design:
    a Maps browser key always ends up in the page source."""
    from bih_agents import hazards, news_agent
    return {"maps_api_key": os.environ.get("MAPS_API_KEY", ""), "agents": llm.available(),
            "hazards": hazards.live(), "news_topics": news_agent.topic_list()}


class HistoryTurn(BaseModel):
    """One earlier turn, as the browser remembers it. Only used to work out what a new message
    refers to; it is never treated as evidence (see scenario.clean_history)."""
    q: str = Field("", max_length=300)
    area: str | None = Field(None, max_length=80)
    mode: str | None = Field(None, max_length=12)
    rainfall_mm: float | None = Field(None, ge=0, le=1000)
    duration_h: float | None = Field(None, ge=0, le=240)
    headline: str | None = Field(None, max_length=300)


class AskRequest(BaseModel):
    question: str = Field(..., min_length=1, max_length=500)
    # Optional: a message can be about the product, or name a place for the browser to find first.
    bbox: list[float] | None = Field(None, min_length=4, max_length=4)
    region: str | None = None
    rainfall_mm: float = Field(100.0, ge=0, le=1000, description="whatever the slider shows")
    return_period: int | None = 50
    res_m: float = Field(10.0, ge=5, le=30)
    history: list[HistoryTurn] = Field(default_factory=list, max_length=12)
    area_label: str | None = Field(None, max_length=80)
    place_resolved: str | None = Field(None, max_length=80)


@app.post("/api/ask")
def ask(req: AskRequest) -> StreamingResponse:
    """Send one message in the conversation: about the drawn box, a named place, or the product.

    Streams newline-delimited JSON rather than answering once at the end. The run takes about ten
    seconds and most of that is two model calls, so watching the agents hand over - including the
    Verifier sending a draft back - is both the honest view of what is happening and the only way
    the wait is bearable. Newline-delimited JSON rather than server-sent events because the
    browser reads it with a plain fetch; EventSource cannot POST.

    Every failure is in-band: once the first line is sent the status code is already 200, so the
    request is validated up front and anything after that arrives as an event.
    """
    r = region_or_404(req.region)
    box = None
    if req.bbox is not None:
        w, s, e, n = req.bbox
        if not (w < e and s < n):
            raise HTTPException(422, "bbox must be west < east and south < north")
        km2 = area_km2(req.bbox)
        if km2 > MAX_AREA_KM2:
            raise HTTPException(422, f"box is {km2:.0f} km2; the limit is {MAX_AREA_KM2:.0f} km2")
        if not r.covers((w, s, e, n)):
            raise HTTPException(422, f"that box is outside the staged region ({r.manifest.get('title')})")
        box = (w, s, e, n)
    if not llm.available():
        raise HTTPException(503, "the agent layer is not configured on this deployment "
                                 "(no GEMINI_API_KEY); the screening itself still works")

    events: queue.Queue = queue.Queue()
    DONE = object()

    def work():
        t0 = time.perf_counter()
        try:
            out = coordinator.run(question=req.question, bbox=box, region=r,
                                  slider_mm=req.rainfall_mm, return_period=req.return_period,
                                  res_m=req.res_m, history=[t.model_dump() for t in req.history],
                                  area_label=req.area_label, place_resolved=req.place_resolved,
                                  all_regions=list(regions().values()),
                                  emit=lambda step: events.put({"event": "step", **step}))
            if out["kind"] == "place_needed":
                # The browser looks the place up on the map and asks again with the box.
                events.put({"event": "place_needed", "place": out["place"],
                            "scenario": out["scenario"]})
                return
            if out.get("result"):
                out["result"]["timings_s"]["total"] = time.perf_counter() - t0
            ev = {"event": "done", "kind": out["kind"], "answer": out.get("answer"),
                  "answer_checked": out.get("answer_checked"), "briefing": out["briefing"],
                  "withheld": out["withheld"], "removed": out["removed"],
                  "scenario": out["scenario"], "result": out["result"],
                  "rain_profile": out.get("rain_profile"),
                  "rain_profile_start": out.get("rain_profile_start"),
                  "forecast_meta": out.get("forecast_meta"),
                  "handoff": out.get("handoff"), "headline": out.get("headline")}
            if out.get("presets"):
                ev["presets"] = out["presets"]
            events.put(ev)
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


# The engine as MCP tools and the agent as an A2A skill, for other agents to call. Registered before the
# static mount below so /mcp and /a2a win over the catch-all file server.
import agents_api  # noqa: E402

agents_api.mount(app, regions)

if WEB_DIR.exists():
    app.mount("/", StaticFiles(directory=WEB_DIR, html=True), name="web")

    @app.get("/")
    def index() -> FileResponse:
        return FileResponse(WEB_DIR / "index.html")
