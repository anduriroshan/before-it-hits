"""The conversation layer: memory, understanding a named place, answering about the product, and a
forecast window that the playback and the engine share. Offline: every model is stubbed, because what
is worth testing is the part that holds when the model is wrong, and that part is deterministic."""
import pytest

from bih_agents import coordinator, guide, narrator, scenario
from bih_agents.llm import LlmUnavailable
from bih_engine import forecast
from test_agents import result_fixture


class Region:
    name = "kochi"
    official_return_periods = []
    manifest = {"title": "Kochi, Kerala"}


BOX = (76.3, 9.99, 76.32, 10.0)


def spec(**over):
    base = {"reading": "r", "mode": "whatif", "rainfall_mm": -1, "rainfall_stated_as": "",
            "duration_h": -1, "return_period_years": -1, "focus": "f", "scope": "area",
            "intent": "analyse", "place": "", "same_rainfall_as_before": False,
            "audience": "officer", "needs_clarification": False, "question_back": ""}
    base.update(over)
    return base


def read_returns(monkeypatch, seen=None, **over):
    def fake(q, **k):
        if seen is not None:
            seen.update(k)
        return type("R", (), {"data": spec(**over), "ms": 1, "model": "fake"})
    monkeypatch.setattr(scenario, "read", fake)


def no_engine(monkeypatch):
    def boom(*a, **k):
        raise AssertionError("the engine must not run for this message")
    monkeypatch.setattr(coordinator, "analyse_area", boom)


# ---- the history is bounded and typed, never trusted ---------------------------------------

def test_history_is_bounded_and_typed():
    raw = [{"q": "x" * 1000, "rainfall_mm": "lots", "headline": 5}, "junk",
           {"q": "ok", "rainfall_mm": 2000}] + [{"q": f"q{i}"} for i in range(10)]
    out = scenario.clean_history(raw)
    assert len(out) == scenario.MAX_HISTORY
    first = scenario.clean_history(raw[:1])[0]
    assert len(first["q"]) <= 240 and first["rainfall_mm"] is None
    assert scenario.clean_history([{"q": "a", "rainfall_mm": 2000}])[0]["rainfall_mm"] is None
    assert scenario.clean_history(None) == []


# ---- settle: reuse of the previous storm is copied by code, never by the model --------------

PREV_WHATIF = [{"q": "what if 100 mm", "area": "Vyttila", "mode": "whatif",
                "rainfall_mm": 100.0, "duration_h": 3.0}]
PREV_FORECAST = [{"q": "forecast", "area": "Vyttila", "mode": "forecast",
                  "rainfall_mm": 0.7, "duration_h": 24.0}]


def settle(raw, q="and the same rainfall for Edappally?", history=None, slider=55.0):
    return scenario.settle(raw, question=q, slider_mm=slider, history=history)


def test_the_same_storm_after_a_whatif_copies_its_rainfall_and_window():
    s = settle(spec(same_rainfall_as_before=True), history=PREV_WHATIF)
    assert s["rainfall_mm"] == 100.0 and s["mode"] == "whatif" and s["duration_h"] == 3.0
    assert s["reused_previous_rainfall"] and "previous answer" in s["rainfall_source"]


def test_the_same_storm_after_a_forecast_means_a_fresh_forecast_not_the_old_number():
    s = settle(spec(same_rainfall_as_before=True), history=PREV_FORECAST)
    assert s["mode"] == "forecast" and s["rainfall_mm"] == 55.0      # the number is not pasted over


def test_reuse_with_nothing_to_reuse_falls_back_to_what_is_on_screen():
    s = settle(spec(same_rainfall_as_before=True), history=[])
    assert s["rainfall_mm"] == 55.0 and not s["reused_previous_rainfall"]


def test_a_figure_in_the_message_beats_reuse():
    s = settle(spec(same_rainfall_as_before=True, rainfall_mm=40, rainfall_stated_as="40 mm"),
               q="same storm but 40 mm", history=PREV_WHATIF)
    assert s["rainfall_mm"] == 40.0 and not s["reused_previous_rainfall"]


def test_the_model_cannot_set_a_rainfall_by_claiming_reuse_of_something_never_said():
    s = settle(spec(same_rainfall_as_before=True, rainfall_mm=900), history=[])
    assert s["rainfall_mm"] == 55.0 and s["ignored_figure"] is True


# ---- a named place has to be in the message --------------------------------------------------

def test_a_place_the_message_names_is_kept():
    s = settle(spec(place="Vyttila"), q="What about Vyttila tonight?")
    assert s["place"] == "Vyttila"


def test_a_place_the_model_took_from_the_history_is_dropped():
    s = settle(spec(place="Vyttila"), q="and the roads?", history=PREV_WHATIF)
    assert s["place"] == ""


def test_place_matching_ignores_case_and_spacing_but_not_substance():
    assert settle(spec(place="vyttila"), q="what about   VYTTILA?")["place"] == "vyttila"
    assert settle(spec(place="Kakkanad"), q="what about Vyttila?")["place"] == ""


def test_odd_characters_never_reach_the_geocoder():
    s = settle(spec(place="Vyttila<script>"), q="what about Vyttila<script>?")
    assert "<" not in s["place"] and ">" not in s["place"]


def test_intent_defaults_to_analyse_and_explain_is_kept():
    assert settle(spec(intent="nonsense"))["intent"] == "analyse"
    assert settle(spec(intent="explain"))["intent"] == "explain"


# ---- routing: what runs, and what must not ---------------------------------------------------

def test_a_question_about_the_product_is_answered_without_running_the_engine(monkeypatch):
    read_returns(monkeypatch, intent="explain")
    no_engine(monkeypatch)
    monkeypatch.setattr(guide, "answer", lambda q, **k: "Pooling rank compares spots within the box.")
    out = coordinator.run(question="what does pooling rank mean?", bbox=None, region=Region())
    assert out["kind"] == "explain" and out["answer_checked"] is True
    assert out["result"] is None and "A8 Guide" in [s["agent"] for s in out["trace"]]


def test_a_guide_that_cannot_give_a_clean_answer_shows_the_fixed_fallback(monkeypatch):
    read_returns(monkeypatch, intent="explain")
    no_engine(monkeypatch)
    monkeypatch.setattr(guide, "answer", lambda q, **k: None)
    out = coordinator.run(question="q", bbox=None, region=Region())
    assert out["answer"] == guide.FALLBACK and out["answer_checked"] is False


def test_a_named_place_is_handed_back_to_the_browser_to_find(monkeypatch):
    read_returns(monkeypatch, place="Vyttila")
    no_engine(monkeypatch)
    out = coordinator.run(question="what about Vyttila?", bbox=BOX, region=Region())
    assert out["kind"] == "place_needed" and out["place"] == "Vyttila"


def test_once_the_place_is_found_the_analysis_runs(monkeypatch):
    read_returns(monkeypatch, place="Vyttila")
    monkeypatch.setattr(coordinator, "analyse_area", lambda *a, **k: result_fixture())
    monkeypatch.setattr(narrator, "draft",
                        lambda *a, **k: (_ for _ in ()).throw(LlmUnavailable("stop here")))
    out = coordinator.run(question="what about Vyttila?", bbox=BOX, region=Region(),
                          place_resolved="Vyttila")
    assert out["kind"] == "analysis" and out["result"] is not None


def test_no_area_and_no_place_asks_for_one(monkeypatch):
    read_returns(monkeypatch)
    no_engine(monkeypatch)
    out = coordinator.run(question="will it flood?", bbox=None, region=Region())
    assert out["kind"] == "needs_area" and "need one first" in out["answer"]


def test_the_selected_area_and_history_reach_the_scenario_agent(monkeypatch):
    seen = {}
    read_returns(monkeypatch, seen)
    monkeypatch.setattr(coordinator, "analyse_area", lambda *a, **k: result_fixture())
    monkeypatch.setattr(narrator, "draft",
                        lambda *a, **k: (_ for _ in ()).throw(LlmUnavailable("stop here")))
    coordinator.run(question="and the roads?", bbox=BOX, region=Region(), area_label="Vyttila",
                    history=PREV_WHATIF)
    assert seen["area_label"] == "Vyttila" and seen["history"][0]["rainfall_mm"] == 100.0
    seen.clear()
    read_returns(monkeypatch, seen)
    no_engine(monkeypatch)
    coordinator.run(question="hello", bbox=None, region=Region(), area_label="Vyttila")
    assert seen["area_label"] is None          # no box, so no "selected area" to refer to


# ---- the forecast window the engine ran on is the window the playback shows -------------------

def forecast_with_storm(storm_hour=30):
    """48 dry hours except a three-hour burst, so the wettest window is not the first one."""
    hours = [{"start": f"2026-10-12T{h % 24:02d}:00:00Z" if h < 24 else f"2026-10-13T{h % 24:02d}:00:00Z",
              "qpf_mm": 3.0 if storm_hour <= h < storm_hour + 3 else 0.0, "probability_pct": 50}
             for h in range(48)]
    return forecast.summarise(hours, 10.0, 76.3)


def run_forecast(monkeypatch, **over):
    read_returns(monkeypatch, mode="forecast", **over)
    monkeypatch.setattr(coordinator.forecast, "for_bbox", lambda bbox, hours=48: forecast_with_storm())
    monkeypatch.setattr(coordinator, "analyse_area", lambda *a, **k: result_fixture())
    monkeypatch.setattr(narrator, "draft",
                        lambda *a, **k: (_ for _ in ()).throw(LlmUnavailable("stop here")))
    return coordinator.run(question="what is coming?", bbox=BOX, region=Region())


def test_the_default_forecast_window_is_the_coming_day_and_the_playback_is_that_window(monkeypatch):
    out = run_forecast(monkeypatch)
    assert out["scenario"]["duration_h"] == 24.0
    assert len(out["rain_profile"]) == 24
    # The engine's rainfall is the playback's final total: one window, not two slices of the forecast.
    assert sum(out["rain_profile"]) == pytest.approx(out["scenario"]["rainfall_mm"], abs=0.11)
    assert out["scenario"]["rainfall_mm"] == pytest.approx(9.0)        # the whole burst is inside it


def test_a_question_that_names_a_window_gets_that_window(monkeypatch):
    out = run_forecast(monkeypatch, duration_h=6)
    assert out["scenario"]["duration_h"] == 6.0 and len(out["rain_profile"]) == 6
    assert sum(out["rain_profile"]) == pytest.approx(out["scenario"]["rainfall_mm"], abs=0.11)


def test_the_playback_starts_where_the_wettest_window_starts(monkeypatch):
    out = run_forecast(monkeypatch, duration_h=3)
    assert out["rain_profile"] == [3.0, 3.0, 3.0]
    assert out["rain_profile_start"] == "2026-10-13T06:00:00Z"          # hour 30 of the series


def test_where_the_forecast_came_from_travels_with_it(monkeypatch):
    out = run_forecast(monkeypatch)
    meta = out["forecast_meta"]
    assert "Weather API" in meta["source"] and meta["window_hours"] == 24 and meta["fetched_at"]


def test_choose_window_takes_the_nearest_available_one():
    peaks = {"1": {}, "3": {}, "6": {}, "12": {}, "24": {}}
    assert coordinator._choose_window(None, peaks) == "24"
    assert coordinator._choose_window(5, peaks) == "6"
    assert coordinator._choose_window(2, peaks) in ("1", "3")
    assert coordinator._choose_window(100, peaks) == "24"
    assert coordinator._choose_window(None, {}) is None
    assert coordinator._choose_window(None, {"3": {}, "6": {}}) == "6"   # no 24 h: the longest there is


# ---- the Guide: its figures must come from the reference or the conversation -------------------

def stub_guide(monkeypatch, text):
    monkeypatch.setattr(guide.llm, "ask", lambda *a, **k: type("R", (), {"text": text}))


def test_a_clean_explanation_is_returned(monkeypatch):
    stub_guide(monkeypatch, "Pooling rank runs from 1 to 5 and compares a spot with the others in the "
                            "same box, so 5 means the most water collects there.")
    assert guide.answer("what is pooling rank?").startswith("Pooling rank")


def test_a_figure_the_reference_does_not_contain_is_refused(monkeypatch):
    stub_guide(monkeypatch, "About 85 percent of spots in a box are rank 5.")
    assert guide.answer("how common is rank 5?") is None


def test_a_figure_from_an_earlier_verified_answer_may_be_repeated(monkeypatch):
    stub_guide(monkeypatch, "In the last answer 44 spots were ranked 4 or 5, meaning they hold the most.")
    hist = [{"q": "forecast", "headline": "Out of 111 spots, 44 are top-ranked."}]
    assert guide.answer("why 44?", history=hist) is not None


def test_no_model_means_no_guide_answer(monkeypatch):
    monkeypatch.setattr(guide.llm, "ask", lambda *a, **k: (_ for _ in ()).throw(LlmUnavailable("no")))
    assert guide.answer("q") is None


def test_the_reference_states_the_limits_the_product_keeps():
    for must in ("No water depth in metres", "No storm-drain data", "not a clock",
                 "never for live dispatch"):
        assert must in guide.REFERENCE, must


def test_the_product_describes_itself_from_the_hazard_list_not_from_rain():
    """The intro must not be about one hazard: it reads whatever the engine can screen today."""
    from bih_agents import hazards
    assert hazards.labels() in guide.REFERENCE
    assert "flood-screening tool" not in guide.REFERENCE
    assert all(h["id"] and h["label"] and h["what"] for h in hazards.live())
    assert "not available yet" in guide.REFERENCE          # a hazard that is not built is never described


# ---- city overview: scope "city" analyses every preset instead of one box ------------------

class RegionWithPresets:
    name = "kochi"
    official_return_periods = []
    bounds = (76.2, 9.85, 76.45, 10.1)
    manifest = {"title": "Kochi, Kerala"}
    presets = [{"label": "A", "bbox": [76.3, 9.99, 76.32, 10.0]},
               {"label": "B", "bbox": [76.31, 9.99, 76.33, 10.0]}]

    def covers(self, bbox):
        return True


class RegionWithHeatStandard(RegionWithPresets):
    """A city whose own weather authority rates heat on something a forecast actually carries."""

    def __init__(self):
        self.manifest = dict(RegionWithPresets.manifest, heat_standard={
            "authority": "the India Meteorological Department",
            "measure": "temperature_c",
            "measure_label": "the highest air temperature forecast",
            "bands": [{"label": "the temperature IMD wants", "min_c": 37}],
            "source": "https://mausam.imd.gov.in/",
            "incomplete": "The departure from normal is unchecked, so no heat wave is ever declared.",
        })


def test_scope_city_runs_city_overview(monkeypatch):
    read_returns(monkeypatch, scope="city", mode="whatif")
    monkeypatch.setattr(coordinator, "analyse_area", lambda *a, **k: result_fixture())
    out = coordinator.run(question="will it flood in Kochi?", bbox=None,
                          region=RegionWithPresets(), slider_mm=50)
    assert out["kind"] == "city_overview"
    assert out.get("presets") is not None
    assert len(out["presets"]) == 2
    assert out["answer_checked"] is True
    assert "Kochi" in (out["answer"] or "")


def test_scope_city_with_forecast(monkeypatch):
    read_returns(monkeypatch, scope="city", mode="forecast", duration_h=24)

    def fake_forecast(bbox, hours=48):
        return type("F", (), {"as_dict": lambda self: {
            "source": "test", "hours_ahead": 48,
            "peaks": {"24": {"mm": 15.0, "starts": "2026-10-13T00:00:00Z"}},
            "hours": [{"t": "2026-10-13T00:00:00Z", "mm": 0.5}] * 24,
        }})()
    monkeypatch.setattr(coordinator.forecast, "for_bbox", fake_forecast)
    monkeypatch.setattr(coordinator, "analyse_area", lambda *a, **k: result_fixture())
    out = coordinator.run(question="what is the expected rainfall?", bbox=None,
                          region=RegionWithPresets(), slider_mm=50)
    assert out["kind"] == "city_overview"
    assert out["scenario"]["rainfall_mm"] == 15.0
    assert out["scenario"]["mode"] == "forecast"
    assert out["rain_profile"] is not None


def test_scope_area_does_not_trigger_city_overview(monkeypatch):
    read_returns(monkeypatch, scope="area", place="Edappally")
    out = coordinator.run(question="what about Edappally?", bbox=None,
                          region=RegionWithPresets(), slider_mm=50)
    assert out["kind"] == "place_needed"


def test_settle_includes_scope():
    s = scenario.settle(spec(scope="city"), question="will it flood?", slider_mm=100)
    assert s["scope"] == "city"
    s2 = scenario.settle(spec(), question="which spots?", slider_mm=100)
    assert s2["scope"] == "area"


def test_city_answer_text():
    from bih_agents.coordinator import _build_city_answer
    s = {"rainfall_mm": 10, "duration_h": 24, "mode": "forecast"}
    presets = [{"label": "A", "spots": 50, "top_ranked": 20, "fills": 5, "road_km": 2.1},
               {"label": "B", "spots": 30, "top_ranked": 10, "fills": 0, "road_km": 0}]
    text = _build_city_answer(s, presets, "Kochi, Kerala")
    assert "10 mm" in text
    assert "A" in text and "B" in text
    assert "would fill" in text


# ---- a whole-city question about one topic is answered from that topic, not from the rain ----

def city_topic(monkeypatch, topic):
    """A whole-city question, with the rain forecast and the terrain engine both forbidden and the
    narrator and checker stubbed to record the findings they were handed."""
    read_returns(monkeypatch, scope="city", topic=topic)
    no_engine(monkeypatch)

    def no_forecast(*a, **k):
        raise AssertionError("a question that is not about rain must not fetch the rain forecast")
    monkeypatch.setattr(coordinator.forecast, "for_bbox", no_forecast)
    seen = {}

    def draft(bundle, **k):
        seen.update(bundle)
        return type("R", (), {"data": {"headline": "h", "bullets": ["b"], "caveat": "c"},
                              "ms": 1, "model": "fake"})
    monkeypatch.setattr(narrator, "draft", draft)
    monkeypatch.setattr(coordinator.verifier, "verify", lambda bundle, briefing: [])
    return seen


@pytest.mark.parametrize("topic", coordinator.TOPIC_ONLY)
def test_a_city_question_about_one_topic_never_becomes_the_rain_overview(monkeypatch, topic):
    city_topic(monkeypatch, topic)
    monkeypatch.setattr(coordinator, "_run_topic", lambda *a, **k: {"kind": "analysis", "called": (a[2], k)})
    out = coordinator.run(question="q", bbox=None, region=RegionWithPresets())
    assert out["called"] == (topic, {"city": True})


def test_a_flood_question_about_the_city_still_gets_the_rain_overview(monkeypatch):
    read_returns(monkeypatch, scope="city", topic="flood", mode="whatif")
    monkeypatch.setattr(coordinator, "analyse_area", lambda *a, **k: result_fixture())
    out = coordinator.run(question="will it flood?", bbox=None, region=RegionWithPresets(), slider_mm=50)
    assert out["kind"] == "city_overview"


def test_who_lives_where_is_compared_across_the_preset_areas_most_people_first(monkeypatch):
    from bih_engine import popgrid
    seen = city_topic(monkeypatch, "population")
    people = {(76.3, 9.99, 76.32, 10.0): 12000, (76.31, 9.99, 76.33, 10.0): 48000}
    monkeypatch.setattr(popgrid, "density_for", lambda region, bbox, cells=None: {
        "available": True, "people_in_area": people[tuple(bbox)], "source": "s", "method": "m"})
    out = coordinator.run(question="most populated area?", bbox=None, region=RegionWithPresets())
    p = seen["people"]
    assert out["kind"] == "analysis" and out["briefing"]["headline"] == "h"
    assert [r["area"] for r in p["by_area_most_people_first"]] == ["B", "A"]
    assert p["area_with_most_people"] == "B" and p["areas_compared"] == 2
    # The model is told the limit, in the findings and not left to chance: only these areas were measured.
    assert "not every neighbourhood" in seen["area"]["what_these_findings_cover"]
    assert "outside them" in p["note"]


def test_an_area_with_no_estimate_is_skipped_not_guessed(monkeypatch):
    from bih_engine import popgrid
    seen = city_topic(monkeypatch, "population")
    monkeypatch.setattr(popgrid, "density_for", lambda region, bbox, cells=None: (
        {"available": True, "people_in_area": 9000, "source": "s", "method": "m"}
        if bbox[0] == 76.3 else {"available": False, "reason": "no buildings"}))
    coordinator.run(question="q", bbox=None, region=RegionWithPresets())
    assert [r["area"] for r in seen["people"]["by_area_most_people_first"]] == ["A"]


def test_no_estimate_for_any_area_means_no_people_claim_at_all(monkeypatch):
    from bih_engine import popgrid
    seen = city_topic(monkeypatch, "population")
    monkeypatch.setattr(popgrid, "density_for", lambda *a, **k: {"available": False, "reason": "none"})
    out = coordinator.run(question="q", bbox=None, region=RegionWithPresets())
    assert "people" not in seen and out["kind"] == "analysis"


def test_city_heat_is_read_at_the_middle_of_the_city_and_says_it_is_the_city(monkeypatch):
    from bih_engine import heat
    seen = city_topic(monkeypatch, "heat")
    at = []

    def fetch(lat, lon, hours=48):
        at.append((lat, lon))
        return type("H", (), {"as_dict": lambda self: {"current": {"heat_index_c": 33},
                                                       "current_level": {"label": "Caution"}}})()
    monkeypatch.setattr(heat, "fetch", fetch)
    monkeypatch.setattr(coordinator.facts, "add_heat", lambda b, d: b.update(heat={"ok": True}))
    coordinator.run(question="how hot is it?", bbox=None, region=RegionWithPresets())
    assert at == [pytest.approx((9.975, 76.325))]
    assert seen["area"] == {"what_these_findings_cover": coordinator.CITY_COVER, "the_city": "Kochi, Kerala"}
    assert "box_km2" not in seen["area"]


# ---- "draw a box on the most populated area": a reference to what an earlier answer named -----

class RegionWithRealNames(RegionWithPresets):
    presets = [{"label": "Geylang / Kallang", "bbox": [76.3, 9.99, 76.32, 10.0]},
               {"label": "Orchard Road", "bbox": [76.31, 9.99, 76.33, 10.0]}]


LABELS = [p["label"] for p in RegionWithRealNames.presets]
SAID_IT = [{"q": "which area is most populated", "area": "Singapore",
            "headline": "Of the four areas compared, Geylang / Kallang has the most people."}]


def earlier(name, history=SAID_IT, labels=LABELS):
    return scenario.settle(spec(earlier_area=name), question="draw a box on the most populated area",
                           slider_mm=50, history=history, preset_labels=labels)["earlier_area"]


def test_an_area_the_earlier_answer_named_is_found_and_spelled_as_the_preset_spells_it():
    assert earlier("geylang / kallang") == "Geylang / Kallang"


def test_the_model_cannot_open_an_area_that_is_not_one_of_the_cities_own():
    assert earlier("Tampines") == ""


def test_a_preset_nobody_mentioned_is_not_opened_on_the_models_say_so():
    assert earlier("Orchard Road") == ""


def test_with_no_conversation_there_is_nothing_to_point_back_at():
    assert earlier("Geylang / Kallang", history=[]) == ""


def test_a_pointed_back_at_area_is_handed_to_the_browser_like_a_named_place(monkeypatch):
    read_returns(monkeypatch, earlier_area="Geylang / Kallang", scope="city")
    no_engine(monkeypatch)
    out = coordinator.run(question="draw a box on the most populated area", bbox=None,
                          region=RegionWithRealNames(), history=SAID_IT, area_label=None)
    assert out["kind"] == "place_needed" and out["place"] == "Geylang / Kallang"
    assert out["scenario"]["scope"] == "area"           # it is about that one area, not the city


def test_once_that_area_is_open_the_analysis_runs_and_does_not_ask_again(monkeypatch):
    read_returns(monkeypatch, earlier_area="Geylang / Kallang", scope="area")
    monkeypatch.setattr(coordinator, "analyse_area", lambda *a, **k: result_fixture())
    monkeypatch.setattr(narrator, "draft",
                        lambda *a, **k: (_ for _ in ()).throw(LlmUnavailable("stop here")))
    out = coordinator.run(question="draw a box on the most populated area", bbox=BOX,
                          region=RegionWithRealNames(), history=SAID_IT,
                          area_label="Geylang / Kallang", place_resolved="Geylang / Kallang")
    assert out["kind"] == "analysis" and out["result"] is not None


def test_the_area_already_selected_is_not_looked_up_again(monkeypatch):
    read_returns(monkeypatch, earlier_area="Geylang / Kallang")
    monkeypatch.setattr(coordinator, "analyse_area", lambda *a, **k: result_fixture())
    monkeypatch.setattr(narrator, "draft",
                        lambda *a, **k: (_ for _ in ()).throw(LlmUnavailable("stop here")))
    out = coordinator.run(question="the most populated area again", bbox=BOX,
                          region=RegionWithRealNames(), history=SAID_IT, area_label="Geylang / Kallang")
    assert out["kind"] == "analysis"


# ---- a temperature the asker named, guarded exactly like a rainfall --------------------------

def heat_spec(**over):
    return spec(**{"heat_c": -1, "heat_stated_as": "", **over})


def test_a_temperature_in_the_askers_own_words_is_kept():
    s = scenario.settle(heat_spec(heat_c=41, heat_stated_as="hits 41"),
                        question="what if it hits 41 here?", slider_mm=50)
    assert s["heat_c"] == 41.0


def test_a_temperature_the_model_invented_is_dropped():
    """The words it claims to quote are not in the message, so the figure cannot be the asker's."""
    s = scenario.settle(heat_spec(heat_c=45, heat_stated_as="reaches 45"),
                        question="what if there is a heat wave here?", slider_mm=50)
    assert s["heat_c"] is None


def test_a_temperature_whose_quoted_words_do_not_carry_it_is_dropped():
    s = scenario.settle(heat_spec(heat_c=45, heat_stated_as="hits 41"),
                        question="what if it hits 41 here?", slider_mm=50)
    assert s["heat_c"] is None


def test_no_temperature_stated_leaves_the_forecast_to_decide():
    s = scenario.settle(heat_spec(), question="how hot will it get?", slider_mm=50)
    assert s["heat_c"] is None


def test_a_rainfall_and_a_temperature_do_not_contaminate_each_other():
    s = scenario.settle(heat_spec(rainfall_mm=120, rainfall_stated_as="120 mm", heat_c=41,
                                  heat_stated_as="hits 41"),
                        question="if 120 mm falls and it hits 41, what happens?", slider_mm=50)
    assert s["rainfall_mm"] == 120.0 and s["heat_c"] == 41.0


# ---- a heat question carries who is exposed, and against whose threshold ----------------------

def test_a_heat_question_carries_the_exposure_block_and_names_the_authority(monkeypatch):
    seen = city_topic(monkeypatch, "heat")
    monkeypatch.setattr(coordinator, "_add_surface_heat", lambda *a, **k: None)
    from bih_engine import heat as heat_mod, popgrid
    monkeypatch.setattr(heat_mod, "fetch", lambda lat, lon, hours=48: type("H", (), {
        "as_dict": lambda self: {"current": {"heat_index_c": 33}, "current_level": {"label": "x"},
                                 "hours": [{"temperature_c": 38, "heat_index_c": 42}]}})())
    monkeypatch.setattr(popgrid, "density_for", lambda region, bbox, cells=None: {
        "available": True, "people_in_area": 73000, "source": "a grid"})
    coordinator.run(question="how many people are exposed to heat here?", bbox=BOX,
                    region=RegionWithHeatStandard())
    ex = seen["heat_exposure"]
    assert ex["whose_threshold_this_is"] == "the India Meteorological Department"
    assert ex["people_living_here"] == 73000
    assert ex["band_the_forecast_reaches"] == "the temperature IMD wants"
    # the model is told, in the findings, that this count is not a count of harm
    assert "never a count of anyone who will fall ill" in ex["note"]


def test_a_city_whose_authority_cannot_be_read_from_a_forecast_says_so_in_the_findings(monkeypatch):
    seen = city_topic(monkeypatch, "heat")
    monkeypatch.setattr(coordinator, "_add_surface_heat", lambda *a, **k: None)
    from bih_engine import heat as heat_mod, popgrid
    monkeypatch.setattr(heat_mod, "fetch", lambda lat, lon, hours=48: type("H", (), {
        "as_dict": lambda self: {"current": {}, "current_level": {},
                                 "hours": [{"temperature_c": 31, "heat_index_c": 35}]}})())
    monkeypatch.setattr(popgrid, "density_for", lambda *a, **k: {"available": False, "reason": "none"})
    region = RegionWithHeatStandard()
    region.manifest["heat_standard"] = {
        "authority": "the National Environment Agency", "measure": "wbgt_c",
        "measure_label": "the wet-bulb globe temperature", "bands": [{"label": "high", "min_c": 33}],
        "source": "s", "incomplete": "WBGT needs solar radiation the forecast does not carry."}
    coordinator.run(question="how hot is it here?", bbox=BOX, region=region)
    ex = seen["heat_exposure"]
    assert ex["whose_threshold_this_is"] != "the National Environment Agency"
    assert "solar radiation" in ex["why_this_authority_and_not_the_local_one"]
    assert ex["people_unavailable"] == "none"


# ---- earthquake: a magnitude the asker named is theirs or it is dropped -----------------------

def quake_spec(**over):
    return spec(**{"magnitude": -1, "magnitude_stated_as": "", "topic": "earthquake", **over})


def test_a_magnitude_in_the_askers_own_words_is_kept():
    s = scenario.settle(quake_spec(magnitude=7, magnitude_stated_as="magnitude 7"),
                        question="what if a magnitude 7 earthquake hits here?", slider_mm=50)
    assert s["magnitude"] == 7.0 and s["topic"] == "earthquake"


def test_a_magnitude_the_model_invented_is_dropped():
    s = scenario.settle(quake_spec(magnitude=7, magnitude_stated_as="magnitude 7"),
                        question="how bad would a big earthquake be here?", slider_mm=50)
    assert s["magnitude"] is None


def test_a_magnitude_outside_any_earthquake_is_dropped_even_when_it_is_in_the_words():
    s = scenario.settle(quake_spec(magnitude=70, magnitude_stated_as="magnitude 70"),
                        question="what about a magnitude 70 earthquake?", slider_mm=50)
    assert s["magnitude"] is None


def test_a_magnitude_is_never_read_as_a_rainfall_or_a_temperature():
    s = scenario.settle(quake_spec(magnitude=7, magnitude_stated_as="magnitude 7"),
                        question="a magnitude 7 earthquake", slider_mm=55.0)
    assert s["rainfall_mm"] == 55.0 and s["heat_c"] is None


# ---- an earthquake question carries the shaking and the limit, and only that -------------------

SEISMIC = {"available": True, "people_in_area": 90000,
           "design_level": {"chance_of_being_exceeded_percent": 10, "within_years": 50,
                            "return_period_years": 475}, "rock_pga_g": {"lowest": 0.041, "highest": 0.041},
           "published_map_steps_here": False,
           "people_by_intensity": [{"intensity": 5, "people": 90000}],
           "intensity_most_people_feel": 5, "intensity_on_firm_rock": 4, "ground_amplifies_by": 1.58,
           "ground": {"vs30_typical": 213, "vs30_most_people_from": 200, "vs30_most_people_to": 230,
                      "classes": [{"class": "D", "described_as": "stiff soil", "share_of_people": 1.0}]},
           "people_with_no_ground_estimate": 0, "people_with_no_hazard_value": 0,
           "scatter_in_intensity": 0.66}


def quake(monkeypatch, scope="area", **over):
    from bih_engine import seismic
    seen = city_topic(monkeypatch, "earthquake")
    monkeypatch.setattr(seismic, "exposure", lambda region, box: dict(SEISMIC, **over))
    read_returns(monkeypatch, scope=scope, topic="earthquake",
                 magnitude=over.pop("_mag", -1), magnitude_stated_as=over.pop("_words", ""))
    return seen


def test_an_earthquake_question_never_runs_the_rain_engine_or_forecast(monkeypatch):
    seen = quake(monkeypatch)
    out = coordinator.run(question="how strong could shaking be here?", bbox=BOX,
                          region=RegionWithPresets(), area_label="Drawn box")
    assert out["kind"] == "analysis" and out["scenario"]["topic"] == "earthquake"
    assert "seismic" in seen and "pooling" not in seen


def test_the_limit_travels_with_the_figures(monkeypatch):
    seen = quake(monkeypatch)
    coordinator.run(question="q", bbox=BOX, region=RegionWithPresets(), area_label="Drawn box")
    s = seen["seismic"]
    assert "damage, injury or death" in s["not_in_the_data"]
    assert "no magnitude can be turned into a casualty count" in s["not_in_the_data"]
    assert "not a forecast" in s["what_this_is"]
    assert s["intensity_most_people_feel"] == {"intensity": "V", "felt_as": "moderate"}
    assert s["intensity_on_firm_rock_for_comparison"]["intensity"] == "IV"


def test_the_prose_about_shaking_is_digit_free_so_it_smuggles_no_figure(monkeypatch):
    """A digit in prose becomes a figure the briefing is allowed to quote. The sources carry years and
    are the one place that is acceptable; everything else that explains must have none."""
    seen = quake(monkeypatch)
    coordinator.run(question="q", bbox=BOX, region=RegionWithPresets(), area_label="Drawn box")
    s = seen["seismic"]
    for key in ("what_this_is", "scatter_note", "the_ground_map_is_coarse", "not_in_the_data"):
        assert not any(c.isdigit() for c in s[key]), key


def test_a_magnitude_is_echoed_with_why_it_does_not_give_shaking(monkeypatch):
    seen = quake(monkeypatch, _mag=7.0, _words="magnitude 7")
    coordinator.run(question="a magnitude 7 earthquake here?", bbox=BOX, region=RegionWithPresets(),
                    area_label="Drawn box")
    s = seen["seismic"]
    assert s["the_magnitude_the_question_gave"] == 7
    assert "depends on how far away" in s["why_that_does_not_give_shaking_here"]


def test_no_magnitude_asked_means_no_magnitude_note(monkeypatch):
    seen = quake(monkeypatch)
    coordinator.run(question="q", bbox=BOX, region=RegionWithPresets(), area_label="Drawn box")
    assert "the_magnitude_the_question_gave" not in seen["seismic"]


def test_a_whole_city_earthquake_question_is_summed_not_read_at_a_point(monkeypatch):
    seen = quake(monkeypatch, scope="city")
    coordinator.run(question="how many people in the city feel shaking?", bbox=None,
                    region=RegionWithPresets())
    assert seen["area"]["what_these_findings_cover"] == coordinator.CITY_SUM_COVER
    assert "summed" in coordinator.CITY_SUM_COVER and "middle of the city" not in coordinator.CITY_SUM_COVER


def test_an_abrupt_seam_in_the_published_map_is_told_to_the_model(monkeypatch):
    seen = quake(monkeypatch, scope="city", published_map_steps_here=True,
                 rock_pga_g={"lowest": 0.019, "highest": 0.042})
    coordinator.run(question="q", bbox=None, region=RegionWithPresets())
    assert "regional models" in seen["seismic"]["the_published_map_jumps_here"]


def test_people_the_ground_map_cannot_place_are_reported_not_hidden(monkeypatch):
    seen = quake(monkeypatch, people_with_no_ground_estimate=290000)
    coordinator.run(question="q", bbox=BOX, region=RegionWithPresets(), area_label="Drawn box")
    assert seen["seismic"]["people_where_the_ground_map_has_no_estimate"] == 290000


def test_a_city_with_nothing_staged_gets_no_seismic_claim_and_says_why(monkeypatch):
    from bih_engine import seismic
    seen = city_topic(monkeypatch, "earthquake")
    monkeypatch.setattr(seismic, "exposure",
                        lambda region, box: {"available": False, "reason": "no seismic data is staged for this city"})
    read_returns(monkeypatch, scope="area", topic="earthquake")
    steps = []
    coordinator.run(question="q", bbox=BOX, region=RegionWithPresets(), area_label="Drawn box",
                    emit=steps.append)
    assert "seismic" not in seen
    assert any(s["agent"] == "Seismic exposure" and s["status"] == "unavailable" for s in steps)


def test_the_product_lists_earthquake_shaking_and_states_the_limit_in_its_own_reference():
    from bih_agents import hazards
    assert "earthquake" in [h["id"] for h in hazards.live()]
    assert "Earthquake shaking" in guide.REFERENCE
    assert "No damage, injury or casualty estimate for an earthquake" in guide.REFERENCE


def test_the_standard_way_to_write_the_design_level_is_traceable_to_the_findings(monkeypatch):
    """The Narrator naturally writes "a 10% chance of being exceeded in 50 years". The digits are the
    dataset's own definition, so they have to be in the findings or every such answer is sent back, which
    the live model did on every run before they were."""
    from bih_agents import facts
    seen = quake(monkeypatch)
    coordinator.run(question="q", bbox=BOX, region=RegionWithPresets(), area_label="Drawn box")
    allowed = facts.allowed_numbers(seen)
    for phrase in ("a 10 percent chance of being exceeded in 50 years", "the 475-year level"):
        assert facts.untraceable_numbers(phrase, allowed) == [], phrase
    assert seen["seismic"]["design_level"]["within_years"] == 50
