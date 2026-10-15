"""A1 Coordinator: holds the message and the conversation, runs the specialists, keeps the trace.

The division of labour is the point of the whole layer, so it is worth stating plainly:

    the language models decide what to ask; the engine decides what is true.

A2 reads the question into parameters. The engine answers it. A6 writes the answer up. A7 checks
every word of that against the engine's own output and removes what does not hold. No model is
ever asked for a finding, which is why no model can invent one.

Every step is emitted as it happens, including the ones that fail. A trace that only shows the
happy path is decoration; this one shows a forecast outage, a model refusing to be reached, and
each claim the Verifier removed, because those are the moments the design is actually about.
"""
from __future__ import annotations

import math
import time
from datetime import datetime, timezone

from bih_engine import analyse_area, forecast
from bih_engine.forecast import ForecastUnavailable

from . import declines, facts, guide, llm, narrator, scenario, verifier

# A briefing nobody checked is worse than no briefing, so these are the only two outcomes.
WITHHELD_UNCHECKED = ("The briefing was withheld: the checking agent could not be reached, and "
                      "this product does not publish a briefing that nothing has checked.")
WITHHELD_FAILED = ("The briefing was withheld: its main claim was not supported by the results.")
NEEDS_AREA = ("I answer about a specific place, so I need one first. Draw a box on the map, pick "
              "an area below, or name a place and I will find it.")
# Topics answered from their own data, without the terrain engine or the rain forecast.
TOPIC_ONLY = ("population", "heat", "air_quality", "wind", "earthquake")

# What a message with no analysis to show carries, so every kind of reply has the same shape.
def _reply(question, spec, trace, **kw) -> dict:
    base = {"kind": "analysis", "question": question, "scenario": spec, "facts": None, "result": None,
            "rain_profile": None, "rain_profile_start": None, "forecast_meta": None,
            "briefing": None, "withheld": None, "removed": [], "answer": None,
            "answer_checked": False, "place": None, "handoff": None, "headline": None,
            "trace": trace.steps}
    base.update(kw)
    return base


# How long a window is when the asker named a time but not a length, and how each reads in the findings.
DEFAULT_WINDOW_H = {"now": 24, "tonight": 12, "tomorrow": 24, "wettest": 24, "later": 1}
WINDOW_SOURCE = {
    "now": "the next {n} h from now",
    "tonight": "tonight, {n} h from the evening",
    "tomorrow": "tomorrow, {n} h from midnight",
    "wettest": "the wettest {n} h stretch in the next {total} h",
    "later": "{n} h starting at the time the question named",
}
# Digit-free, so the words cannot smuggle a figure into the findings.
WINDOW_WORDS = {
    "now": "the first hours of the forecast, starting now",
    "tonight": "tonight, from the evening",
    "tomorrow": "tomorrow, from midnight",
    "wettest": "the wettest stretch of that length anywhere in the forecast, which may start hours from now",
    "later": "the hours starting at the time the question named",
}


def _apply_window(spec: dict, f: dict, region) -> dict | None:
    """Settle which hours of the forecast the question is about, and say so on the spec.

    The forecast reduced to its worst stretch answers "how bad could it get", and that was served for
    every question: "the next 3 hours" got the wettest 3 hours anywhere in two days. The asker's own time
    ("now", "tonight", "tomorrow") now picks the hours, and only a question with no time, or one that asks
    for the worst, gets the worst. A time beyond the forecast falls back to the worst stretch and says so
    rather than being answered as if it were covered."""
    anchor = spec.get("window_from") or "wettest"
    tz = region.manifest.get("timezone") if region is not None else None
    asked = spec.get("duration_h")
    length = asked or DEFAULT_WINDOW_H[anchor]
    if anchor == "wettest":
        length = min(length, 24)       # the worst STRETCH: a "wettest 48 hours" is the whole forecast, and
                                       # a total over two days hides the burst that floods a street
    start, has_passed = None, False
    if anchor == "later":
        at = spec["starts_at"]
        start = forecast.hours_until(f, tz, hour=at.get("hour"), day=at.get("day", "none"),
                                     in_hours=at.get("in_hours"))
        if start is None:                       # "at 9am" asked at noon: that time has gone
            has_passed, anchor, start = True, "now", None
    w = forecast.pick_window(f, length, anchor, tz, start=start)
    fell_back = False
    if w is None and anchor != "wettest":
        w, fell_back = forecast.pick_window(f, length, "wettest", tz), True
    if w is None:
        return None
    spec.update(window_time_has_passed=has_passed, window_rounded_up=bool(asked and asked < 1),
                rainfall_mm=w["mm"], duration_h=float(w["hours"]), mode="forecast",
                window_anchor=w["anchor"], window_starts_in_hours=w["starts_in_hours"],
                window_cut_short=w["truncated"], window_fell_back=fell_back,
                rainfall_source=WINDOW_SOURCE[w["anchor"]].format(n=w["hours"], total=f["hours_ahead"]))
    return w


def _rain_outlook(f: dict) -> dict:
    """Whether it rains at all in the forecast and when it first does, so "when will the rain start" and
    "is it going to rain" are answered from the hours and not from whichever window was picked ("starts
    now" is true of every window picked to start now, wet or dry). A tenth of a millimetre in the hour is
    this product's own line for "some rain"; it is named as such and claims no standard."""
    hs = f.get("hours") or []
    wet = [i for i, h in enumerate(hs) if h["mm"] >= 0.1]
    return {
        "forecast_hours_looked_at": len(hs),
        "hours_with_at_least_a_tenth_of_a_millimetre": len(wet),
        "first_such_hour_is_in_hours": wet[0] if wet else None,
        "last_such_hour_is_in_hours": wet[-1] if wet else None,
        "whole_forecast_total_mm": f.get("total_mm"),
    }


def window_phrase(spec: dict) -> str:
    """The window as a sentence can state it truthfully. 'The next 6 hours' is only said of hours that
    start now."""
    n = int(round(spec.get("duration_h") or 24))
    a, k = spec.get("window_anchor"), spec.get("window_starts_in_hours")
    if a == "wettest":
        return (f"in the wettest {n}-hour stretch of the next two days, starting in about {k} hours" if k
                else f"in the wettest {n}-hour stretch of the next two days, which starts now")
    if a == "later":
        return (f"in the hour starting in about {k} hours" if n == 1
                else f"over {n} hours starting in about {k} hours")
    if a == "tonight":
        return f"tonight, over {n} hours"
    if a == "tomorrow":
        return f"tomorrow, over {n} hours"
    return f"over the next {n} hours"


def _note_limit(bundle: dict, spec: dict) -> None:
    """A limit that is part of the question travels into the findings, so the Narrator says it first."""
    if spec.get("limit_note"):
        bundle["asked_for_but_not_given"] = spec["limit_note"]


def _choose_window(requested, peaks: dict) -> str | None:
    """Which of the forecast's window lengths to run on. The one the question asked about if there
    is one, else the coming day: the default question is about the coming day, and a window that
    is long enough to show rain starting and stopping is what makes the playback readable."""
    available = sorted(int(k) for k in peaks)
    if not available:
        return None
    if requested:
        return str(min(available, key=lambda w: abs(w - float(requested))))
    return "24" if 24 in available else str(available[-1])


def _profile_for(hours: list, start_iso: str, length: int) -> list[float] | None:
    """The hourly amounts of exactly the window the engine ran on, so the playback ends on the same
    total the engine used rather than on a different slice of the forecast."""
    idx = next((i for i, h in enumerate(hours) if h.get("t") == start_iso), 0)
    seg = [h["mm"] for h in hours[idx: idx + length]]
    return seg or None


class Trace:
    """Collects steps and, if someone is listening, hands each one over as it finishes."""

    def __init__(self, emit=None):
        self.steps: list[dict] = []
        self._emit = emit

    def add(self, agent: str, status: str, ms: int, note: str = "", **detail) -> dict:
        step = {"agent": agent, "status": status, "ms": ms, "note": note, **detail}
        self.steps.append(step)
        if self._emit:
            self._emit(step)
        return step


def _observed_now(region, bbox, trace) -> dict | None:
    """Live gauge rainfall for the box, where the region has an open feed (Singapore / NEA). This is
    the 'what is falling now' the agent gets beside the forecast's 'what is coming'. Never blocks the
    run. Only mm values reach the facts - no timestamp, so no date smuggles integers into the
    allowed set the Verifier checks against."""
    if region is None or not region.manifest.get("live_rainfall"):
        return None
    try:
        from bih_engine import rainfall_obs
        o = rainfall_obs.near(tuple(bbox))
    except Exception:
        return None
    w = o.get("wettest_nearby") or {}
    trace.add("A3 Forecast", "ok", 0,
              f"Live gauges now: wettest nearby {w.get('mm_5min')} mm per 5 min.")
    return {"nearest_gauges_mm_per_5min": [g["mm_5min"] for g in o.get("nearest", [])[:3]],
            "wettest_nearby_mm_per_5min": w.get("mm_5min")}


def _default_spec(slider_mm: float, return_period: int | None, question: str) -> dict:
    """What we run when the Scenario agent is unreachable: exactly what is on screen."""
    return {
        "reading": "Read from the controls on screen, not from the question: "
                   "the scenario agent could not be reached.",
        "heat_c": None, "heat_asked_as": "", "magnitude": None, "earlier_area": "",
        "window_from": "wettest", "decline": "none", "declined_detail": "", "out_of_range": None,
        "rainfall_asked_as": "", "starts_at": None,
        "focus": question, "audience": "officer", "mode": "whatif",
        "rainfall_mm": float(slider_mm), "rainfall_source": "the rainfall already set on screen",
        "duration_h": None, "return_period_years": return_period, "ignored_figure": False,
        "needs_clarification": False, "question_back": "",
        "scope": "area", "intent": "analyse", "topic": "overview",
        "place": "", "reused_previous_rainfall": False,
    }


def _run_city_overview(question, spec, region, trace, return_period) -> dict:
    """Answer a city-wide question by fetching the forecast and analysing each preset area."""
    region_title = region.manifest.get("title") if region is not None else None
    presets = region.presets if region is not None else []
    periods = region.official_return_periods if region is not None else []

    if not presets:
        return _reply(question, spec, trace, kind="needs_area",
                      answer="I don't have preset areas for this city yet. "
                             "Draw a box on the map to analyse a specific area.")

    rain_profile, profile_start, forecast_meta = None, None, None

    # Forecast for the city centre
    if spec["mode"] in ("forecast", "whatif"):
        t0 = time.perf_counter()
        try:
            lat = (region.bounds[1] + region.bounds[3]) / 2
            lng = (region.bounds[0] + region.bounds[2]) / 2
            d = 0.01
            f = forecast.for_bbox((lng - d, lat - d, lng + d, lat + d), hours=48).as_dict()
            w = _apply_window(spec, f, region)
            if w:
                rain_profile = _profile_for(f.get("hours", []), w["starts"], int(w["hours"]))
                profile_start = w["starts"]
                forecast_meta = {"source": f["source"], "window_hours": int(w["hours"]),
                                 "fetched_at": datetime.now(timezone.utc).isoformat(
                                     timespec="seconds")}
            trace.add("A3 Forecast", "ok", int((time.perf_counter() - t0) * 1000),
                      f"City-wide: {spec['rainfall_mm']} mm {window_phrase(spec)}.",
                      detail=f["peaks"])
        except ForecastUnavailable as exc:
            trace.add("A3 Forecast", "failed", int((time.perf_counter() - t0) * 1000),
                      f"{exc}. Using the rainfall already set.")
            spec["mode"] = "whatif"

    # Run engine on each preset
    rp = return_period if return_period in periods else None
    preset_results = []
    for p in presets:
        t0 = time.perf_counter()
        box = tuple(p["bbox"])
        try:
            result = analyse_area(box, rainfall_mm=spec["rainfall_mm"], res_m=10.0,
                                  region=region, return_period=rp)
            result.pop("debug", None)
            result.pop("debug_excluded", None)
            screening = result.get("screening", {})
            deps = screening.get("depressions", [])
            top = [d for d in deps if d["pooling_index"] >= 4]
            feats = result.get("geojson", {}).get("features", [])
            fills = sum(1 for feat in feats if feat["properties"].get("fills_at_rainfall"))
            routes = screening.get("routes_affected", [])
            total_road_m = sum(r.get("length_m", 0) for r in routes)
            preset_results.append({
                "label": p["label"], "bbox": p["bbox"],
                "spots": len(deps), "top_ranked": len(top), "fills": fills,
                "road_km": round(total_road_m / 1000, 1),
                "top_roads": [{"road": r["road"], "kind": r["highway"],
                               "length_m": r["length_m"]} for r in routes[:3]],
            })
            trace.add("A4 Engine", "ok", int((time.perf_counter() - t0) * 1000),
                      f"{p['label']}: {len(deps)} spots, {len(top)} top-ranked, {fills} fill.")
        except Exception as exc:
            trace.add("A4 Engine", "failed", int((time.perf_counter() - t0) * 1000),
                      f"{p['label']}: {exc}")

    answer = _build_city_answer(spec, preset_results, region_title)
    return _reply(question, spec, trace, kind="city_overview", answer=answer,
                  headline=answer.split("\n")[0], answer_checked=True, presets=preset_results,
                  rain_profile=rain_profile, rain_profile_start=profile_start,
                  forecast_meta=forecast_meta)


def _build_city_answer(spec, preset_results, region_title):
    mm = spec["rainfall_mm"]
    dur = spec.get("duration_h") or 24
    is_forecast = spec["mode"] == "forecast"

    if is_forecast:
        lines = [f"About {mm:g} mm of rain is expected {window_phrase(spec)} in {region_title}."]
    else:
        lines = [f"At {mm:g} mm over {dur:g} hours in {region_title}:"]

    ranked = sorted(preset_results, key=lambda p: (p["fills"], p["top_ranked"]), reverse=True)
    if ranked:
        lines.append("")
        for p in ranked:
            parts = [f"{p['spots']} low spots"]
            if p["top_ranked"]:
                parts.append(f"{p['top_ranked']} top-ranked")
            if p["fills"]:
                parts.append(f"{p['fills']} would fill")
            if p["road_km"]:
                parts.append(f"{p['road_km']} km of roads through worst spots")
            lines.append(f"{p['label']}: {' · '.join(parts)}")

    fills_areas = [p["label"] for p in ranked if p["fills"] > 0]
    if fills_areas:
        lines.append("")
        if len(fills_areas) == len(ranked):
            lines.append("All areas have low spots that would fill at this rainfall.")
        else:
            names = ", ".join(fills_areas[:-1]) + " and " + fills_areas[-1] \
                if len(fills_areas) > 1 else fills_areas[0]
            lines.append(f"At this rainfall, {names} "
                         f"{'have' if len(fills_areas) > 1 else 'has'} low spots that would fill.")

    lines.append("")
    lines.append("This is a terrain screening. Stormwater drains are not in the data, "
                 "so pooling is overstated where drains work.")
    return "\n".join(lines)


def _add_surface_heat(bundle: dict, region, bbox, trace: Trace) -> None:
    """Best-effort: the satellite picture of which parts of the box run hot, from the staged
    composite. Needs no network and no model, so it costs almost nothing; it is simply absent for a
    city where nothing is staged, and the trace says so rather than leaving the gap unexplained."""
    if region is None:
        return
    from bih_engine import surface_heat
    if not surface_heat.available(region):
        return
    t0 = time.perf_counter()
    try:
        sh = surface_heat.read(region, tuple(bbox) if bbox else None, places=bool(bbox))
        if not sh.get("available"):
            trace.add("Surface heat", "unavailable", 0, sh.get("reason", "")[:120])
            return
        facts.add_surface_heat(bundle, sh)
        hot = (sh.get("hottest") or [None])[0]
        trace.add("Surface heat", "ok", int((time.perf_counter() - t0) * 1000),
                  f"Ground here runs {sh['typical_low_c']} to {sh['typical_high_c']}°C at the "
                  f"surface" + (f"; hottest place {hot['surface_c']}°C." if hot else "."))
    except Exception as exc:
        trace.add("Surface heat", "unavailable", 0, str(exc)[:120])


def _add_heat_exposure(bundle: dict, region, box, hours: list, spec: dict, trace: Trace,
                       people: bool = True) -> None:
    """Who is exposed to heat here, against the city's own authority's threshold, plus that
    authority's live readings where it runs a station network. Best-effort: a heat answer without
    the exposure block is still a heat answer, but one with a figure nobody published is not."""
    if region is None:
        return
    from bih_engine import heat_exposure, heat_obs
    try:
        t0 = time.perf_counter()
        ex = heat_exposure.assess(region, box if people else None, hours, stated_c=spec.get("heat_c"))
        facts.add_heat_exposure(bundle, ex)
        if ex.get("available"):
            who = f"~{ex['people_living_here']} people here; " if "people_living_here" in ex else ""
            trace.add("Heat exposure", "ok", int((time.perf_counter() - t0) * 1000),
                      f"{who}judged against {ex['judged_against']}"
                      + (f", which reaches {ex['band_the_forecast_reaches']}."
                         if "band_the_forecast_reaches" in ex else "."))
    except Exception as exc:
        trace.add("Heat exposure", "unavailable", 0, str(exc)[:120])

    if not heat_obs.available(region):
        return
    try:
        t0 = time.perf_counter()
        obs = heat_obs.near(tuple(box))
        facts.add_measured_heat(bundle, obs)
        near = obs["nearest"][0]
        trace.add("Measured heat stress", "ok", int((time.perf_counter() - t0) * 1000),
                  f"{near['name']} station reads {near['wbgt_c']} and the authority rates it "
                  f"{near['rated']}.")
    except Exception as exc:
        trace.add("Measured heat stress", "unavailable", 0, str(exc)[:120])


def _add_seismic(bundle: dict, region, box, spec: dict, trace: Trace) -> None:
    """The published design-level shaking for the ground in the box, and who stands on each level.
    Best-effort and honest about absence: a city with nothing staged says so in the trace and the
    bundle stays empty, so the briefing has nothing to say about shaking rather than something invented."""
    if region is None:
        return
    from bih_engine import seismic
    try:
        t0 = time.perf_counter()
        ex = seismic.exposure(region, tuple(box))
        if not ex.get("available"):
            trace.add("Seismic exposure", "unavailable", 0, ex.get("reason", "")[:120])
            return
        facts.add_seismic(bundle, ex, magnitude=spec.get("magnitude"))
        trace.add("Seismic exposure", "ok", int((time.perf_counter() - t0) * 1000),
                  f"Design-level shaking: most people at intensity "
                  f"{facts.ROMAN[ex['intensity_most_people_feel']]} on this ground "
                  f"(firm rock would give {facts.ROMAN[ex['intensity_on_firm_rock']]}).")
    except Exception as exc:
        trace.add("Seismic exposure", "unavailable", 0, str(exc)[:120])


def _add_wind(bundle: dict, lat: float, lon: float, trace: Trace) -> None:
    """The wind forecast at the middle of the box: speed, gusts, and which way it blows."""
    try:
        from bih_engine import heat as heat_mod
        t0 = time.perf_counter()
        hdata = heat_mod.fetch(lat, lon, hours=48).as_dict()
        facts.add_wind(bundle, hdata)
        if "wind" not in bundle:
            trace.add("Wind", "unavailable", 0, "The forecast carries no wind for this point.")
            return
        now = bundle["wind"]["now"]
        trace.add("Wind", "ok", int((time.perf_counter() - t0) * 1000),
                  f"Wind {now['speed_kmh']} km/h" + (f" from the {now['blowing_from']}." if "blowing_from" in now else "."))
    except Exception as exc:
        trace.add("Wind", "unavailable", 0, str(exc)[:120])


def _add_supplementary_hazards(bundle: dict, region, bbox, trace: Trace) -> None:
    """Best-effort: heat and air quality for the same box the screening ran on, into the bundle.

    Asked at the centre of the drawn box, so the briefing's three hazards are all about the same
    patch of ground rather than silently mixing a neighbourhood with a city. Both providers answer
    at a point, and the notes in the bundle say so. Failures never block the flood analysis.
    """
    if region is None:
        return
    w, s, e, n = bbox if bbox else region.bounds
    lat, lon = (s + n) / 2, (w + e) / 2

    try:
        from bih_engine import heat as heat_mod
        t0 = time.perf_counter()
        hf = heat_mod.fetch(lat, lon, hours=48)
        hdata = hf.as_dict()
        facts.add_heat(bundle, hdata, tz=region.manifest.get("timezone"))
        trace.add("Heat", "ok", int((time.perf_counter() - t0) * 1000),
                  f"Heat index {hdata['current'].get('heat_index_c')}°C, "
                  f"level: {hdata['current_level'].get('label')}.")
    except Exception as exc:
        trace.add("Heat", "unavailable", 0, str(exc)[:120])

    _add_surface_heat(bundle, region, bbox, trace)

    try:
        from bih_engine import air_quality as aq_mod
        t0 = time.perf_counter()
        aqdata = aq_mod.for_region(lat, lon)
        facts.add_air_quality(bundle, aqdata)
        trace.add("Air quality", "ok", int((time.perf_counter() - t0) * 1000),
                  f"AQI {aqdata.get('aqi')}, {aqdata.get('category')}.")
    except Exception as exc:
        trace.add("Air quality", "unavailable", 0, str(exc)[:120])


def _box_km2(bbox) -> float:
    w, s, e, n = bbox
    return round(abs(e - w) * abs(n - s) * 110.574 * 111.320 * abs(math.cos(math.radians((s + n) / 2))), 2)


def _sig2(x: float) -> int:
    return int(float(f"{x:.2g}"))


def _people_by_preset(region, trace) -> dict:
    """People in each of the city's preset areas, most people first. A city question about who lives
    where cannot be answered from one box, and the whole city's total says nothing about which part
    holds most of it, so the areas this product already knows are compared side by side."""
    from bih_engine import popgrid
    rows, last = [], {}
    for p in region.presets:
        t0 = time.perf_counter()
        r = popgrid.density_for(region, tuple(p["bbox"]))
        if not r.get("available"):
            trace.add("Population", "unavailable", 0, f"{p['label']}: {r.get('reason', '')}"[:120])
            continue
        last = r
        people = r["people_in_area"]
        rows.append({"area": p["label"], "roughly_people": people,
                     "roughly_per_km2": _sig2(people / _box_km2(p["bbox"]))})
        trace.add("Population", "ok", int((time.perf_counter() - t0) * 1000),
                  f"{p['label']}: ~{people} people.")
    if not rows:
        raise ValueError("no population estimate for any area of this city")
    rows.sort(key=lambda x: -x["roughly_people"])
    return {
        "areas_compared": len(rows),
        "by_area_most_people_first": rows,
        "area_with_most_people": rows[0]["area"],
        "area_with_the_highest_density": max(rows, key=lambda x: x["roughly_per_km2"])["area"],
        "source": last.get("source", "unknown"), "method": last.get("method", ""),
        "note": "A modelled estimate by area only, never a claim about a street or a home. Only the "
                "areas listed were measured, so a more populated part of the city may lie outside them.",
    }


CITY_COVER = ("the whole city. Each reading is taken once, at the middle of the city, so where a "
              "note says 'this box' it means the whole city.")
# Shaking is not a reading at one point: a city's answer is the sum over everyone who lives in it.
CITY_SUM_COVER = ("the whole city, summed over every part of it where people live. Where a note says "
                  "'this area' or 'this box' it means the whole city.")


def _run_topic(question, spec, topic, bbox, region, trace, city=False) -> dict:
    """Answer a question about one specific data layer — population, heat, air quality or wind — without
    running the full terrain engine. The engine is the costly part (~8 s) and answers about flooding,
    which is not what was asked. The bundle carries only the requested data, so the Narrator writes
    about that and nothing else.

    `city` means the question was about the whole city, not a box: the readings are the city's, and
    the bundle says so, because a neighbourhood and a city are different claims."""
    w, s, e, n = bbox if bbox else (region.bounds if region else (0, 0, 0, 0))
    lat, lon = (s + n) / 2, (w + e) / 2
    region_title = region.manifest.get("title") if region else None
    area_km2 = _box_km2((w, s, e, n))

    bundle: dict = {
        "question": question,
        "topic": topic,
        "area": ({"what_these_findings_cover": CITY_SUM_COVER if topic == "earthquake" else CITY_COVER,
                  "the_city": region_title} if city else {
            "what_these_findings_cover": "one box drawn on the map",
            "box_km2": round(area_km2, 2),
            "the_box_is_inside": region_title,
        }),
        "limits": [],
    }

    if topic == "population" and city:
        try:
            bundle["people"] = _people_by_preset(region, trace)
            bundle["area"]["what_these_findings_cover"] = (
                "the areas this product has set up for the city, compared side by side. They are "
                "not every neighbourhood in it.")
        except Exception as exc:
            trace.add("Population", "failed", 0, str(exc)[:120])

    elif topic == "population":
        try:
            from bih_engine import popgrid
            t0 = time.perf_counter()
            if region is None:
                raise ValueError("no city is selected")
            r = popgrid.density_for(region, (w, s, e, n))
            if not r.get("available"):
                raise ValueError(r.get("reason", "no population estimate for this area"))
            ms = int((time.perf_counter() - t0) * 1000)
            bundle["people"] = {
                "roughly_in_the_whole_box": r["people_in_area"],
                "density_max_per_km2": r.get("max"),
                "density_p95_per_km2": r.get("p95"),
                "source": r.get("source", "unknown"),
                "method": r.get("method", ""),
                "note": "A modelled estimate by area only, never a claim about a street or a home.",
            }
            trace.add("Population", "ok", ms,
                      f"~{r['people_in_area']} people in {area_km2} km².")
        except Exception as exc:
            trace.add("Population", "failed", 0, str(exc)[:120])

    elif topic == "heat":
        try:
            from bih_engine import heat as heat_mod
            t0 = time.perf_counter()
            hf = heat_mod.fetch(lat, lon, hours=48)
            hdata = hf.as_dict()
            facts.add_heat(bundle, hdata, tz=region.manifest.get("timezone") if region else None)
            trace.add("Heat", "ok", int((time.perf_counter() - t0) * 1000),
                      f"Heat index {hdata['current'].get('heat_index_c')}°C, "
                      f"level: {hdata['current_level'].get('label')}.")
        except Exception as exc:
            hdata = {}
            trace.add("Heat", "unavailable", 0, str(exc)[:120])
        # A whole city's people against the forecast at its centre is not "who lives where this applies".
        _add_heat_exposure(bundle, region, (w, s, e, n), hdata.get("hours") or [], spec, trace, people=not city)
        # The forecast says how hot the air is at one point; the satellite says which parts of the
        # box run hotter than others, so a heat question gets both, and the briefing keeps them apart.
        _add_surface_heat(bundle, region, (w, s, e, n), trace)
        if city:    # the whole city set against the whole city says nothing
            for k in ("this_box_runs_hotter_than_the_whole_city_by_c",
                      "this_box_runs_cooler_than_the_whole_city_by_c"):
                bundle.get("surface_heat", {}).pop(k, None)

    elif topic == "air_quality":
        try:
            from bih_engine import air_quality as aq_mod
            t0 = time.perf_counter()
            aqdata = aq_mod.for_region(lat, lon)
            facts.add_air_quality(bundle, aqdata)
            trace.add("Air quality", "ok", int((time.perf_counter() - t0) * 1000),
                      f"AQI {aqdata.get('aqi')}, {aqdata.get('category')}.")
        except Exception as exc:
            trace.add("Air quality", "unavailable", 0, str(exc)[:120])

    elif topic == "wind":
        _add_wind(bundle, lat, lon, trace)

    elif topic == "earthquake":
        _add_seismic(bundle, region, (w, s, e, n), spec, trace)

    _note_limit(bundle, spec)
    out = _reply(question, spec, trace, kind="analysis", facts=bundle, result=None)

    # Narrator + Verifier: the same two-trip loop as the full pipeline, just with a smaller bundle.
    briefing = None
    try:
        reply = narrator.draft(bundle, focus=spec["focus"])
        briefing = narrator.clean(reply.data or {})
        trace.add("A6 Narrator", "ok", reply.ms, briefing["headline"], model=reply.model)
    except llm.LlmUnavailable as exc:
        trace.add("A6 Narrator", "failed", 0, str(exc))
        out["withheld"] = f"The briefing was not written: {exc}"
        return out

    for attempt in range(verifier.MAX_REVISIONS + 1):
        t0 = time.perf_counter()
        try:
            problems = verifier.verify(bundle, briefing)
        except llm.LlmUnavailable as exc:
            trace.add("A7 Verifier", "failed", int((time.perf_counter() - t0) * 1000), str(exc))
            out["withheld"] = WITHHELD_UNCHECKED
            return out
        ms = int((time.perf_counter() - t0) * 1000)
        if not problems:
            trace.add("A7 Verifier", "ok", ms, "Every figure traced to the results; no "
                                               "unsupported claim found.")
            break
        note = "; ".join(f"{p['part']}: {p['problem']}" for p in problems)
        if attempt == verifier.MAX_REVISIONS:
            trace.add("A7 Verifier", "rejected", ms, note, problems=problems)
            break
        trace.add("A7 Verifier", "sent back", ms, note, problems=problems)
        try:
            reply = narrator.revise(bundle, focus=spec["focus"], previous=briefing,
                                    problems=[f"{p['part']}: {p['problem']}" for p in problems])
            briefing = narrator.clean(reply.data or {})
            trace.add("A6 Narrator", "revised", reply.ms, briefing["headline"], model=reply.model)
        except llm.LlmUnavailable as exc:
            trace.add("A6 Narrator", "failed", 0, str(exc))
            out["withheld"] = WITHHELD_UNCHECKED
            return out

    if problems:
        kept, removed = verifier.enforce(briefing, problems)
        out["removed"] = removed
        if kept is None:
            trace.add("A7 Verifier", "withheld", 0, WITHHELD_FAILED)
            out["withheld"] = WITHHELD_FAILED
            return out
        briefing = kept
        trace.add("A7 Verifier", "trimmed", 0,
                  f"{len(removed)} claim(s) removed; the rest held.")

    out["briefing"] = briefing
    return out


def _decline_or_route(question, spec, region, bbox, trace, all_regions) -> dict | None:
    """Answer, without running the analysis, a question that asks for what the product does not do or
    keeps elsewhere. Returns None when the question is one it can screen.

    Costs one model call (A2's) instead of the 20-second rain pipeline, and answers from fixed reviewed
    text (declines.py) rather than from a model that has been handed irrelevant flood figures and asked
    to talk around them."""
    decline = spec.get("decline", "none")

    def done(kind, answer, step_note, **kw):
        trace.add("Scope check", "ok", 0, step_note)
        return _reply(question, spec, trace, kind=kind, answer=answer, answer_checked=True, **kw)

    if spec.get("out_of_range"):
        o = spec["out_of_range"]
        return done("declined", declines.out_of_range(o["asked"], o["limit_mm"]),
                    "That rainfall is past anything this product runs; it was not run.")
    if decline in scenario.DECLINE_ONLY:
        return done("declined", declines.only(decline, spec.get("declined_detail", "")),
                    "Asks for something this product does not do; answered without running the analysis.")
    if decline in ("news", "history"):
        return done("handoff", declines.ROUTED[decline],
                    "That is reported news, which has its own agent; the question was handed to it.",
                    handoff={"tab": "news", "question": spec.get("focus") or question,
                             "place": spec.get("place") or ""})
    if decline == "comparison":
        return done("declined", declines.compare(all_regions or ([region] if region is not None else [])),
                    "A comparison of what each city lets this product see, read from their own manifests.")
    if decline == "preparations":
        title = (region.manifest.get("title") or "this city").split(",")[0] if region is not None else "this city"
        if region is None or not region.manifest.get("preparations"):
            return done("declined", declines.NO_BUDGET.format(city=title),
                        "No documented budget is staged for this city, so no plan can be built.")
        if bbox is None:
            return done("needs_area", declines.NEEDS_AREA_FOR_PLAN,
                        "A preparation plan is built for one area, so one is needed first.")
        return done("handoff", declines.ROUTED["preparations"],
                    "The preparation plan has its own place in the Details tab; it was opened.",
                    handoff={"tab": "details", "question": question, "place": ""})
    if spec.get("topic") == "traffic" and spec.get("scope") == "city" and spec.get("intent") == "analyse":
        return done("declined", declines.TRAFFIC_NEEDS_AN_AREA,
                    "Traffic is read for one area at a time, so a whole-city question asks for an area.")
    if decline in scenario.DECLINE_AND_ANSWER and spec.get("scope") == "city":
        return done("declined", declines.partial_only(decline),
                    "Asks for something this product does not give; a whole-city answer cannot carry "
                    "that limit, so it says so and asks for an area.")
    return None


def run(*, question: str, bbox, region, slider_mm: float = 100.0,
        return_period: int | None = 50, res_m: float = 10.0, emit=None,
        history=None, area_label: str | None = None, place_resolved: str | None = None,
        all_regions: list | None = None) -> dict:
    """Answer one message in a conversation. Never raises for a model failure.

    `bbox` may be None: the message may be about the product rather than an area, or name a place
    the client has yet to find on the map. `history` is the earlier turns, used by A2 only to work
    out what a message refers to. `place_resolved` says the client already found the named place
    and `bbox` is it, so the place is not asked for again.
    """
    trace = Trace(emit)
    history = scenario.clean_history(history)
    periods = region.official_return_periods if region is not None else []

    # --- A2 Scenario: the question becomes parameters -------------------------------------
    region_title = region.manifest.get("title") if region is not None else None
    t0 = time.perf_counter()
    spec, raw = None, None
    try:
        reply = scenario.read(question, history=history,
                              area_label=area_label if bbox is not None else None,
                              region_title=region_title)
        raw = reply.data or {}
        spec = scenario.settle(raw, question=question, slider_mm=slider_mm,
                               published_return_periods=list(periods),
                               forecast_peak_mm=None, history=history,
                               preset_labels=[p["label"] for p in getattr(region, "presets", [])])
        trace.add("A2 Scenario", "ok", reply.ms, spec["reading"], model=reply.model,
                  detail={k: spec[k] for k in ("scope", "intent", "topic", "mode", "rainfall_mm",
                                               "rainfall_source", "return_period_years",
                                               "audience")})
        if spec["ignored_figure"]:
            trace.add("A2 Scenario", "guarded", 0,
                      "A rainfall figure was proposed that is not in your words, so it was "
                      "dropped and the figure on screen used instead.")
    except Exception as exc:
        # Not only LlmUnavailable. A reply that parses but is the wrong shape (a list, a word where a number
        # goes) used to crash `settle` and end the turn with "something went wrong". This is the trust
        # boundary for model output, so anything odd degrades to what is on screen, and the trace says why.
        spec = _default_spec(slider_mm, return_period, question)
        why = str(exc) if isinstance(exc, llm.LlmUnavailable) else f"unusable reply: {type(exc).__name__}: {exc}"
        trace.add("A2 Scenario", "failed", int((time.perf_counter() - t0) * 1000), why[:200])

    if spec["return_period_years"] is None:
        spec["return_period_years"] = return_period

    # "Draw a box on the most populated area": an earlier answer named it, so it is opened like any
    # named place. The browser finds a preset by its label, instantly, and asks again with its box.
    earlier = spec.get("earlier_area")
    if earlier and not spec["place"] and earlier != area_label:
        spec["place"], spec["scope"] = earlier, "area"

    # --- the question asks for something this product does not do, or keeps elsewhere -----------
    routed = _decline_or_route(question, spec, region, bbox, trace, all_regions)
    if routed is not None:
        return routed

    # --- city-wide question: analyse each preset instead of one box -------------------------
    if spec.get("scope") == "city" and spec["intent"] == "analyse":
        # Only a flood or general question gets the rain overview; asking who lives in the city, or
        # how hot or windy it is, is answered from that data and never from the rain forecast.
        if spec.get("topic") in TOPIC_ONLY:
            return _run_topic(question, spec, spec["topic"], None, region, trace, city=True)
        return _run_city_overview(question, spec, region, trace, return_period)

    # --- a message about the product, not about an area: answered from the reference ---------
    if spec["intent"] == "explain":
        t0 = time.perf_counter()
        text = guide.answer(question, history=history, area_label=area_label,
                            region_title=region_title)
        trace.add("A8 Guide", "ok" if text else "failed", int((time.perf_counter() - t0) * 1000),
                  "Answered from the product reference; no area was analysed." if text
                  else "No clean answer could be had.")
        return _reply(question, spec, trace, kind="explain", answer=text or guide.FALLBACK,
                      answer_checked=bool(text))

    # --- they named somewhere: the browser finds it on the map and asks again ---------------
    if spec["place"] and not place_resolved:
        return _reply(question, spec, trace, kind="place_needed", place=spec["place"])

    # A limit that belongs to a question about an area needs the area to give the rest for.
    if spec.get("decline") in scenario.DECLINE_AND_ANSWER:
        if bbox is None:
            return _reply(question, spec, trace, kind="declined",
                          answer=declines.partial_only(spec["decline"]), answer_checked=True)
        spec["limit_note"] = declines.partial_note(spec["decline"])
        trace.add("Scope check", "noted", 0,
                  f"Asks for {spec['limit_note']['asked_for']}: that cannot be given, and the answer will "
                  f"say so before giving what the data supports.")

    if bbox is None:
        return _reply(question, spec, trace, kind="needs_area", answer=NEEDS_AREA)

    # --- topic-specific fast path: skip the engine when the question is not about flooding ----
    topic = spec.get("topic", "overview")
    if topic in TOPIC_ONLY:
        return _run_topic(question, spec, topic, bbox, region, trace)

    # --- A3 Forecast: a tool, not a model, until we have bulletins worth reading -----------
    f_all = None            # the whole forecast, for "when does it start" and "will it rain at all"
    rain_profile = None     # the forecast's real hourly shape, when forecast mode succeeds
    profile_start = None    # when that window starts (ISO, UTC), so the UI can label real hours
    forecast_meta = None    # where it came from and when it was fetched, shown beside the playback
    observed = None         # live gauge rainfall "now", where the region has an open feed
    if spec["mode"] == "replay":
        trace.add("A3 Forecast", "unavailable", 0,
                  "Replaying a past storm needs historical rainfall, which the forecast service "
                  "does not serve. Running it as a what-if at the rainfall on screen instead.")
        spec["mode"] = "whatif"
    elif spec["mode"] == "forecast":
        t0 = time.perf_counter()
        try:
            f = forecast.for_bbox(tuple(bbox), hours=48).as_dict()
            f_all = f
            w = _apply_window(spec, f, region)
            if w:
                # The hourly amounts of exactly this window, so the road playback ends on the
                # same total the engine ran on instead of on a different slice of the forecast.
                rain_profile = _profile_for(f.get("hours", []), w["starts"], int(w["hours"]))
                profile_start = w["starts"]
                forecast_meta = {"source": f["source"], "window_hours": int(w["hours"]),
                                 "fetched_at": datetime.now(timezone.utc).isoformat(timespec="seconds")}
            trace.add("A3 Forecast", "ok", int((time.perf_counter() - t0) * 1000),
                      f"{spec['rainfall_mm']} mm {window_phrase(spec)}.", detail=f["peaks"])
            observed = _observed_now(region, bbox, trace)
        except ForecastUnavailable as exc:
            trace.add("A3 Forecast", "failed", int((time.perf_counter() - t0) * 1000),
                      f"{exc}. Using the rainfall on screen instead.")
            spec["mode"] = "whatif"

    # --- A4 Engine: the only thing here that produces a figure ----------------------------
    t0 = time.perf_counter()
    rp = spec["return_period_years"] if spec["return_period_years"] in periods else None
    result = analyse_area(tuple(bbox), rainfall_mm=spec["rainfall_mm"], res_m=res_m,
                          region=region, return_period=rp)
    result.pop("debug", None)              # depths in metres: internal only, never leave here
    result.pop("debug_excluded", None)
    n = len(result["screening"]["depressions"])
    trace.add("A4 Engine (no model)", "ok", int((time.perf_counter() - t0) * 1000),
              f"{n} places that trap water, ranked.",
              detail={"rainfall_mm": spec["rainfall_mm"], "return_period_years": rp,
                      "res_m": res_m})

    outlook = _rain_outlook(f_all) if f_all else {}
    rainfall_facts = {"mm": spec["rainfall_mm"], "over_hours": spec["duration_h"],
                      "where_this_figure_came_from": spec["rainfall_source"],
                      "is_a_real_forecast": spec["mode"] == "forecast"}
    if observed:
        rainfall_facts["falling_now_at_gauges"] = observed
    elif not (region is not None and region.manifest.get("live_rainfall")):
        rainfall_facts["this_city_has_no_live_rain_gauge_so_whether_it_is_raining_now_is_not_observed"] = True
    if spec["mode"] == "forecast" and spec.get("window_anchor"):
        # When the rain falls is part of the finding. "Over the next 6 hours" is only true of hours that
        # start now; the wettest stretch of the next two days can start tomorrow.
        rainfall_facts["window_is"] = WINDOW_WORDS[spec["window_anchor"]]
        rainfall_facts["window_starts_in_hours"] = spec["window_starts_in_hours"]
        if spec.get("window_cut_short"):
            rainfall_facts["window_is_cut_short_by_the_end_of_the_forecast"] = True
        if spec.get("window_fell_back"):
            rainfall_facts["the_time_asked_about_is_beyond_the_forecast_so_this_is_the_wettest_stretch"] = True
        if spec.get("window_time_has_passed"):
            rainfall_facts["the_time_asked_about_has_already_passed_so_this_is_the_forecast_from_now"] = True
        if spec.get("window_rounded_up"):
            rainfall_facts["the_forecast_is_hourly_so_this_is_the_whole_next_hour_not_a_shorter_stretch"] = True
        rainfall_facts.update(outlook)
    elif spec["mode"] == "whatif" and not spec.get("rainfall_asked_as"):
        # Rain nobody forecast and nobody asked for: the rainfall left on the slider. Said so, below.
        rainfall_facts["this_is_a_what_if_at_the_rainfall_set_on_the_slider_not_a_forecast"] = True
    if spec["mode"] == "whatif" and spec.get("rainfall_asked_as"):
        rainfall_facts["the_asker_wrote"] = spec["rainfall_asked_as"]
    bundle = facts.build(result, question=question, rainfall=rainfall_facts,
                         region_title=region_title)
    _note_limit(bundle, spec)
    if topic == "traffic":
        from bih_engine import traffic
        mix = None
        try:
            mix = traffic.road_mix(region.read_osm(tuple(bbox)).roads, tuple(bbox))
        except Exception:                   # without the road mix both effects are offered, which is cautious
            pass
        facts.add_traffic(bundle, traffic.assess(
            spec["rainfall_mm"], spec.get("duration_h"), rain_profile,
            result["screening"].get("routes_affected", []), mix))

    # --- supplementary hazards: heat + air quality, best-effort, and ONLY for a general question. A
    # question about water is not answered with the heat index; that was noise and ~6 s of waiting.
    if topic == "overview":
        _add_supplementary_hazards(bundle, region, bbox, trace)

    out = _reply(question, spec, trace, kind="analysis", facts=bundle, result=result,
                 rain_profile=rain_profile, rain_profile_start=profile_start,
                 forecast_meta=forecast_meta)

    # --- A6 Narrator and A7 Verifier, up to two trips back --------------------------------
    briefing, problems = None, []
    try:
        reply = narrator.draft(bundle, focus=spec["focus"])
        briefing = narrator.clean(reply.data or {})
        trace.add("A6 Narrator", "ok", reply.ms, briefing["headline"], model=reply.model)
    except llm.LlmUnavailable as exc:
        trace.add("A6 Narrator", "failed", 0, str(exc))
        out["withheld"] = f"The briefing was not written: {exc}"
        return out

    for attempt in range(verifier.MAX_REVISIONS + 1):
        t0 = time.perf_counter()
        try:
            problems = verifier.verify(bundle, briefing)
        except llm.LlmUnavailable as exc:
            trace.add("A7 Verifier", "failed", int((time.perf_counter() - t0) * 1000), str(exc))
            out["withheld"] = WITHHELD_UNCHECKED
            return out
        ms = int((time.perf_counter() - t0) * 1000)
        if not problems:
            trace.add("A7 Verifier", "ok", ms, "Every figure traced to the results; no "
                                               "unsupported claim found.")
            break
        note = "; ".join(f"{p['part']}: {p['problem']}" for p in problems)
        if attempt == verifier.MAX_REVISIONS:
            trace.add("A7 Verifier", "rejected", ms, note, problems=problems)
            break
        trace.add("A7 Verifier", "sent back", ms, note, problems=problems)
        try:
            reply = narrator.revise(bundle, focus=spec["focus"], previous=briefing,
                                    problems=[f"{p['part']}: {p['problem']}" for p in problems])
            briefing = narrator.clean(reply.data or {})
            trace.add("A6 Narrator", "revised", reply.ms, briefing["headline"], model=reply.model)
        except llm.LlmUnavailable as exc:
            trace.add("A6 Narrator", "failed", 0, str(exc))
            out["withheld"] = WITHHELD_UNCHECKED
            return out

    if problems:
        kept, removed = verifier.enforce(briefing, problems)
        out["removed"] = removed
        if kept is None:
            trace.add("A7 Verifier", "withheld", 0, WITHHELD_FAILED)
            out["withheld"] = WITHHELD_FAILED
            return out
        briefing = kept
        trace.add("A7 Verifier", "trimmed", 0,
                  f"{len(removed)} claim(s) removed; the rest held.")

    out["briefing"] = briefing
    return out
