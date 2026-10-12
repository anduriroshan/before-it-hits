"""A5 Planner: a short, plain read on the preparations the engine ranked - the trade-offs only.

The list itself is deterministic (`bih_engine.preparations.rank`): which spot, which measure, what
it costs, in what order. This agent does the soft part the plan asks of A5 - it "explains the
trade-offs" and names the highest-value lever - in two or three sentences a planner can act on.

The hard rules, enforced not just asked (the same spine as every other agent here):
  - No number that is not already in the ranked plan. The budget, the costs, the counts are the
    engine's; a figure the plan does not contain is removed and the note is rewritten or dropped.
  - Choose from the ranked candidates only. It may not invent a measure, an asset or a spot.
  - Preparation only. No dispatch, no evacuation, no "send a team now" - those words fail the note.
Fails closed: if it cannot produce a clean note, none is shown and the costed list stands alone.
"""
from __future__ import annotations

import json

from . import facts, llm

SYSTEM = """You write two or three plain sentences for a city planner about a ranked list of flood
preparations for one neighbourhood. You are given the ranked plan: the worst pooling spots, the
measure chosen for each, its cost, and the budget line it is drawn from.

Rules, each checked afterwards:
- Say what to do first and why - which measure is the highest-value lever here, and the trade-off
  against the others. Ground every sentence in the ranked plan you are given.
- Use no number that is not in the plan. No invented cost, count, percentage, time or depth. The
  only figures you may use are those already in the plan.
- Choose only from the measures and spots in the plan. Invent no asset, team or measure.
- Preparation and planning only. Never write dispatch, evacuation, rescue or 'send a team now'.
- Name the honest limit in a clause: this is pre-season or pre-storm preparation against modelled
  rain-pooling, not a live response plan."""

# A note that reads as live response is worse than no note. These are stripped as a class, not
# nudged against in the prompt alone.
FORBIDDEN = ("dispatch", "evacuat", "rescue", "send a team", "send teams", "deploy now")


def _plan_facts(plan: dict, region_title: str) -> dict:
    """Just the parts of the ranked plan a note may lean on - every number here already lives in
    the deterministic plan, so the arithmetic check has something to trace each figure back to."""
    return {
        "city": region_title,
        "budget_line": plan.get("budget_line"),
        "budget_total_inr": plan.get("budget_total_inr"),
        "budget_used_inr": plan.get("budget_used_inr"),
        "preparations": [
            {"measure": it["measure"], "where": it["where"], "when": it["when"],
             "cost_inr": it["cost_inr"], "pooling_rank": it["pooling_rank"]}
            for it in plan.get("items", [])
        ],
        "people_in_worst_spots": (plan.get("covers") or {}).get("people_in_worst_spots"),
        "not_worth_the_budget": [x.get("measure") for x in plan.get("infeasible", [])],
    }


def write(region_title: str, plan: dict, *, model: str = llm.FAST_MODEL) -> dict | None:
    """Return {note} or None (fail closed). Never raises for a model failure."""
    if not plan.get("available") or not plan.get("items"):
        return None
    pfacts = _plan_facts(plan, region_title)
    allowed = facts.allowed_numbers(pfacts)
    prompt = f"Ranked preparation plan:\n{json.dumps(pfacts, indent=1, ensure_ascii=False)}"
    for _ in range(2):
        try:
            text = llm.ask(prompt, model=model, system=SYSTEM, temperature=0.3,
                           thinking="low").text.strip()
        except llm.LlmUnavailable:
            return None
        bad_number = facts.untraceable_numbers(text, allowed)
        bad_word = any(w in text.lower() for w in FORBIDDEN)
        if not bad_number and not bad_word:
            return {"note": text}
        prompt += ("\n\nYour previous reply broke a rule (an unsupported number, or a dispatch or "
                   "evacuation word). Rewrite it using only the figures in the plan, as preparation "
                   "advice only.")
    return None     # could not get a clean note; show the costed list alone rather than an unchecked one
