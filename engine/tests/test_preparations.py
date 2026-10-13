"""Preparations within budget (F9): offline.

Two things are checked. The ranker is deterministic and must behave like the plan says - worst spot
first, the right measure for each severity, the city's real budget as the ceiling, low spots left
out. And the planner note, which a model writes, must never introduce a figure the list does not
already contain, nor a dispatch word - it fails closed to no note at all.
"""
from bih_engine import preparations as prep
from bih_agents import planner
from bih_agents.llm import LlmUnavailable

CAPS = {
    "budget_total_inr": 50000000,
    "budget_line": "Kochi Corporation seasonal preparedness line",
    "measures": {
        "clear_outlet_drain": {"label": "Clear / desilt the outlet drain", "cost_inr": 180000,
                               "when": "before the monsoon season"},
        "pre_position_pump": {"label": "Pre-position a dewatering pump", "cost_inr": 420000,
                              "when": "ahead of forecast rain"},
        "barricade_low_stretch": {"label": "Barricade the low stretch", "cost_inr": 60000,
                                  "when": "ahead of forecast rain"},
    },
    "infeasible": [
        {"op": "road_raising", "label": "Raise the road level", "reason": "Capital work"},
    ],
    "source": "Kerala SDRF 2026 budget rules",
}


def _dep(id, idx, order, road=None):
    return {"id": id, "pooling_index": idx, "fill_order": order, "contributing_ha": 1.0,
            "rain_to_fill_mm": 20.0, "nearest_road": road}


def _result(deps, exposure=None):
    r = {"screening": {"depressions": deps}}
    if exposure is not None:
        r["exposure"] = exposure
    return r


def _deps():
    return [
        _dep("d_01", 5, 2, {"name": "Banerji Rd", "distance_m": 15}),
        _dep("d_02", 4, 1, {"name": "NH 66 Bypass", "distance_m": 40}),
        _dep("d_03", 4, 3, None),
        _dep("d_04", 3, 4, {"name": "Pipeline Rd", "distance_m": 25}),
        _dep("d_05", 2, 5, {"name": "Lane 9", "distance_m": 10}),   # below MIN_RANK
    ]


def test_the_worst_spot_gets_the_structural_lever_and_the_rest_follow_by_severity():
    plan = prep.rank(_result(_deps()), CAPS)
    ops = [it["op"] for it in plan["items"]]
    assert ops == ["clear_outlet_drain", "pre_position_pump", "pre_position_pump",
                   "barricade_low_stretch"]
    assert plan["items"][0]["where"].startswith("Banerji Rd")


def test_a_spot_below_the_minimum_rank_is_left_out():
    plan = prep.rank(_result(_deps()), CAPS)
    assert plan["spots_considered"] == 4           # the rank-2 spot is not a candidate
    assert all(it["spot_id"] != "d_05" for it in plan["items"])


def test_the_budget_is_summed_from_the_real_unit_costs():
    plan = prep.rank(_result(_deps()), CAPS)
    assert plan["budget_used_inr"] == 180000 + 420000 + 420000 + 60000
    assert plan["budget_total_inr"] == 50000000
    assert plan["budget_left_inr"] == 50000000 - plan["budget_used_inr"]


def test_a_measure_that_does_not_fit_the_budget_is_skipped_not_forced():
    tight = dict(CAPS, budget_total_inr=200000)     # only the first drain fits
    plan = prep.rank(_result(_deps()), tight)
    assert plan["budget_used_inr"] == 180000
    assert len(plan["items"]) == 1
    assert plan["spots_skipped_for_budget"] >= 1


def test_only_the_worst_few_are_listed():
    many = [_dep(f"d_{i:02d}", 4, i) for i in range(1, 12)]
    plan = prep.rank(_result(many), CAPS)
    assert len(plan["items"]) == prep.MAX_ITEMS
    assert plan["spots_not_listed"] == len(many) - prep.MAX_ITEMS


def test_exposure_is_carried_through_as_the_people_covered():
    plan = prep.rank(_result(_deps(), {"available": True, "people_in_worst_spots": 12000,
                                        "people_in_box": 48000}), CAPS)
    assert plan["covers"]["people_in_worst_spots"] == 12000


def test_no_documented_budget_is_an_honest_not_available():
    out = prep.rank(_result(_deps()), None)
    assert out["available"] is False and "documented" in out["reason"]


def test_the_infeasible_measures_are_passed_through_with_their_reasons():
    plan = prep.rank(_result(_deps()), CAPS)
    assert plan["infeasible"][0]["measure"] == "Raise the road level"
    assert "Capital work" in plan["infeasible"][0]["reason"]


# ---- before / after (F10) -------------------------------------------------------------------

def _analysis(top_filling_ids, people, road_m):
    feats = [{"properties": {"id": i, "pooling_index": 5, "fills_at_rainfall": True}}
             for i in top_filling_ids]
    feats.append({"properties": {"id": "d_dry", "pooling_index": 5, "fills_at_rainfall": False}})
    feats.append({"properties": {"id": "d_low", "pooling_index": 2, "fills_at_rainfall": True}})
    out = {"geojson": {"features": feats},
           "screening": {"routes_affected": [{"road": "X", "length_m": road_m}]}}
    if people is not None:
        out["exposure"] = {"available": True, "people_in_worst_spots": people}
    return out


def test_only_measures_that_act_on_the_water_are_counted_as_reducing_exposure():
    plan = prep.rank(_result(_deps()), CAPS)       # drain, pump, pump, barricade
    assert prep.treated_spot_ids(plan) == ["d_01", "d_02", "d_03"]


def test_before_after_reports_the_difference_between_two_runs():
    plan = prep.rank(_result(_deps()), CAPS)
    before = _analysis(["d_01", "d_02", "d_03", "d_04"], 2400, 4200)
    after = _analysis(["d_04"], 600, 900)
    ba = prep.before_after(before, after, plan)
    assert ba["available"] and ba["best_case"] is True
    assert ba["worst_spots_that_fill"] == {"before": 4, "after": 1}      # dry and low-rank spots ignored
    assert ba["road_km_through_worst_spots"] == {"before": 4.2, "after": 0.9}
    assert ba["people_in_worst_spots"] == {"before": 2400, "after": 600, "share_addressed_pct": 75}
    assert ba["measures_counted"] == 3 and ba["measures_not_counted"] == 1


def test_before_after_says_it_is_a_best_case_and_that_barricades_are_not_counted():
    plan = prep.rank(_result(_deps()), CAPS)
    ba = prep.before_after(_analysis(["d_01"], None, 1000), _analysis([], None, 0), plan)
    text = " ".join(ba["limits"]).lower()
    assert "best case" in text and "barricade" in text and "drain" in text
    assert ba["people_in_worst_spots"] is None          # no population configured: not invented


def test_a_plan_of_only_barricades_has_no_before_and_after():
    caps = dict(CAPS, measures={"barricade_low_stretch": CAPS["measures"]["barricade_low_stretch"]})
    only_barricades = prep.rank(_result(_deps()), caps)
    assert {it["op"] for it in only_barricades["items"]} == {"barricade_low_stretch"}
    ba = prep.before_after(_analysis([], None, 0), _analysis([], None, 0), only_barricades)
    assert ba["available"] is False and "barricade" in ba["reason"]


def test_the_planner_may_quote_the_best_case_figures_because_they_are_in_the_plan(monkeypatch):
    plan = prep.rank(_result(_deps()), CAPS)
    plan["before_after"] = prep.before_after(_analysis(["d_01", "d_02", "d_03", "d_04"], 2400, 4200),
                                             _analysis(["d_04"], 600, 900), plan)
    _stub(monkeypatch, "At best, if every measure fully worked, the worst spots that fill would fall "
                       "from 4 to 1. Preparation only, not a response plan.")
    assert planner.write("Kochi, Kerala", plan) is not None
    _stub(monkeypatch, "At best the worst spots that fill would fall from 4 to 2.")
    assert planner.write("Kochi, Kerala", plan) is None          # 2 is in no fact


# ---- the planner note (model stubbed) -------------------------------------------------------

def _plan():
    return prep.rank(_result(_deps()), CAPS)


def _stub(monkeypatch, text):
    monkeypatch.setattr(planner.llm, "ask", lambda *a, **k: type("R", (), {"text": text}))


def test_a_clean_note_that_only_cites_plan_figures_is_kept(monkeypatch):
    _stub(monkeypatch, "Desilting the outlet drain at Banerji Rd is the highest-value lever; the "
                       "pumps cost more and cover fewer spots. Pre-season preparation against "
                       "modelled pooling, not a live response plan.")
    out = planner.write("Kochi, Kerala", _plan())
    assert out is not None and out["note"].startswith("Desilting")


def test_a_cost_the_plan_does_not_contain_is_refused(monkeypatch):
    _stub(monkeypatch, "Clearing the drain for about 25000 rupees is the best first move.")
    assert planner.write("Kochi, Kerala", _plan()) is None


def test_a_dispatch_word_is_refused_even_with_clean_numbers(monkeypatch):
    _stub(monkeypatch, "Dispatch a team to the outlet drain at Banerji Rd before the season.")
    assert planner.write("Kochi, Kerala", _plan()) is None


def test_a_plan_figure_is_allowed_in_the_note(monkeypatch):
    _stub(monkeypatch, "Within the 50000000 budget line, desilting the outlet drain first is the "
                       "highest-value move. Preparation only, not a response plan.")
    out = planner.write("Kochi, Kerala", _plan())
    assert out is not None


def test_no_model_means_no_note(monkeypatch):
    monkeypatch.setattr(planner.llm, "ask",
                        lambda *a, **k: (_ for _ in ()).throw(LlmUnavailable("no key")))
    assert planner.write("Kochi, Kerala", _plan()) is None
