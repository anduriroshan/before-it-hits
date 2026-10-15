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
    assert b["pooling"]["spots_ranked_4_or_5"] == 2        # index 5 and 4
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


def test_the_voice_leads_the_prompt_and_its_examples_carry_no_figure_a_model_could_copy():
    assert narrator.SYSTEM.startswith("HOW YOU SOUND")
    # the only digit allowed is the "5" of "rank <rank> of 5", which is the printed scale and is in every bundle
    assert set(c for c in narrator.VOICE if c.isdigit()) <= {"5"}


# --- quoting somebody else's data ----------------------------------------------------------

def official_bundle(inferred=True):
    return {"official": {"whose": "KSDMA", "units": "cm", "units_are_inferred": inferred,
                         "units_note": "cm (inferred from magnitude; not stated at source)",
                         "their_deepest_value_in_box": 67.0,
                         "their_median_value_where_wet": 51.0}}


def test_an_inferred_unit_quoted_as_if_it_were_stated_is_caught():
    briefing = {"headline": "The authority's layer covers part of this box.",
                "bullets": ["KSDMA's layer shows a deepest value of 67.0 cm here."],
                "caveat": "Screening only; drains are not in the data."}
    found = verifier.check_official_units(official_bundle(), briefing)
    assert [f["part"] for f in found] == ["bullet-1"]
    assert "inferred" in found[0]["problem"]


def test_saying_the_unit_is_inferred_anywhere_in_the_briefing_is_enough():
    briefing = {"headline": "The authority's layer covers part of this box.",
                "bullets": ["KSDMA's layer shows a deepest value of 67.0 cm here.",
                            "Their units are inferred from magnitude, not stated at source."],
                "caveat": "Screening only; drains are not in the data."}
    assert verifier.check_official_units(official_bundle(), briefing) == []


def test_a_unit_the_authority_did_state_needs_no_hedge():
    briefing = {"headline": "h", "bullets": ["A deepest value of 67.0 cm."], "caveat": "drains"}
    assert verifier.check_official_units(official_bundle(inferred=False), briefing) == []


def test_a_briefing_that_quotes_no_official_figure_is_left_alone():
    briefing = {"headline": "Three spots take the water.", "bullets": ["Two fill first."],
                "caveat": "drains"}
    assert verifier.check_official_units(official_bundle(), briefing) == []


def test_a_spot_carries_the_road_it_sits_on_into_the_facts():
    r = result_fixture()
    r["screening"]["depressions"][0]["nearest_road"] = {
        "name": "Pipeline Road", "highway": "tertiary", "distance_m": 40}
    b = facts.build(r, question="q", rainfall={"mm": 50}, region_title="Kochi, Kerala")
    assert b["pooling"]["worst_spots"][0]["nearest_named_road"] == "Pipeline Road, about 40 m away"
    assert b["pooling"]["worst_spots"][1]["nearest_named_road"] is None


def test_a_whole_number_is_written_whole_not_as_a_float():
    b = facts.build(result_fixture(), question="q",
                    rainfall={"mm": 100.0, "over_hours": 1.0, "where": "slider"},
                    region_title="Kochi, Kerala")
    assert b["rainfall"]["mm"] == 100 and b["rainfall"]["over_hours"] == 1
    assert b["area"]["analysis_grid_m"] == 10        # not 10.0
    assert b["area"]["box_km2"] == 7.1               # a real fraction keeps its decimals


def test_a_heat_level_is_credited_to_its_scale_and_not_to_the_forecast_provider():
    """The readings are the provider's; the band they fall in belongs to the National Weather
    Service. A briefing that credits the band to the provider reports an American scale as though
    the weather service had issued it, which the live model did before the block said so."""
    from bih_agents import facts
    bundle = {}
    facts.add_heat(bundle, {"current": {"heat_index_c": 33}, "current_level": {"label": "Caution"},
                            "peak": {"heat_index_c": 38}, "peak_level": {"label": "Extreme caution"},
                            "source": "Google Maps Platform Weather API (hourly forecast)"})
    assert "National Weather Service" in bundle["heat"]["whose_scale_the_level_is_on"]
    assert "Google" in bundle["heat"]["source"]
    assert "Google" not in bundle["heat"]["whose_scale_the_level_is_on"]


# ---- earthquake: what the briefing must say is checked, not hoped for -------------------------

def _seismic_bundle(jump=False):
    b = {"topic": "earthquake", "seismic": {"not_in_the_data": "x"}}
    if jump:
        b["seismic"]["the_published_map_jumps_here"] = "the map is joined from separate regional models"
    return b


def _briefing(caveat, bullets=("Roughly people stand on intensity V.",)):
    return {"headline": "Design-level shaking.", "bullets": list(bullets), "caveat": caveat}


def test_an_earthquake_caveat_must_say_no_building_data_is_held():
    from bih_agents import verifier
    bad = verifier.check_seismic_statements(_seismic_bundle(), _briefing("This is a design level."))
    assert [p["part"] for p in bad] == ["caveat"] and "building" in bad[0]["problem"]
    ok = verifier.check_seismic_statements(
        _seismic_bundle(), _briefing("No building data is held, so damage is not estimated."))
    assert ok == []


def test_a_seam_in_the_published_map_must_be_said_somewhere_in_the_briefing():
    from bih_agents import verifier
    caveat = "No building data is held, so damage is not estimated."
    silent = verifier.check_seismic_statements(_seismic_bundle(jump=True), _briefing(caveat))
    assert len(silent) == 1 and "separate regional models are joined" in silent[0]["problem"]
    said = verifier.check_seismic_statements(
        _seismic_bundle(jump=True),
        _briefing(caveat, bullets=("The published map changes abruptly across the north of the city.",)))
    assert said == []


def test_the_seam_check_does_not_fire_where_the_map_does_not_jump():
    from bih_agents import verifier
    caveat = "No building data is held, so damage is not estimated."
    assert verifier.check_seismic_statements(_seismic_bundle(jump=False), _briefing(caveat)) == []


def test_other_topics_are_never_held_to_the_earthquake_statements():
    from bih_agents import verifier
    assert verifier.check_seismic_statements({"topic": "heat", "heat": {}}, _briefing("A point forecast.")) == []


def test_the_seismic_checks_run_as_part_of_verify(monkeypatch):
    from bih_agents import verifier
    monkeypatch.setattr(verifier, "check_claims", lambda *a, **k: [])
    problems = verifier.verify(_seismic_bundle(), _briefing("This is a design level."))
    assert any("no building data" in p["problem"] for p in problems)


def test_a_ground_class_that_is_almost_everyone_is_not_rounded_up_to_everyone():
    from bih_agents import facts
    ex = {"available": True, "people_in_area": 5900000, "rock_pga_g": {"lowest": 0.02, "highest": 0.04},
          "design_level": {"chance_of_being_exceeded_percent": 10, "within_years": 50,
                           "return_period_years": 475},
          "people_by_intensity": [{"intensity": 5, "people": 5000000}], "intensity_most_people_feel": 5,
          "intensity_on_firm_rock": 4, "ground_amplifies_by": 1.47, "scatter_in_intensity": 0.66,
          "ground": {"vs30_typical": 261, "vs30_most_people_from": 218, "vs30_most_people_to": 312,
                     "classes": [{"class": "D", "described_as": "stiff soil", "share_of_people": 0.996},
                                 {"class": "C", "described_as": "very dense soil or soft rock",
                                  "share_of_people": 0.004}]}}
    bundle = {}
    facts.add_seismic(bundle, ex)
    pct = {c["class"]: c["percent"] for c in bundle["seismic"]["the_ground"]["share_of_people_by_class"]}
    assert pct == {"D": 99.6, "C": 0.4}


# --- a road the question names is looked up in full, not in the short list ---------------------------------------

def _road_rows(*rows):
    """roads_timeline rows as the engine writes them: (name, highway, rain to fill in mm, pooling rank, metres)."""
    return {"type": "FeatureCollection", "features": [
        {"type": "Feature", "geometry": None,
         "properties": {"name": n, "highway": h, "length_m": m, "rain_to_flood_mm": mm, "severity": s}}
        for n, h, mm, s, m in rows]}


def _long_list_of_roads():
    """Seven roads through spots that fill, longest first, so the bundle's five-road list cuts the last two."""
    return [{"road": f"Long Rd {i}", "highway": "primary", "segments": 2, "length_m": 900 - 100 * i}
            for i in range(5)] + [{"road": "Bendemeer Road", "highway": "primary", "segments": 1, "length_m": 72},
                                  {"road": "Kitchener Road", "highway": "primary", "segments": 1, "length_m": 22}]


def _asked(question, rows, mm=2.8, routes=None):
    r = result_fixture(roads_timeline=_road_rows(*rows))
    if routes is not None:
        r["screening"]["routes_affected"] = routes
    return facts.build(r, question=question, region_title="Singapore",
                       rainfall={"mm": mm, "over_hours": 24, "where_this_figure_came_from": "forecast",
                                 "is_a_real_forecast": True})


FILLS = "a_real_length_of_it_is_in_low_spots_that_fill_at_this_rainfall"
LEAST = "least_rain_that_fills_low_spots_along_a_real_length_of_it_mm"
SOONER = "shorter_bits_of_it_may_fill_with_less_rain_but_the_terrain_data_cannot_resolve_them"


def test_a_road_cut_from_the_short_list_is_still_found_and_the_list_is_marked_short():
    b = _asked("Will Bendemeer Road flood?", [("Bendemeer Road", "primary", 1.7, 5, 100), ("Bendemeer Road", "primary", 12.7, 4, 50)],
               routes=_long_list_of_roads())
    assert "Bendemeer Road" not in [r["road"] for r in b["roads_through_the_worst_spots"]]    # the cap cuts it...
    assert "worst_spots_and_roads_are_short_lists" in b                                       # ...and the bundle says so
    [named] = b["roads_the_question_names"]                                                  # ...but it is not lost
    assert named[FILLS] is True and named[LEAST] == 1.7 and SOONER not in named
    assert named["highest_pooling_rank_of_the_low_spots_on_it"] == 5 and named["a_real_length_is_at_least_metres"] == 90


@pytest.mark.parametrize("rows, expect", [
    # Bendemeer Road in five of the eight box positions tried: a 55-70 m pit that fills first, then 160-190 m at 11-13 mm
    ([(1.5, 55), (11.2, 162), (114.1, 149)], 11.2), ([(2.2, 63), (13.2, 189), (48.5, 68)], 13.2),
    # and in the others the pit is simply not there
    ([(11.7, 21), (12.6, 174), (87.2, 76)], 12.6), ([(14.6, 174), (41.6, 40)], 14.6)])
def test_a_pit_too_short_to_resolve_does_not_set_the_roads_threshold(rows, expect):
    b = _asked("will bendemeer rd flood tonight?", [("Bendemeer Road", "primary", mm, 4, m) for mm, m in rows], routes=[])
    [named] = b["roads_the_question_names"]                                          # "rd" is read as "road"
    assert named[FILLS] is False and named[LEAST] == expect
    assert expect in facts.allowed_numbers(b)                                        # so the briefing may quote it


def test_the_shorter_bit_that_fills_sooner_is_flagged_not_dropped():
    [named] = _asked("Will Bendemeer Road flood?", [("Bendemeer Road", "primary", 1.5, 5, 55),
                                                     ("Bendemeer Road", "primary", 11.2, 4, 162)])["roads_the_question_names"]
    assert named[SOONER] is True


def test_a_road_only_short_bits_of_which_cross_low_spots_gets_no_threshold():
    [named] = _asked("Is Orchard Road at risk?", [("Orchard Road", "primary", 2.0, 5, 30), ("Orchard Road", "primary", 3.0, 4, 20)]
                     )["roads_the_question_names"]
    assert named[FILLS] is False and LEAST not in named and named[SOONER] is True


def test_a_question_that_names_no_road_here_gets_no_road_block_and_a_complete_list_is_not_called_short():
    b = _asked("Which roads will be affected first?", [("Bendemeer Road", "primary", 9.6, 4, 200)])
    assert "roads_the_question_names" not in b and "worst_spots_and_roads_are_short_lists" not in b


def test_the_longer_road_name_wins_and_does_not_also_match_the_shorter_one():
    b = _asked("Is Kim Keat Road East flooding?", [("Kim Keat Road", "primary", 3.0, 4, 200), ("Kim Keat Road East", "primary", 8.0, 5, 200)])
    assert [r["road"] for r in b["roads_the_question_names"]] == ["Kim Keat Road East"]


def test_the_verifier_rejects_calling_a_road_clear_when_a_real_length_of_it_fills():
    b = _asked("Will Bendemeer Road flood?", [("Bendemeer Road", "primary", 1.7, 5, 120)], routes=_long_list_of_roads())
    bad = {"headline": "Bendemeer Road is not among the roads running through the worst low spots.", "bullets": []}
    [p] = verifier.check_named_roads(b, bad)
    assert p["part"] == "headline" and "real length" in p["problem"]
    good = {"headline": "Low spots on Bendemeer Road fill at about 1.7 mm, below the 2.8 mm forecast.", "bullets": []}
    assert verifier.check_named_roads(b, good) == []


def test_the_verifier_lets_a_road_that_needs_more_rain_be_called_unaffected_at_this_rainfall():
    b = _asked("Will Bendemeer Road flood?", [("Bendemeer Road", "primary", 9.6, 4, 200)], routes=[])
    ok = {"headline": "Bendemeer Road is not among the roads through low spots that fill at 2.8 mm; they fill at 9.6 mm.",
          "bullets": []}
    assert verifier.check_named_roads(b, ok) == []


# --- "which roads are hit first" is answered with roads in the order their low ground fills ---------------------

ORDER = "roads_in_the_order_their_low_ground_fills"


def test_which_roads_are_hit_first_gets_the_roads_in_the_order_of_the_rain_they_need():
    rows = [("Road A", "primary", 12.0, 4, 200), ("Road B", "primary", 3.0, 5, 120), ("Road C", "primary", 1.5, 5, 40),
            ("Road D", "secondary", 50.0, 3, 300), ("Road D", "secondary", 90.0, 3, 100)]
    b = _asked("Which roads are hit first?", rows, mm=5.0)
    assert [r["road"] for r in b[ORDER]] == ["Road B", "Road A", "Road D"]       # C is under a real length, so it is not ranked
    assert [r[FILLS] for r in b[ORDER]] == [True, False, False]                 # only B fills at the 5 mm asked about
    assert b[ORDER][0][LEAST] == 3.0 and "roads_the_question_names" not in b


def test_a_roads_question_that_is_not_about_order_does_not_get_the_ordered_list():
    rows = [("Road B", "primary", 3.0, 5, 120)]
    assert ORDER not in _asked("Which roads would the rain reach?", rows)            # the box question: roads reached at this rain
    assert ORDER not in _asked("Which spots fill first?", rows)                      # first, but not about roads


def test_a_road_named_in_the_question_wins_over_the_ordered_list():
    b = _asked("Is Road B the first road to flood?", [("Road B", "primary", 3.0, 5, 120), ("Road A", "primary", 12.0, 4, 200)])
    assert [r["road"] for r in b["roads_the_question_names"]] == ["Road B"] and ORDER not in b


def test_the_verifier_rejects_odds_in_words_but_not_a_published_chance_or_a_refusal_to_give_one():
    odds = {"headline": "Yes, water is likely to collect on Bendemeer Road.", "bullets": ["Flooding is unlikely tonight."], "caveat": "c"}
    assert [p["part"] for p in verifier.check_odds({}, odds)] == ["headline"]           # first one found; the revise loop fixes the rest
    assert verifier.check_odds({}, {"headline": "Probably fine.", "bullets": [], "caveat": ""})
    ok = {"headline": "The design level has a 10 percent chance of being exceeded in 50 years.",
          "bullets": ["I can't give you a likelihood for flooding; the findings show 4 low spots fill at this rain."], "caveat": "c"}
    assert verifier.check_odds({}, ok) == []
