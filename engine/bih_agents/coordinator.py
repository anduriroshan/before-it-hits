"""A1 Coordinator: holds the question and the drawn box, runs the specialists, keeps the trace.

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

from bih_engine import analyse_area, forecast
from bih_engine.forecast import ForecastUnavailable

from . import facts, llm, narrator, scenario, verifier

# A briefing nobody checked is worse than no briefing, so these are the only two outcomes.
WITHHELD_UNCHECKED = ("The briefing was withheld: the checking agent could not be reached, and "
                      "this product does not publish a briefing that nothing has checked.")
WITHHELD_FAILED = ("The briefing was withheld: its main claim was not supported by the results.")


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


def _default_spec(slider_mm: float, return_period: int | None, question: str) -> dict:
    """What we run when the Scenario agent is unreachable: exactly what is on screen."""
    return {
        "reading": "Read from the controls on screen, not from the question: "
                   "the scenario agent could not be reached.",
        "focus": question, "audience": "officer", "mode": "whatif",
        "rainfall_mm": float(slider_mm), "rainfall_source": "the rainfall already set on screen",
        "duration_h": None, "return_period_years": return_period, "ignored_figure": False,
        "needs_clarification": False, "question_back": "",
    }


def run(*, question: str, bbox, region, slider_mm: float = 100.0,
        return_period: int | None = 50, res_m: float = 10.0, emit=None) -> dict:
    """Answer a question about one drawn box. Never raises for a model failure."""
    trace = Trace(emit)
    periods = region.official_return_periods if region is not None else []

    # --- A2 Scenario: the question becomes parameters -------------------------------------
    t0 = time.perf_counter()
    spec, raw = None, None
    try:
        reply = scenario.read(question)
        raw = reply.data or {}
        spec = scenario.settle(raw, question=question, slider_mm=slider_mm,
                               published_return_periods=list(periods),
                               forecast_peak_mm=None)
        trace.add("A2 Scenario", "ok", reply.ms, spec["reading"], model=reply.model,
                  detail={k: spec[k] for k in ("mode", "rainfall_mm", "rainfall_source",
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

    # --- A3 Forecast: a tool, not a model, until we have bulletins worth reading -----------
    rain_profile = None     # the forecast's real hourly shape, when forecast mode succeeds
    if spec["mode"] == "replay":
        trace.add("A3 Forecast", "unavailable", 0,
                  "Replaying a past storm needs historical rainfall, which the forecast service "
                  "does not serve. Running it as a what-if at the rainfall on screen instead.")
        spec["mode"] = "whatif"
    elif spec["mode"] == "forecast":
        t0 = time.perf_counter()
        try:
            f = forecast.for_bbox(tuple(bbox), hours=48).as_dict()
            window = "3" if "3" in f["peaks"] else next(iter(f["peaks"]), None)
            if window:
                spec["rainfall_mm"] = f["peaks"][window]["mm"]
                spec["duration_h"] = float(window)
                spec["rainfall_source"] = f"the worst {window} h in the next {f['hours_ahead']} h"
            # The real hourly shape, so the road playback can follow the forecast's own build-up
            # rather than spreading a total evenly. Capped so the scrubber stays usable.
            rain_profile = [h["mm"] for h in f.get("hours", [])][:24] or None
            trace.add("A3 Forecast", "ok", int((time.perf_counter() - t0) * 1000),
                      f"Worst {window} h window: {spec['rainfall_mm']} mm.", detail=f["peaks"])
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

    bundle = facts.build(result, question=question,
                         rainfall={"mm": spec["rainfall_mm"], "over_hours": spec["duration_h"],
                                   "where_this_figure_came_from": spec["rainfall_source"],
                                   "is_a_real_forecast": spec["mode"] == "forecast"},
                         region_title=(region.manifest.get("title") if region else None))

    out = {"question": question, "scenario": spec, "facts": bundle, "result": result,
           "rain_profile": rain_profile, "briefing": None, "withheld": None,
           "removed": [], "trace": trace.steps}

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
