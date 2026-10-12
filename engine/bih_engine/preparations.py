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
