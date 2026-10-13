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

What it deliberately does not do:
  - No per-building or per-home claim. A measure addresses a spot and the road it sits on.
  - No absolute water depth. Spots are ranked against each other, never quoted in metres.
  - No live dispatch, evacuation routing or "send a team now". Preparation and planning only.
"""
from __future__ import annotations

# A spot has to rank at least this high to be worth spending against. Below it the pooling is not
# distinguishable from the terrain error, so a measure there would be spending against noise.
MIN_RANK = 3
TOP_RANK = 4
# The list is a prioritised plan an officer can act on, not a dump of every low spot in the box.
MAX_ITEMS = 6


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


def rank(result: dict, capabilities: dict | None) -> dict:
    """A greedy preparation plan for one drawn box, capped by the documented budget.

    `result` is an `analyse_area` result. `capabilities` is the region manifest's `preparations`
    block: a budget ceiling, the measures it pays for (each with a unit cost), and the measures it
    explicitly cannot (capital works), with reasons. Returns a plan, or an honest "not available".
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
        "budget_total_inr": int(budget_total),
        "budget_used_inr": int(spent),
        "budget_left_inr": int(budget_total - spent) if budget_total else None,
        "budget_line": capabilities.get("budget_line"),
        "items": items,
        "spots_considered": len(cand),
        "spots_not_listed": max(0, len(cand) - len(items)),
        "spots_skipped_for_budget": skipped_for_budget,
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
        ],
    }
