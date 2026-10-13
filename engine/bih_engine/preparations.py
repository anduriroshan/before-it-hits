"""F9: preparations within what the city actually has, ranked and capped by its real budget.

This is the "so what do we do" layer. It never dispatches and never routes an evacuation - Google's
terms bar emergency-response use, and a dispatch decision is not something a 30 m terrain screening
could validate anyway. It *plans*: given the worst pooling spots the screening already found, the
measures the city's preparedness budget actually pays for, and the ceiling on that budget, it fills
the budget greedily from the worst spot down and says what each rupee buys and where.

Every figure here is either from the engine (which spot, what rank, which road) or from the city's
documented budget lines in the region manifest (unit costs, the budget ceiling). No figure is
invented, and no language model runs in this file: a model only phrases the result afterwards, and
even then may use no number that is not already here (see bih_agents/planner.py).

It also has to be able to say "nothing". A planner handed a spending list every time they look at a
map learns to ignore the list, and most days are ordinary - the rain forecast is the rain this city
gets, and its drains were built for it. So the plan is gated on the rainfall before it is gated on
anything else, by two deterministic tests:

  - Is this an ordinary amount of rain? Judged against the India Meteorological Department's own
    published daily classes, not against anything we invented. Light rain or less is a normal day,
    and a normal day does not get the preparedness budget spent on it.
  - If it is more than that, does it actually reach anything? A spot only earns a measure when this
    rainfall fills it, which the screening already computes per spot as `rain_to_fill_mm`.

Either way the answer is no measures and the reason, with the rainfall it would take to change it -
not a shorter list of things to buy anyway. Neither test says the city is safe, and the quiet
verdict carries the limits that say so.

What it deliberately does not do:
  - No per-building or per-home claim. A measure addresses a spot and the road it sits on.
  - No absolute water depth. Spots are ranked against each other, never quoted in metres.
  - No live dispatch, evacuation routing or "send a team now". Preparation and planning only.
  - No "the city is fine". A quiet verdict is about this rainfall in this box, nothing wider.
"""
from __future__ import annotations

# A spot has to rank at least this high to be worth spending against. Below it the pooling is not
# distinguishable from the terrain error, so a measure there would be spending against noise.
MIN_RANK = 3
TOP_RANK = 4
# The list is a prioritised plan an officer can act on, not a dump of every low spot in the box.
MAX_ITEMS = 6
# How close to filling a spot has to be before it is worth preparing against anyway. A spot that
# needs 5% more rain than the forecast carries is a spot inside the forecast's own error.
NEAR_MISS = 1.05

# The top of "light rain" on the India Meteorological Department's daily rainfall classes, which is
# where an ordinary day stops being ordinary. Their boundary, published and named - we are not the
# ones deciding what counts as a normal amount of rain for a city.
ROUTINE_MM = 15.5
MODERATE_MM = 15.6
RAIN_CLASS_SOURCE = ("India Meteorological Department daily rainfall classes "
                     "(light rain is up to 15.5 mm; moderate begins at 15.6 mm)")


def _measure_for(rank: int, is_worst: bool, measures: dict) -> str | None:
    """Which documented measure fits a spot of this severity. The single worst spot earns the
    highest-value structural lever (clearing its outlet); other top-ranked spots get a pump; the
    rest get a barricade. Only measures the city actually budgets for are ever returned."""
    if is_worst:
        order = ["clear_outlet_drain", "pre_position_pump", "barricade_low_stretch"]
    elif rank >= TOP_RANK:
        order = ["pre_position_pump", "barricade_low_stretch"]
    else:
        order = ["barricade_low_stretch"]
    for op in order:
        if op in measures:
            return op
    return None


# Measures that act on the water at a spot. A barricade keeps vehicles out of a low stretch; it does
# not drain it, reopen the road or move anyone, so it is not counted as reducing exposure.
REDUCING_MEASURES = ("clear_outlet_drain", "pre_position_pump")


def treated_spot_ids(plan: dict) -> list[str]:
    """The spots whose pooling a plan's measures act on, for the best-case 'after' run."""
    return [it["spot_id"] for it in plan.get("items", []) if it["op"] in REDUCING_MEASURES]


def _top_filling(result: dict) -> set[str]:
    return {f["properties"]["id"] for f in result.get("geojson", {}).get("features", [])
            if f["properties"]["pooling_index"] >= TOP_RANK and f["properties"].get("fills_at_rainfall")}


def _road_km(result: dict) -> float:
    return round(sum(r["length_m"] for r in result["screening"].get("routes_affected", [])) / 1000.0, 1)


def _nearest_5_pct(before: float, after: float) -> int | None:
    if not before:
        return None
    return int(round(100.0 * (1.0 - after / before) / 5.0) * 5)


def before_after(baseline: dict, after: dict, plan: dict) -> dict:
    """F10: the same box with and without the preparations - the difference in exposure.

    `baseline` is the analysis as it is; `after` is the same analysis re-run with the spots the
    plan acts on taken out of the exposed set (see `analyse_grid(treated=...)`). Everything in the
    comparison is therefore computed by the engine, twice, and subtracted: no model, no estimate of
    what a pump achieves.

    It is a BEST CASE. It assumes each measure fully takes its spot out of the flood, which a pump
    or a cleared drain will rarely do completely, and the screening has no storm-drain data to say how
    much any of them would help. So it is an upper bound on what the plan can buy, and says so.
    """
    ids = treated_spot_ids(plan)
    if not ids:
        return {"available": False,
                "reason": "the plan lists only barricades, which keep traffic out of a low stretch "
                          "but do not reduce what the water reaches, so there is no before and after"}
    # The treated spots still exist and still fill on the map; what changes is whether they count as
    # exposed. So "after" is the baseline set minus the spots the plan acts on, not a fresh count.
    top_b = _top_filling(baseline)
    spots_b, spots_a = len(top_b), len(top_b - set(ids))
    out = {
        "available": True,
        "best_case": True,
        "treated_spot_ids": ids,
        "measures_counted": len(ids),
        "measures_not_counted": len(plan.get("items", [])) - len(ids),
        "worst_spots_that_fill": {"before": spots_b, "after": spots_a},
        "road_km_through_worst_spots": {"before": _road_km(baseline), "after": _road_km(after)},
        "people_in_worst_spots": None,
        "limits": [
            "A best case: it assumes every counted measure fully takes its spot out of the flood. A "
            "pump or a cleared drain reduces a problem and rarely ends it.",
            "Barricades keep vehicles out of a low stretch; they do not reduce who is exposed or "
            "reopen the road, so they are not counted here.",
            "No storm-drain data exists, so how much any measure helps is not modelled - only how "
            "much of the exposure it acts on.",
        ],
    }
    eb, ea = baseline.get("exposure") or {}, after.get("exposure") or {}
    if eb.get("available") and ea.get("available"):
        out["people_in_worst_spots"] = {"before": eb["people_in_worst_spots"],
                                        "after": ea["people_in_worst_spots"],
                                        "share_addressed_pct": _nearest_5_pct(
                                            eb["people_in_worst_spots"], ea["people_in_worst_spots"])}
    return out


def _quiet_day(cand: list[dict], rainfall_mm: float, capabilities: dict, *, because: str) -> dict:
    """The plan when there is nothing worth spending the preparedness budget on.

    Every figure here is the screening's own or the IMD's: the rainfall the run was given, the
    class that rainfall falls in, and the `rain_to_fill_mm` the screening already computed per
    spot. It says nothing about the city beyond this box and nothing about any other hazard.
    """
    budget = int(float(capabilities.get("budget_total_inr") or 0))
    needed = min(d["rain_to_fill_mm"] for d in cand) if cand else None
    fills = sum(1 for d in cand if d["rain_to_fill_mm"] <= float(rainfall_mm))
    ordinary = because == "light_rain"
    return {
        "available": True,
        "routine": True,
        "because": because,
        "items": [],
        "rainfall_mm": round(float(rainfall_mm), 1),
        "moderate_rain_begins_mm": MODERATE_MM,
        "rain_class_source": RAIN_CLASS_SOURCE,
        "spots_considered": len(cand),
        "spots_that_fill_at_this_rainfall": fills,
        "rain_to_first_spot_mm": None if needed is None else round(float(needed), 1),
        "headroom_mm": None if needed is None else round(float(needed) - float(rainfall_mm), 1),
        "budget_total_inr": budget,
        "budget_used_inr": 0,
        "budget_left_inr": budget or None,
        "budget_line": capabilities.get("budget_line"),
        "verdict": (
            "An ordinary amount of rain, so nothing to buy for it. The India Meteorological "
            "Department would call this light rain or less, which is the weather a city's drains "
            "are built for - the preparedness budget is for the days that are not this one."
            if ordinary else
            "Nothing to buy for this rainfall. The screening finds low spots in this box, but "
            "none of the ones worth spending against fill at the rainfall being looked at, so "
            "the preparedness budget is better left unspent than spent here today."),
        "source": capabilities.get("source"),
        "limits": [
            "This is about this box and this rainfall only. It is not a verdict on the city, on "
            "any other day, or on any hazard other than rain pooling.",
            "The screening has no storm-drain data, so it already overstates pooling. A quiet "
            "answer here is therefore a conservative one, not an optimistic one.",
            "Rain that falls outside the window the screening was run on is not in this answer.",
            "Routine readiness a city does anyway - clearing drains before the season, keeping "
            "pumps serviced - is not in scope here and is not cancelled by this.",
        ],
    }


def rank(result: dict, capabilities: dict | None, rainfall_mm: float | None = None) -> dict:
    """A greedy preparation plan for one drawn box, capped by the documented budget.

    `result` is an `analyse_area` result. `capabilities` is the region manifest's `preparations`
    block: a budget ceiling, the measures it pays for (each with a unit cost), and the measures it
    explicitly cannot (capital works), with reasons. `rainfall_mm` is the rainfall the result was
    run on; given it, the plan only spends against spots that rainfall actually fills, and returns
    an explicit "nothing to buy" when it fills none. Returns a plan, or an honest "not available".
    """
    if not capabilities or not capabilities.get("measures"):
        return {"available": False,
                "reason": "no preparedness budget or capability inventory is documented for this "
                          "region, so preparations cannot be ranked against real limits here"}

    measures = capabilities["measures"]
    budget_total = float(capabilities.get("budget_total_inr") or 0)
    deps = result.get("screening", {}).get("depressions", [])
    # Worst first: most water trapped (pooling_index), ties to whichever fills sooner.
    cand = sorted((d for d in deps if d["pooling_index"] >= MIN_RANK),
                  key=lambda d: (-d["pooling_index"], d["fill_order"]))

    # Spend only where this rainfall puts water, plus spots that sit just inside the forecast's own
    # error. The rest are real low spots and stay in the screening - they are simply not what this
    # forecast is asking the budget for.
    held_back = 0
    if rainfall_mm is not None and cand:
        # An ordinary day first. Low spots take water on any wet day, so "something fills" is not
        # on its own a reason to spend - the question is whether this is a day worth spending on,
        # and that is the IMD's call, not ours.
        if float(rainfall_mm) <= ROUTINE_MM:
            return _quiet_day(cand, float(rainfall_mm), capabilities, because="light_rain")
        reach = float(rainfall_mm) * NEAR_MISS
        at_risk = [d for d in cand if d["rain_to_fill_mm"] <= reach]
        if not at_risk:
            return _quiet_day(cand, float(rainfall_mm), capabilities, because="nothing_fills")
        held_back = len(cand) - len(at_risk)
        cand = at_risk

    items: list[dict] = []
    spent = 0.0
    skipped_for_budget = 0
    for i, d in enumerate(cand):
        if len(items) >= MAX_ITEMS:
            break
        op = _measure_for(d["pooling_index"], i == 0, measures)
        if op is None:
            continue
        m = measures[op]
        cost = float(m.get("cost_inr") or 0)
        if budget_total and spent + cost > budget_total:
            skipped_for_budget += 1
            continue
        spent += cost
        road = d.get("nearest_road")
        items.append({
            "op": op,
            "measure": m.get("label", op.replace("_", " ")),
            "spot_id": d["id"],
            "pooling_rank": d["pooling_index"],
            "fills_first_order": d["fill_order"],
            "where": (f"{road['name']} (about {road['distance_m']} m away)" if road
                      else "no named road is close enough to use as a landmark"),
            "when": m.get("when", "before forecast rain"),
            "cost_inr": int(cost),
        })

    exposure = result.get("exposure") or {}
    covers_people = exposure.get("people_in_worst_spots") if exposure.get("available") else None

    return {
        "available": True,
        "routine": False,
        "rainfall_mm": None if rainfall_mm is None else round(float(rainfall_mm), 1),
        "budget_total_inr": int(budget_total),
        "budget_used_inr": int(spent),
        "budget_left_inr": int(budget_total - spent) if budget_total else None,
        "budget_line": capabilities.get("budget_line"),
        "items": items,
        "spots_considered": len(cand),
        "spots_not_listed": max(0, len(cand) - len(items)),
        "spots_skipped_for_budget": skipped_for_budget,
        # Ranked spots this rainfall does not reach. They are not a backlog and not a shortfall;
        # they are simply not what today is asking the budget for.
        "spots_dry_at_this_rainfall": held_back,
        "covers": {
            "people_in_worst_spots": covers_people,
            "note": ("A modelled distribution across the worst spots these measures target, not a "
                     "count of residents." if covers_people is not None
                     else "Population exposure is not modelled for this region."),
        },
        "infeasible": [
            {"op": x.get("op"), "measure": x.get("label"), "reason": x.get("reason")}
            for x in capabilities.get("infeasible", [])
        ],
        "source": capabilities.get("source"),
        "limits": [
            "Preparation and planning only - never live dispatch, evacuation routing or a "
            "'send a team now' instruction.",
            "Costs and the budget ceiling are the city's own documented figures, not a quote. The "
            "measure chosen for each spot is a fixed rule by severity, not a tendered plan.",
            "Spots are ranked against each other in this box, never quoted as a depth.",
            "This plan is for the rainfall being looked at. Low spots that rainfall does not reach "
            "are left out of it, and a different forecast gives a different list.",
        ],
    }
