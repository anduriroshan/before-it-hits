"""Offline tests for the agent layer. No network and no model: what is worth testing here is the
part that holds when the model is wrong, and that part is deterministic by design."""
import pytest

from bih_agents import coordinator, facts, narrator, scenario, verifier
from bih_agents.llm import LlmUnavailable


def result_fixture(**over):
    """A small but complete engine result, shaped exactly as analyse_area returns one."""
    deps = [
        {"id": "d_01", "pooling_index": 5, "contributing_ha": 4.2, "fill_order": 2,
         "nearest_road": None},
        {"id": "d_02", "pooling_index": 4, "contributing_ha": 1.8, "fill_order": 1,
         "nearest_road": None},
        {"id": "d_03", "pooling_index": 2, "contributing_ha": 0.6, "fill_order": 3,
         "nearest_road": None},
    ]
    feats = [
        {"type": "Feature", "geometry": None,
         "properties": {"id": d["id"], "pooling_index": d["pooling_index"],
                        "fill_order": d["fill_order"], "fills_at_rainfall": d["pooling_index"] > 2,
                        "low_confidence": False, "contributing_ha": d["contributing_ha"]}}
        for d in deps
    ]
    res = {
        "screening": {
            "depressions": deps,
            "excluded": [{"id": "d_09", "reason": "possible water body"}],
            "routes_affected": [{"road": "Seaport Airport Rd", "highway": "primary",
                                 "segments": 3, "length_m": 240}],
            "confidence": {"terrain": "weak", "drains": "gap"},
            "caveat": "30 m terrain screening.",
        },
        "geojson": {"type": "FeatureCollection", "features": feats},
        "grid": {"shape": [700, 700], "res_m": 10.0, "area_km2": 7.1},
        "timings_s": {},
        "region": {"name": "kochi", "title": "Kochi, Kerala", "official": [10, 25, 50]},
    }
    res.update(over)
    return res


def bundle_fixture(**kw):
    return facts.build(result_fixture(**kw.pop("result", {})),
                       question=kw.pop("question", "Which parts collect water first?"),
                       rainfall=kw.pop("rainfall", {"mm": 100.0, "over_hours": None,
                                                    "where_this_figure_came_from": "on screen",
                                                    "is_a_real_forecast": False}),
                       region_title="Kochi, Kerala")


# --- the facts bundle ---------------------------------------------------------------------

def test_a_depth_of_ours_can_never_reach_a_prompt():
    with pytest.raises(AssertionError, match="depth"):
        facts.assert_no_own_depth({"pooling": {"worst_spots": [{"max_depth_m": 0.8}]}})


def test_the_authoritys_depth_is_allowed_because_it_is_theirs():
    facts.assert_no_own_depth({"official": {"their_deepest_value_in_box": 180.0}})


def test_the_bundle_carries_the_counts_a_briefing_needs():
    b = bundle_fixture()
    assert b["pooling"]["spots_found"] == 3
    assert b["pooling"]["top_ranked_spots"] == 2        # index 5 and 4
    assert b["pooling"]["spots_that_fill_at_this_rainfall"] == 2
    assert b["pooling"]["spots_left_out"] == 1
    assert b["area"]["box_km2"] == 7.1


def test_a_city_with_no_published_layer_says_so_rather_than_going_quiet():
    r = result_fixture()
    r["region"]["official"] = None
    b = facts.build(r, question="q", rainfall={"mm": 50}, region_title="Singapore")
    assert b["official"]["whose"] is None and "normal case" in b["official"]["note"]


# --- the arithmetic check -----------------------------------------------------------------

def test_a_figure_that_is_in_the_findings_passes():
    allowed = {4.2, 7.1, 100.0}
    assert facts.untraceable_numbers("4.2 ha drains into it at 100 mm", allowed) == []


def test_a_figure_that_is_not_in_the_findings_is_caught():
    assert facts.untraceable_numbers("about 850 households", {4.2, 7.1}) == [850.0]


def test_ordinary_rounding_is_allowed_but_not_drifting():
    allowed = {2.78}
    assert facts.untraceable_numbers("2.8 times better", allowed) == []
    assert facts.untraceable_numbers("nearly 3 times better", allowed) == []
    assert facts.untraceable_numbers("4 times better", allowed) == [4.0]


def test_a_share_may_be_written_as_a_percentage():
    assert facts.untraceable_numbers("21.4% of the box", {0.214}) == []
    assert facts.untraceable_numbers("21% of the box", {0.214}) == []
    assert facts.untraceable_numbers("31% of the box", {0.214}) == [31.0]


def test_a_whole_number_is_not_quietly_multiplied_into_a_percentage():
    # 0.5 may be written as 50%, but 50 must not license 5000.
    assert facts.untraceable_numbers("5000 people", {50.0}) == [5000.0]


def test_flags_do_not_become_figures():
    # True is 1 in Python; a confidence flag must not license writing "1".
    assert facts.allowed_numbers({"discriminating": True, "ok": False}) == set()


def test_the_verifier_names_the_part_that_cites_an_invented_figure():
    bundle = bundle_fixture()
    briefing = {"headline": "Two spots matter here.",
                "bullets": ["4.2 ha drains into the worst one.",
                            "Roughly 850 homes sit in them."],
                "caveat": "Terrain screening only; drains are not in the data."}
    found = verifier.check_numbers(briefing, facts.allowed_numbers(bundle))
    assert [f["part"] for f in found] == ["bullet-2"]
    assert "850" in found[0]["problem"]


# --- what gets published ------------------------------------------------------------------

def test_a_failed_bullet_is_removed_and_the_rest_survive():
    briefing = {"headline": "h", "bullets": ["a", "b", "c"], "caveat": "drains"}
    kept, removed = verifier.enforce(briefing, [{"part": "bullet-2", "problem": "x"}])
    assert kept["bullets"] == ["a", "c"] and len(removed) == 1


def test_a_failed_headline_takes_the_whole_briefing_with_it():
    briefing = {"headline": "h", "bullets": ["a"], "caveat": "drains"}
    kept, _ = verifier.enforce(briefing, [{"part": "headline", "problem": "x"}])
    assert kept is None


def test_a_briefing_with_nothing_left_is_withheld_rather_than_shown_empty():
    briefing = {"headline": "h", "bullets": ["a"], "caveat": "drains"}
    kept, _ = verifier.enforce(briefing, [{"part": "bullet-1", "problem": "x"}])
    assert kept is None


def test_a_caveat_that_forgets_the_drains_is_a_problem():
    assert verifier.check_caveat({"caveat": "A 10 m terrain screening."})
    assert not verifier.check_caveat({"caveat": "Screening only; storm drains are not in the data."})
    assert verifier.check_caveat({"caveat": ""})


# --- the scenario guard -------------------------------------------------------------------

def spec(**kw):
    base = {"reading": "r", "focus": "f", "audience": "officer", "mode": "whatif",
            "rainfall_mm": -1, "rainfall_stated_as": "", "duration_h": -1,
            "return_period_years": -1, "needs_clarification": False, "question_back": ""}
    base.update(kw)
    return base


def test_a_rainfall_figure_in_the_askers_own_words_is_used():
    s = scenario.settle(spec(rainfall_mm=120, rainfall_stated_as="120 mm", duration_h=1),
                        question="What if 120 mm falls in an hour?", slider_mm=100)
    assert s["rainfall_mm"] == 120.0 and s["rainfall_source"] == "stated in the question"
    assert s["ignored_figure"] is False


def test_a_rainfall_figure_the_model_invented_is_dropped():
    s = scenario.settle(spec(rainfall_mm=150, rainfall_stated_as="heavy rain"),
                        question="What if there is heavy rain?", slider_mm=100)
    assert s["rainfall_mm"] == 100.0           # the slider, which the user can see
    assert s["ignored_figure"] is True


def test_a_quote_that_is_not_in_the_question_cannot_launder_a_figure():
    s = scenario.settle(spec(rainfall_mm=200, rainfall_stated_as="200 mm"),
                        question="What about a really bad storm?", slider_mm=80)
    assert s["rainfall_mm"] == 80.0 and s["ignored_figure"] is True


def test_a_return_period_nobody_publishes_is_not_requested():
    s = scenario.settle(spec(return_period_years=75), question="q", slider_mm=100,
                        published_return_periods=[10, 25, 50])
    assert s["return_period_years"] is None
    s = scenario.settle(spec(return_period_years=25), question="q", slider_mm=100,
                        published_return_periods=[10, 25, 50])
    assert s["return_period_years"] == 25


def test_forecast_mode_takes_its_figure_from_the_forecast_not_the_model():
    s = scenario.settle(spec(mode="forecast"), question="What is coming tonight?",
                        slider_mm=100, forecast_peak_mm=38.0)
    assert s["rainfall_mm"] == 38.0 and "forecast" in s["rainfall_source"]


# --- the pipeline, with the models stubbed out ---------------------------------------------

class FakeRegion:
    name = "kochi"
    official_return_periods = [10, 25, 50]
    manifest = {"title": "Kochi, Kerala"}


def wire(monkeypatch, *, narrate, verify_problems=None, verify_raises=False):
    """Replace both models and the engine, so only the Coordinator's own logic is under test."""
    monkeypatch.setattr(coordinator, "analyse_area",
                        lambda *a, **k: dict(result_fixture(), debug=[{"max_depth_m": 1.2}]))
    monkeypatch.setattr(scenario, "read",
                        lambda q, **k: type("R", (), {"data": spec(), "ms": 1, "model": "fake"}))
    calls = {"narrate": 0, "verify": 0}

    def fake_draft(bundle, **k):
        calls["narrate"] += 1
        return type("R", (), {"data": narrate[min(calls["narrate"] - 1, len(narrate) - 1)],
                              "ms": 1, "model": "fake"})

    def fake_verify(bundle, briefing, **k):
        calls["verify"] += 1
        if verify_raises:
            raise LlmUnavailable("model unreachable")
        return (verify_problems or [])[:] if calls["verify"] <= len(verify_problems or []) else []

    monkeypatch.setattr(narrator, "draft", fake_draft)
    monkeypatch.setattr(narrator, "revise", lambda b, **k: fake_draft(b))
    monkeypatch.setattr(coordinator.verifier, "verify", fake_verify)
    return calls


CLEAN = {"headline": "Two spots take the water here.",
         "bullets": ["4.2 ha drains into the worst one."],
         "caveat": "Terrain screening; drains are not in the data."}


def test_a_clean_run_publishes_and_leaves_a_trace(monkeypatch):
    wire(monkeypatch, narrate=[CLEAN])
    out = coordinator.run(question="Where does water collect?", bbox=(76.3, 9.99, 76.32, 10.0),
                          region=FakeRegion())
    assert out["briefing"]["headline"] == CLEAN["headline"]
    assert out["withheld"] is None
    agents = [s["agent"] for s in out["trace"]]
    assert "A4 Engine (no model)" in agents and "A7 Verifier" in agents


def test_internal_depths_never_leave_the_coordinator(monkeypatch):
    wire(monkeypatch, narrate=[CLEAN])
    out = coordinator.run(question="q", bbox=(76.3, 9.99, 76.32, 10.0), region=FakeRegion())
    assert "debug" not in out["result"]


def test_a_verifier_that_cannot_be_reached_withholds_the_briefing(monkeypatch):
    wire(monkeypatch, narrate=[CLEAN], verify_raises=True)
    out = coordinator.run(question="q", bbox=(76.3, 9.99, 76.32, 10.0), region=FakeRegion())
    assert out["briefing"] is None
    assert "nothing has checked" in out["withheld"]
    assert out["result"]["screening"]["depressions"]          # the screening still answers


def test_a_narrator_that_cannot_be_reached_costs_the_briefing_and_nothing_else(monkeypatch):
    wire(monkeypatch, narrate=[CLEAN])
    monkeypatch.setattr(narrator, "draft",
                        lambda *a, **k: (_ for _ in ()).throw(LlmUnavailable("down")))
    out = coordinator.run(question="q", bbox=(76.3, 9.99, 76.32, 10.0), region=FakeRegion())
    assert out["briefing"] is None and "down" in out["withheld"]
    assert len(out["result"]["screening"]["depressions"]) == 3


def test_a_rejected_claim_is_sent_back_before_it_is_removed(monkeypatch):
    problems = [{"part": "bullet-1", "problem": "cites 850", "rule": "unsupported"}]
    calls = wire(monkeypatch, narrate=[CLEAN, CLEAN], verify_problems=problems)
    coordinator.run(question="q", bbox=(76.3, 9.99, 76.32, 10.0), region=FakeRegion())
    assert calls["narrate"] >= 2                 # it was sent back, not silently trimmed


def test_the_scenario_agent_failing_falls_back_to_the_controls_on_screen(monkeypatch):
    wire(monkeypatch, narrate=[CLEAN])
    monkeypatch.setattr(scenario, "read",
                        lambda q, **k: (_ for _ in ()).throw(LlmUnavailable("no key")))
    out = coordinator.run(question="q", bbox=(76.3, 9.99, 76.32, 10.0), region=FakeRegion(),
                          slider_mm=65.0)
    assert out["scenario"]["rainfall_mm"] == 65.0
    assert any(s["agent"] == "A2 Scenario" and s["status"] == "failed" for s in out["trace"])


def test_the_narrator_contract_drops_empties_and_caps_the_bullets():
    b = narrator.clean({"headline": " h ", "bullets": ["a", "", "  ", "b", "c", "d", "e", "f"],
                        "caveat": "c"})
    assert b["headline"] == "h" and len(b["bullets"]) == narrator.MAX_BULLETS
