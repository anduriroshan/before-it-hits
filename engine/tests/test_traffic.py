"""Traffic: what published studies say rain does to roads, set beside the roads that run through the low spots.
The product holds no traffic data, so the whole point is what it must NOT do: invent a figure from memory, apply
a freeway number to a street, or let rain figures read as a forecast of a jam. The published numbers are pinned to
what the FHWA pages say, so an edit cannot drift them quietly."""
import pytest

from bih_agents import coordinator, facts, narrator, verifier
from bih_engine import traffic
from test_agents import result_fixture
from test_conversation import BOX, read_returns
from test_router import Region

ROADS = [{"road": "Guillemard Road", "highway": "primary", "segments": 4, "length_m": 480},
         {"road": "Kallang-Paya Lebar Expressway", "highway": "trunk", "segments": 2, "length_m": 900},
         {"road": "Lorong 12 Geylang", "highway": "residential", "segments": 1, "length_m": 120},
         {"road": "unnamed service road", "highway": "service", "segments": 1, "length_m": 60}]


# ---- the classes, by the one study that defines them -----------------------------------------------------

@pytest.mark.parametrize("mm_per_hour,want", [(0.0, "below_light"), (0.24, "below_light"), (0.25, "light"),
                                              (3.0, "light"), (6.4, "light"), (6.41, "heavy"), (40.0, "heavy")])
def test_rain_is_classed_by_smith_and_others_definition_by_the_wettest_hour(mm_per_hour, want):
    assert traffic.rain_class(mm_per_hour) == want


def test_the_classes_are_the_studies_millimetre_figures():
    assert traffic.LIGHT_FROM_MM_PER_H == 0.25 and traffic.HEAVY_ABOVE_MM_PER_H == 6.4


def test_the_strength_is_the_wettest_hour_not_the_average_a_dry_stretch_dilutes():
    profile = [0.0, 0.0, 7.0, 0.0, 0.0, 0.0]
    out = traffic.assess(7.0, 6, profile, ROADS)
    assert out["average_mm_per_hour"] == pytest.approx(1.17, abs=0.01)
    assert out["wettest_hour_mm"] == 7.0 and out["rain_class"] == "heavy"


def test_a_whatif_with_a_duration_is_taken_to_fall_evenly_and_says_so():
    out = traffic.assess(100.0, 2, None, ROADS)
    assert out["assumed_even"] is True and out["wettest_hour_mm"] == 50.0 and out["rain_class"] == "heavy"


def test_a_whatif_with_no_duration_has_no_rate_and_is_not_guessed():
    out = traffic.assess(100.0, None, None, ROADS)
    assert out["rain_class"] == "unknown"
    assert out["wettest_hour_mm"] is None and out["average_mm_per_hour"] is None
    assert out["freeway_effect"] is None and out["arterial_effect_when_wet"] is None


# ---- the published figures are the FHWA's, exactly --------------------------------------------------------

def test_the_freeway_figures_are_fhwas_table():
    assert traffic.FREEWAY["light"] == {"average_speed": (3, 13), "free_flow_speed": (2, 13),
                                        "volume": (5, 10), "capacity": (4, 11)}
    assert traffic.FREEWAY["heavy"] == {"average_speed": (3, 16), "free_flow_speed": (6, 17),
                                        "volume": (14, 14), "capacity": (10, 30)}


def test_the_arterial_figure_is_for_wet_pavement_and_only_given_once_it_is_raining_enough_to_class():
    assert traffic.ARTERIAL_WET_PAVEMENT_SPEED == (10, 25)
    assert traffic.assess(3, 6, [0.5] * 6, ROADS)["arterial_effect_when_wet"] == (10, 25)
    assert traffic.assess(0.3, 6, [0.05] * 6, ROADS)["arterial_effect_when_wet"] is None
    assert traffic.assess(10, None, None, ROADS)["arterial_effect_when_wet"] is None


def test_rain_lighter_than_the_lightest_class_has_no_published_figure_to_give():
    out = traffic.assess(0.9, 6, [0.15] * 6, ROADS)
    assert out["rain_class"] == "below_light" and out["freeway_effect"] is None


def test_each_road_gets_the_kind_the_studies_measured_and_a_local_street_gets_none():
    assert [traffic.road_type(h) for h in ("motorway", "trunk_link", "primary", "secondary_link", "tertiary",
                                           "residential", "service", "unclassified", "living_street")] == [
        "freeway", "freeway", "arterial", "arterial", "arterial",
        "local street", "local street", "local street", "local street"]


def test_the_roads_are_reported_with_their_kind_and_metres_and_capped():
    many = [{"road": f"r{i}", "highway": "primary", "length_m": 10 + i} for i in range(20)]
    out = traffic.assess(3, 6, [0.5] * 6, many)
    assert len(out["roads"]) == traffic.MAX_ROADS
    assert out["roads"][0] == {"road": "r0", "class": "primary", "type": "arterial", "metres_through_low_spots": 10}


# ---- the findings carry the limits with the figures ---------------------------------------------------------

def _facts(total=3.0, hours=6, profile=None, roads=ROADS):
    b = {}
    facts.add_traffic(b, traffic.assess(total, hours, profile if profile is not None else [total / hours] * hours, roads))
    return b["traffic"]


def test_the_findings_say_first_that_there_is_no_traffic_data():
    t = _facts()
    assert "Not traffic data" in t["what_this_is"] and "never a prediction" in t["what_this_is"]
    assert "no traffic counts, speeds, incidents or closures" in t["no_traffic_data"]


def test_the_freeway_figure_is_attached_to_freeways_only_and_the_arterial_one_to_arterials_only():
    effects = _facts(3.0, 6, [0.5] * 6)["published_effects"]
    assert [e["applies_to"].split(" ")[0] for e in effects] == ["freeways", "arterial"]
    assert effects[0]["average_speed_falls_by_percent"] == [3, 13] and effects[0]["capacity_falls_by_percent"] == [4, 11]
    assert effects[1]["speed_falls_by_percent"] == [10, 25] and "wet pavement" in effects[1]["for"]
    assert not any("local" in e["applies_to"] for e in effects)


def test_a_single_published_value_stays_single_and_a_range_stays_a_range():
    heavy = _facts(30.0, 3, [10.0, 10.0, 10.0])["published_effects"][0]
    assert heavy["traffic_volume_falls_by_percent"] == 14           # published as one figure
    assert heavy["capacity_falls_by_percent"] == [10, 30]


def test_rain_too_light_for_the_studies_says_none_applies_rather_than_leaving_a_gap_to_fill():
    t = _facts(0.9, 6, [0.15] * 6)
    assert "lighter than the studies measured" in t["published_effects"]
    assert "lighter than the lightest class" in t["the_rain"]["which_class_the_studies_would_call_it"]


def test_an_unknown_rate_tells_the_narrator_to_ask_for_a_duration_not_to_guess_one():
    t = _facts(100.0, None, [])
    assert "ask how long the rain lasts" in t["the_rain"]["which_class_the_studies_would_call_it"].lower()
    assert "average_mm_per_hour" not in t["the_rain"] and "not known" in t["published_effects"]


def test_the_limits_of_the_studies_travel_with_every_use():
    t = _facts()
    text = " ".join(t["limits_of_the_studies"])
    assert "American studies" in text and "No study from Singapore or Kerala" in text
    assert "wet pavement" in text and "range" in text and "No published figure for a local street" in text


def test_rain_slowing_traffic_and_standing_water_are_kept_apart():
    assert "not added together" in _facts()["these_are_two_different_things"]


def test_the_class_definitions_are_numbers_so_a_briefing_may_quote_them():
    d = _facts()["the_rain"]["how_the_studies_define_the_classes"]
    assert d["light_rain_from_mm_per_hour"] == 0.25 and d["heavy_rain_above_mm_per_hour"] == 6.4
    assert "Smith and others" in d["defined_by"]


def test_the_explanatory_prose_carries_no_digit_into_the_findings():
    t = _facts()
    for key in ("what_this_is", "no_traffic_data", "these_are_two_different_things"):
        assert not any(c.isdigit() for c in t[key]), key
    assert not any(c.isdigit() for c in " ".join(t["limits_of_the_studies"]))


# ---- a traffic answer must say there is no traffic data: checked, not requested --------------------------------

def _brief(headline, bullets=(), caveat="Drains are not in the data."):
    return {"headline": headline, "bullets": list(bullets), "caveat": caveat}


def test_a_traffic_answer_that_never_says_there_is_no_traffic_data_is_caught():
    bad = verifier.check_traffic_statements({"traffic": {}}, _brief("Guillemard Road runs through the low spots."))
    assert len(bad) == 1 and "no traffic data" in bad[0]["problem"]


@pytest.mark.parametrize("sentence", ["No traffic data is held.", "Traffic data is not in the findings.",
                                      "This product holds no traffic counts.", "It cannot say what traffic will do."
                                      if False else "There is no data on traffic here."])
def test_every_honest_way_of_saying_it_passes(sentence):
    assert verifier.check_traffic_statements({"traffic": {}}, _brief(sentence)) == []


def test_the_sentence_has_to_be_about_traffic_and_negative():
    """'Roads run through the low spots' and 'no spots fill' are not it."""
    b = {"traffic": {}}
    assert verifier.check_traffic_statements(b, _brief("Roads run through the low spots.", ["No spots fill."]))
    assert verifier.check_traffic_statements(b, _brief("Traffic will be heavy on Guillemard Road."))


def test_other_questions_are_never_held_to_the_traffic_statement():
    assert verifier.check_traffic_statements({"pooling": {}}, _brief("3 spots fill.")) == []


def test_the_traffic_check_runs_as_part_of_verify(monkeypatch):
    monkeypatch.setattr(verifier, "check_claims", lambda *a, **k: [])
    assert any("traffic data" in p["problem"] for p in verifier.verify({"traffic": {}}, _brief("Roads run through spots.")))


# ---- the coordinator: the traffic topic runs the rain pipeline and adds the block ---------------------------------

def _traffic(monkeypatch, **over):
    called = []
    read_returns(monkeypatch, topic="traffic", **over)
    monkeypatch.setattr(coordinator, "analyse_area", lambda *a, **k: result_fixture())
    monkeypatch.setattr(coordinator, "_add_supplementary_hazards", lambda *a, **k: called.append(1))
    monkeypatch.setattr(narrator, "draft", lambda *a, **k: (_ for _ in ()).throw(narrator.llm.LlmUnavailable("stop")))
    return called


def test_a_traffic_question_carries_the_roads_and_the_published_effect(monkeypatch):
    called = _traffic(monkeypatch, mode="whatif", duration_h=2, rainfall_mm=50, rainfall_stated_as="50 mm")
    out = coordinator.run(question="how would 50 mm in 2 hours affect traffic?", bbox=BOX, region=Region())
    t = out["facts"]["traffic"]
    assert t["roads_through_this_areas_low_spots"][0] == {"road": "Seaport Airport Rd", "kind_of_road": "arterial",
                                                          "metres_through_the_low_spots": 240}
    assert t["the_rain"]["which_class_the_studies_would_call_it"] == "heavy rain"
    assert called == []                                           # not a general question: no heat, no air


def test_a_traffic_question_is_not_answered_with_the_heat_index(monkeypatch):
    called = _traffic(monkeypatch)
    coordinator.run(question="how would this affect traffic?", bbox=BOX, region=Region())
    assert called == []


def test_a_whatif_traffic_question_without_a_duration_has_an_unknown_class(monkeypatch):
    _traffic(monkeypatch, mode="whatif", rainfall_mm=80, rainfall_stated_as="80 mm", duration_h=-1)
    out = coordinator.run(question="how would 80 mm affect traffic?", bbox=BOX, region=Region())
    assert "not known" in out["facts"]["traffic"]["the_rain"]["which_class_the_studies_would_call_it"]


def test_other_topics_carry_no_traffic_block(monkeypatch):
    read_returns(monkeypatch, topic="flood")
    monkeypatch.setattr(coordinator, "analyse_area", lambda *a, **k: result_fixture())
    monkeypatch.setattr(narrator, "draft", lambda *a, **k: (_ for _ in ()).throw(narrator.llm.LlmUnavailable("stop")))
    assert "traffic" not in coordinator.run(question="q", bbox=BOX, region=Region())["facts"]


# ---- round 2: only the kinds of road that are actually in the area ------------------------------------------------

from shapely.geometry import LineString  # noqa: E402
from types import SimpleNamespace  # noqa: E402

BOX_SG = (103.860, 1.305, 103.885, 1.325)


def _road(x0, x1, highway, y=1.31):
    return SimpleNamespace(geom=LineString([(x0, y), (x1, y)]), highway=highway)


def test_the_road_mix_measures_kilometres_of_each_kind_inside_the_box():
    mix = traffic.road_mix([_road(103.860, 103.870, "trunk"), _road(103.860, 103.870, "primary", 1.312),
                            _road(103.860, 103.870, "residential", 1.314)], BOX_SG)
    assert mix["freeway"] == pytest.approx(1.1, abs=0.1) and mix["arterial"] == pytest.approx(1.1, abs=0.1)
    assert mix["local street"] == pytest.approx(1.1, abs=0.1)


def test_a_road_that_runs_out_of_the_box_counts_only_the_part_inside_it():
    mix = traffic.road_mix([_road(103.870, 103.900, "primary")], BOX_SG)      # 103.870 to 103.885 is inside
    assert mix["arterial"] == pytest.approx(1.7, abs=0.1)


def test_a_road_wholly_outside_the_box_counts_for_nothing():
    assert traffic.road_mix([_road(104.0, 104.1, "motorway")], BOX_SG)["freeway"] == 0.0


def test_only_the_effects_for_kinds_of_road_that_are_here_are_offered():
    local_only = {"freeway": 0.0, "arterial": 0.0, "local street": 4.0}
    out = traffic.assess(10.0, 3, [3.0, 4.0, 3.0], ROADS, local_only)
    assert out["freeway_effect"] is None and out["arterial_effect_when_wet"] is None
    arterial = traffic.assess(10.0, 3, [3.0, 4.0, 3.0], ROADS, {"freeway": 0.0, "arterial": 2.0, "local street": 1.0})
    assert arterial["freeway_effect"] is None and arterial["arterial_effect_when_wet"] == (10, 25)
    freeway = traffic.assess(10.0, 3, [3.0, 4.0, 3.0], ROADS, {"freeway": 1.5, "arterial": 0.0, "local street": 1.0})
    assert freeway["freeway_effect"] is not None and freeway["arterial_effect_when_wet"] is None


def test_without_a_road_mix_both_effects_are_offered_which_is_the_cautious_default():
    out = traffic.assess(10.0, 3, [3.0, 4.0, 3.0], ROADS, None)
    assert out["freeway_effect"] is not None and out["arterial_effect_when_wet"] == (10, 25)


def test_an_area_of_only_local_streets_says_why_no_figure_is_given():
    b = {}
    facts.add_traffic(b, traffic.assess(10.0, 3, [3.0, 4.0, 3.0], ROADS, {"freeway": 0.0, "arterial": 0.0, "local street": 4.0}))
    t = b["traffic"]
    assert "none of those are in this area, only local streets" in t["published_effects"]
    assert t["roads_in_this_area_by_kind_in_km"] == {"freeways": 0, "arterial_roads": 0, "local_streets": 4}


def test_the_findings_say_how_much_of_each_kind_of_road_is_in_the_area():
    b = {}
    facts.add_traffic(b, traffic.assess(10.0, 3, [3.0, 4.0, 3.0], ROADS, {"freeway": 1.5, "arterial": 2.0, "local street": 9.5}))
    assert b["traffic"]["roads_in_this_area_by_kind_in_km"] == {"freeways": 1.5, "arterial_roads": 2, "local_streets": 9.5}
    assert len(b["traffic"]["published_effects"]) == 2
