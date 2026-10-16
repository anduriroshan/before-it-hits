"""MCP and A2A endpoints: the engine and the agent, exposed so other agents can call them.

Hand-rolled over the protocols' shared JSON-RPC 2.0 core rather than pulling an SDK, so the service stays a
single lean FastAPI app on Cloud Run and does not depend on a Pre-GA agent SDK. Two surfaces:

  - MCP (/mcp): the deterministic engine as tools. Each returns the engine's OWN figures as structured JSON,
    never a model's - the same guarantee the rest of the product rests on. A stock MCP client can list and
    call these.
  - A2A (/.well-known/agent-card.json + /a2a): the whole agent. One skill, "screen a place", runs the full
    verified pipeline (coordinator.run: read the question, run the engine, write, and check) and returns the
    checked briefing. Refusals, units, time windows and the no-model-ever-produces-a-figure rule all hold,
    because it is the same code path the web app uses.

Both are a public surface, so every call is validated here before it reaches the engine.
"""
from __future__ import annotations

import math
import re
import uuid

from fastapi import Request
from fastapi.responses import JSONResponse

from bih_agents import coordinator, llm

MCP_PROTOCOL = "2025-06-18"
A2A_PROTOCOL = "0.3.0"
VERSION = "0.2"
MAX_AREA_KM2 = 120.0
MAX_RAINFALL_MM = 1000.0


class ToolError(Exception):
    """A bad request to a tool: reported to the caller, never a 500."""


def _norm(s: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", str(s).lower()).strip()


def _area_km2(bbox) -> float:
    w, s, e, n = bbox
    return abs(e - w) * abs(n - s) * 111.32**2 * math.cos(math.radians((s + n) / 2))


# ---- shared: turn a region + (bbox or preset name) into a validated box ------------------------------------

def _resolve(get_regions, region_name, bbox, area):
    rs = get_regions()
    if not rs:
        raise ToolError("no staged region is available")
    reg = None
    if region_name:
        if region_name not in rs:
            raise ToolError(f"unknown region {region_name!r}; staged: {', '.join(sorted(rs))}")
        reg = rs[region_name]
    if area:                                   # a preset label like "Geylang / Kallang" or "Edappally"
        want = _norm(area)
        for r in ([reg] if reg else list(rs.values())):
            for p in r.presets:
                have = _norm(p["label"])
                if want and have and (want == have or want in have or have in want):
                    return r, tuple(p["bbox"])
        raise ToolError(f"no area named {area!r}; try one of: "
                        + ", ".join(p["label"] for r in rs.values() for p in r.presets))
    if bbox is not None:
        if not (isinstance(bbox, (list, tuple)) and len(bbox) == 4 and all(isinstance(v, (int, float)) for v in bbox)):
            raise ToolError("bbox must be [west, south, east, north] in degrees")
        w, s, e, n = (float(v) for v in bbox)
        if not (w < e and s < n):
            raise ToolError("bbox must have west < east and south < north")
        box = (w, s, e, n)
        if _area_km2(box) > MAX_AREA_KM2:
            raise ToolError(f"box is {_area_km2(box):.0f} km2; the limit is {MAX_AREA_KM2:.0f} km2")
        if reg is None:
            reg = next((r for r in rs.values() if r.covers(box)), None)
            if reg is None:
                raise ToolError("that box is not inside any staged region")
        elif not reg.covers(box):
            raise ToolError(f"that box is outside {reg.manifest.get('title') or reg.name}")
        return reg, box
    if reg is None:
        raise ToolError("give a region plus a bbox or an area (preset name)")
    return reg, reg.bounds                     # the whole city, for the point-forecast tools


# ---- the MCP tools: the engine's figures, never a model's --------------------------------------------------

def _t_list_regions(get_regions, _args):
    out = []
    for r in get_regions().values():
        out.append({"region": r.name, "title": r.manifest.get("title"),
                    "bbox_wsen": list(r.bounds),
                    "areas": [p["label"] for p in r.presets],
                    "has_official_flood_map": bool(r.manifest.get("official")),
                    "has_live_rain_gauges": bool(r.manifest.get("live_rainfall"))})
    return {"regions": out,
            "note": "Use a region name plus an area (preset) or a bbox [w,s,e,n] in the other tools."}


def _t_rain_forecast(get_regions, args):
    from bih_engine import forecast
    r, box = _resolve(get_regions, args.get("region"), args.get("bbox"), args.get("area"))
    try:
        f = forecast.for_bbox(box, hours=48).as_dict()
    except forecast.ForecastUnavailable as exc:
        raise ToolError(str(exc)) from exc
    hours = f.get("hours", [])
    return {"region": r.name, "source": f.get("source"),
            "total_mm_next_48h": round(sum(h.get("mm", 0) for h in hours), 1),
            "hours_with_measurable_rain": sum(1 for h in hours if h.get("mm", 0) >= 0.1),
            "peak_windows": f.get("peaks"),
            "note": "Live forecast at the centre of the area. A tool, not a model: every figure is the "
                    "provider's or the engine's."}


def _t_flood_screening(get_regions, args):
    from bih_engine import analyse_area
    r, box = _resolve(get_regions, args.get("region"), args.get("bbox"), args.get("area"))
    mm = float(args.get("rainfall_mm", 50.0))
    if not (0 <= mm <= MAX_RAINFALL_MM):
        raise ToolError(f"rainfall_mm must be between 0 and {MAX_RAINFALL_MM:.0f}")
    res = analyse_area(box, rainfall_mm=mm, res_m=10.0, region=r)
    res.pop("debug", None)
    res.pop("debug_excluded", None)
    sc = res["screening"]
    deps = sc["depressions"]
    by_id = {f["properties"]["id"]: f["properties"] for f in res.get("geojson", {}).get("features", [])}
    fills = sum(1 for p in by_id.values() if p.get("fills_at_rainfall"))
    roads = [{"road": x["road"], "kind": x["highway"], "length_m": x["length_m"]}
             for x in sc.get("routes_affected", [])[:5]]
    out = {"region": r.name, "area_km2": res["grid"]["area_km2"], "rainfall_mm": mm,
           "low_spots_found": len(deps),
           "low_spots_ranked_4_or_5": sum(1 for d in deps if d["pooling_index"] >= 4),
           "low_spots_that_fill_at_this_rainfall": fills,
           "roads_through_the_worst_spots": roads,
           "has_official_depth_map": bool(res.get("official")),
           "limits": ["A terrain screening on a 10 m grid: it ranks low spots 1 to 5, it never states a "
                      "water depth. Stormwater drains are not in any public data, so pooling is overstated "
                      "where drains work.",
                      "Not dispatch or evacuation advice. Preparation only."]}
    ex = res.get("exposure")
    if ex and ex.get("available"):
        out["roughly_people_in_worst_spots_that_fill"] = ex.get("people_in_worst_spots")
        out["roughly_people_in_the_whole_box"] = ex.get("people_in_box")
    return out


def _t_heat_forecast(get_regions, args):
    from bih_engine import heat
    r, box = _resolve(get_regions, args.get("region"), args.get("bbox"), args.get("area"))
    try:
        out = heat.for_bbox(box).as_dict()
    except heat.HeatUnavailable as exc:
        raise ToolError(str(exc)) from exc
    out["region"] = r.name
    out["note"] = ("A point forecast at the centre of the area. The heat band belongs to the authority named "
                   "with it, never ours; this holds no health data and counts no one as harmed.")
    return out


def _t_air_quality(get_regions, args):
    from bih_engine import air_quality as aq
    r, box = _resolve(get_regions, args.get("region"), args.get("bbox"), args.get("area"))
    w, s, e, n = box
    try:
        out = aq.for_region((s + n) / 2, (w + e) / 2)
    except aq.AirQualityUnavailable as exc:
        raise ToolError(str(exc)) from exc
    out["region"] = r.name
    out["note"] = "The provider's regional reading, attributed and unchanged. It does not vary at street level."
    return out


# name -> (description, input schema, handler). The schema is what an MCP client reads to call the tool.
_AREA_PROPS = {
    "region": {"type": "string", "description": "staged region name, e.g. 'kochi' or 'singapore'"},
    "area": {"type": "string", "description": "a preset area name, e.g. 'Edappally' or 'Geylang / Kallang'"},
    "bbox": {"type": "array", "items": {"type": "number"}, "minItems": 4, "maxItems": 4,
             "description": "[west, south, east, north] in degrees; an alternative to 'area'"},
}
TOOLS = {
    "list_regions": ("List the staged cities, their areas, and what data each has.",
                     {"type": "object", "properties": {}}, _t_list_regions),
    "rain_forecast": ("Live rainfall forecast for an area over the next 48 hours.",
                      {"type": "object", "properties": dict(_AREA_PROPS), "required": ["region"]}, _t_rain_forecast),
    "flood_screening": ("Where rain water collects in an area: low spots ranked 1 to 5, the roads through the "
                        "worst of them, and roughly how many people live there. Never a water depth.",
                        {"type": "object", "properties": {**_AREA_PROPS,
                         "rainfall_mm": {"type": "number", "description": "rainfall to screen at, 0 to 1000 mm (default 50)"}},
                         "required": ["region"]}, _t_flood_screening),
    "heat_forecast": ("Forecast heat index, temperature and humidity for an area, against a published threshold.",
                      {"type": "object", "properties": dict(_AREA_PROPS), "required": ["region"]}, _t_heat_forecast),
    "air_quality": ("Current air quality (AQI, dominant pollutant) for an area, from the provider.",
                    {"type": "object", "properties": dict(_AREA_PROPS), "required": ["region"]}, _t_air_quality),
}


def _tools_list():
    return [{"name": n, "description": d, "inputSchema": schema} for n, (d, schema, _) in TOOLS.items()]


def _call_tool(get_regions, name, args):
    if name not in TOOLS:
        raise ToolError(f"unknown tool {name!r}")
    return TOOLS[name][2](get_regions, args or {})


# ---- MCP JSON-RPC (streamable HTTP transport, single-response mode) ----------------------------------------

def _rpc_result(id_, result):
    return {"jsonrpc": "2.0", "id": id_, "result": result}


def _rpc_error(id_, code, message):
    return {"jsonrpc": "2.0", "id": id_, "error": {"code": code, "message": message}}


def _handle_mcp(get_regions, msg):
    method, id_ = msg.get("method"), msg.get("id")
    if method == "initialize":
        return _rpc_result(id_, {"protocolVersion": MCP_PROTOCOL,
                                 "capabilities": {"tools": {"listChanged": False}},
                                 "serverInfo": {"name": "before-it-hits", "version": VERSION}})
    if method in ("notifications/initialized", "notifications/cancelled"):
        return None                                         # a notification takes no response
    if method == "ping":
        return _rpc_result(id_, {})
    if method == "tools/list":
        return _rpc_result(id_, {"tools": _tools_list()})
    if method == "tools/call":
        params = msg.get("params") or {}
        try:
            data = _call_tool(get_regions, params.get("name"), params.get("arguments"))
            import json as _json
            return _rpc_result(id_, {"content": [{"type": "text", "text": _json.dumps(data, ensure_ascii=False)}],
                                     "isError": False})
        except ToolError as exc:                            # a bad request is reported as a tool error, not a crash
            return _rpc_result(id_, {"content": [{"type": "text", "text": str(exc)}], "isError": True})
    return _rpc_error(id_, -32601, f"method not found: {method}")


# ---- A2A: the agent card and the one skill -----------------------------------------------------------------

def _agent_card(base_url):
    return {
        "protocolVersion": A2A_PROTOCOL,
        "name": "Before It Hits",
        "description": "Screens a neighbourhood before a hazard hits: where rain water collects and which roads "
                       "it reaches, plus heat, air quality, wind and earthquake shaking. Every figure is computed "
                       "by an engine and checked; the model never produces a number, and it refuses what it "
                       "cannot support (no depths, no evacuation advice, no traffic prediction, no casualties).",
        "url": f"{base_url}/a2a",
        "preferredTransport": "JSONRPC",
        "version": VERSION,
        "capabilities": {"streaming": False, "pushNotifications": False},
        "defaultInputModes": ["text/plain"],
        "defaultOutputModes": ["text/plain"],
        "skills": [{
            "id": "screen_place",
            "name": "Screen a place before it hits",
            "description": "Ask a plain-language question about a staged area (Kochi or Singapore) and get a "
                           "checked answer. Name an area in the message, e.g. 'Will Edappally flood tonight?' "
                           "or 'How would rain affect traffic in Geylang / Kallang?'.",
            "tags": ["flood", "heat", "air-quality", "wind", "earthquake", "preparedness"],
            "examples": ["Will Edappally flood tonight?",
                         "How hot will it get in Geylang / Kallang tomorrow?",
                         "How would this affect traffic in Vyttila?"],
        }],
    }


def _message_text(message):
    parts = (message or {}).get("parts") or []
    return " ".join(p.get("text", "") for p in parts if isinstance(p, dict) and p.get("kind", "text") == "text").strip()


def _resolve_place_from_text(get_regions, text, meta):
    """An A2A message names its own place: a region and/or a preset in the text or the metadata. Returns
    (region, bbox, label) or raises ToolError listing the areas, because the server cannot geocode."""
    meta = meta or {}
    if meta.get("region") or meta.get("area") or meta.get("bbox"):
        r, box = _resolve(get_regions, meta.get("region"), meta.get("bbox"), meta.get("area"))
        return r, box, (meta.get("area") or r.manifest.get("title") or r.name)
    low = _norm(text)
    for r in get_regions().values():
        for p in r.presets:
            if _norm(p["label"]) in low:
                return r, tuple(p["bbox"]), p["label"]
        if _norm(r.manifest.get("title") or r.name) in low or _norm(r.name) in low:
            return r, r.bounds, (r.manifest.get("title") or r.name)
    raise ToolError("Name an area in your message. I can screen: "
                    + "; ".join(f"{r.manifest.get('title') or r.name}: "
                                + ", ".join(p["label"] for p in r.presets) for r in get_regions().values()))


def _briefing_text(out):
    b = out.get("briefing")
    if b:
        lines = [b.get("headline", "")] + list(b.get("bullets") or [])
        if b.get("caveat"):
            lines.append(b["caveat"])
        return "\n\n".join(x for x in lines if x)
    return out.get("answer") or out.get("withheld") or "No answer was produced."


def _agent_message(text):
    return {"kind": "message", "role": "agent", "messageId": uuid.uuid4().hex,
            "parts": [{"kind": "text", "text": text}]}


def _handle_a2a(get_regions, msg):
    method, id_ = msg.get("method"), msg.get("id")
    if method not in ("message/send", "message/stream"):
        return _rpc_error(id_, -32601, f"method not found: {method}")
    if not llm.available():
        return _rpc_result(id_, _agent_message("The agent layer is not configured on this deployment."))
    params = msg.get("params") or {}
    message = params.get("message") or {}
    text = _message_text(message)
    if not text:
        return _rpc_error(id_, -32602, "message has no text part")
    try:
        r, box, label = _resolve_place_from_text(get_regions, text, message.get("metadata"))
    except ToolError as exc:
        return _rpc_result(id_, _agent_message(str(exc)))
    try:
        out = coordinator.run(question=text, bbox=tuple(box), region=r, slider_mm=100.0,
                              return_period=None, history=[], area_label=label, place_resolved=label,
                              all_regions=list(get_regions().values()), emit=lambda step: None)
        return _rpc_result(id_, _agent_message(_briefing_text(out)))
    except Exception as exc:                                # never leak a stack trace to a peer agent
        return _rpc_error(id_, -32603, f"internal error: {type(exc).__name__}")


# ---- wiring: add the routes to the FastAPI app, before the static mount ------------------------------------

def _base_url(request):
    """The externally visible origin. Cloud Run terminates TLS and forwards plain HTTP, so request.base_url
    says http://; the card's url must be the https:// a client can actually reach, from the forwarded headers."""
    proto = request.headers.get("x-forwarded-proto") or request.url.scheme
    host = request.headers.get("x-forwarded-host") or request.headers.get("host") or request.url.netloc
    return f"{proto}://{host}"


def mount(app, get_regions):
    """Register /mcp, /a2a and the agent card on `app`. `get_regions` returns the staged regions dict."""

    @app.post("/mcp")
    async def mcp_endpoint(request: Request):          # noqa: ANN202
        try:
            msg = await request.json()
        except Exception:
            return JSONResponse(_rpc_error(None, -32700, "parse error"), status_code=400)
        if isinstance(msg, list):                      # a JSON-RPC batch
            out = [r for r in (_handle_mcp(get_regions, m) for m in msg) if r is not None]
            return JSONResponse(out) if out else JSONResponse(None, status_code=202)
        resp = _handle_mcp(get_regions, msg)
        return JSONResponse(resp) if resp is not None else JSONResponse(None, status_code=202)

    @app.get("/mcp")
    async def mcp_get():                                # noqa: ANN202
        # This server answers each request in one JSON response; it opens no server-initiated SSE stream.
        return JSONResponse(_rpc_error(None, -32000, "this MCP server is request/response only; POST JSON-RPC to /mcp"),
                            status_code=405)

    @app.get("/.well-known/agent-card.json")
    async def agent_card(request: Request):             # noqa: ANN202
        return JSONResponse(_agent_card(_base_url(request)))

    @app.get("/.well-known/agent.json")                 # the older well-known name, same card
    async def agent_card_legacy(request: Request):      # noqa: ANN202
        return JSONResponse(_agent_card(_base_url(request)))

    @app.post("/a2a")
    async def a2a_endpoint(request: Request):           # noqa: ANN202
        try:
            msg = await request.json()
        except Exception:
            return JSONResponse(_rpc_error(None, -32700, "parse error"), status_code=400)
        return JSONResponse(_handle_a2a(get_regions, msg))
