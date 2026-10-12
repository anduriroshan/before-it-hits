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

import time
from datetime import datetime, timezone

from bih_engine import analyse_area, forecast
from bih_engine.forecast import ForecastUnavailable

from . import facts, guide, llm, narrator, scenario, verifier

# A briefing nobody checked is worse than no briefing, so these are the only two outcomes.
WITHHELD_UNCHECKED = ("The briefing was withheld: the checking agent could not be reached, and "
                      "this product does not publish a briefing that nothing has checked.")
WITHHELD_FAILED = ("The briefing was withheld: its main claim was not supported by the results.")
NEEDS_AREA = ("I answer about a specific place, so I need one first. Draw a box on the map, pick "
              "an area below, or name a place and I will find it.")

# What a message with no analysis to show carries, so every kind of reply has the same shape.
def _reply(question, spec, trace, **kw) -> dict:
    base = {"kind": "analysis", "question": question, "scenario": spec, "facts": None, "result": None,
            "rain_profile": None, "rain_profile_start": None, "forecast_meta": None,
            "briefing": None, "withheld": None, "removed": [], "answer": None,
            "answer_checked": False, "place": None, "trace": trace.steps}
    base.update(kw)
    return base


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
        "focus": question, "audience": "officer", "mode": "whatif",
        "rainfall_mm": float(slider_mm), "rainfall_source": "the rainfall already set on screen",
        "duration_h": None, "return_period_years": return_period, "ignored_figure": False,
        "needs_clarification": False, "question_back": "",
        "intent": "analyse", "place": "", "reused_previous_rainfall": False,
    }


def run(*, question: str, bbox, region, slider_mm: float = 100.0,
        return_period: int | None = 50, res_m: float = 10.0, emit=None,
        history=None, area_label: str | None = None, place_resolved: str | None = None) -> dict:
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
    t0 = time.perf_counter()
    spec, raw = None, None
    try:
        reply = scenario.read(question, history=history,
                              area_label=area_label if bbox is not None else None)
        raw = reply.data or {}
        spec = scenario.settle(raw, question=question, slider_mm=slider_mm,
                               published_return_periods=list(periods),
                               forecast_peak_mm=None, history=history)
        trace.add("A2 Scenario", "ok", reply.ms, spec["reading"], model=reply.model,
                  detail={k: spec[k] for k in ("intent", "mode", "rainfall_mm", "rainfall_source",
                                               "return_period_years", "audience")})
        if spec["ignored_figure"]:
            trace.add("A2 Scenario", "guarded", 0,
                      "A rainfall figure was proposed that is not in your words, so it was "
                      "dropped and the figure on screen used instead.")
    except llm.LlmUnavailable as exc:
        spec = _default_spec(slider_mm, return_period, question)
        trace.add("A2 Scenario", "failed", int((time.perf_counter() - t0) * 1000), str(exc))

    if spec["return_period_years"] is None:
        spec["return_period_years"] = return_period

    region_title = region.manifest.get("title") if region is not None else None

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

    if bbox is None:
        return _reply(question, spec, trace, kind="needs_area", answer=NEEDS_AREA)

    # --- A3 Forecast: a tool, not a model, until we have bulletins worth reading -----------
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
            window = _choose_window(spec.get("duration_h"), f["peaks"])
            if window:
                peak = f["peaks"][window]
                spec["rainfall_mm"] = peak["mm"]
                spec["duration_h"] = float(window)
                spec["rainfall_source"] = f"the wettest {window} h in the next {f['hours_ahead']} h"
                # The hourly amounts of exactly this window, so the road playback ends on the
                # same total the engine ran on instead of on a different slice of the forecast.
                rain_profile = _profile_for(f.get("hours", []), peak["starts"], int(window))
                profile_start = peak["starts"]
                forecast_meta = {"source": f["source"], "window_hours": int(window),
                                 "fetched_at": datetime.now(timezone.utc).isoformat(timespec="seconds")}
            trace.add("A3 Forecast", "ok", int((time.perf_counter() - t0) * 1000),
                      f"Wettest {window} h window: {spec['rainfall_mm']} mm.", detail=f["peaks"])
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

    rainfall_facts = {"mm": spec["rainfall_mm"], "over_hours": spec["duration_h"],
                      "where_this_figure_came_from": spec["rainfall_source"],
                      "is_a_real_forecast": spec["mode"] == "forecast"}
    if observed:
        rainfall_facts["falling_now_at_gauges"] = observed
    bundle = facts.build(result, question=question, rainfall=rainfall_facts,
                         region_title=region_title)

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
