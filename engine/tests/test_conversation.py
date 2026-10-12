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
            "duration_h": -1, "return_period_years": -1, "focus": "f", "intent": "analyse",
            "place": "", "same_rainfall_as_before": False, "audience": "officer",
            "needs_clarification": False, "question_back": ""}
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
