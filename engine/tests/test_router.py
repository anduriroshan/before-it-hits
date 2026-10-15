"""The router: what happens to a question the product cannot or will not answer, a time the asker named, and a
unit the asker wrote. Every one of these was found by running 98 realistic questions through the real
pipeline and reading what came back, not by thinking of them. Offline: every model is stubbed, because what is
worth testing is what holds when the model is wrong, and that part is deterministic."""
import pytest

from bih_agents import coordinator, declines, facts, narrator, scenario, verifier
from bih_engine import forecast
from test_agents import result_fixture
from test_conversation import BOX, no_engine, read_returns, spec


class Region:
    name = "kochi"
    official_return_periods = []
    bounds = (76.2, 9.85, 76.45, 10.1)
    manifest = {"title": "Kochi, Kerala", "timezone": "Asia/Kolkata", "preparations": {"budget": 1}}
    presets = [{"label": "A", "bbox": [76.3, 9.99, 76.32, 10.0]}]

    def covers(self, bbox):
        return True


def with_manifest(**manifest):
    r = Region()
    r.manifest = dict(Region.manifest, **manifest)
    return r


def settle(raw, q="q", **kw):
    return scenario.settle(raw, question=q, slider_mm=kw.pop("slider", 55.0), **kw)


def no_forecast(monkeypatch):
    def boom(*a, **k):
        raise AssertionError("this question must not fetch the rain forecast")
    monkeypatch.setattr(coordinator.forecast, "for_bbox", boom)


# ---- units: the model copies the number as written, code converts it --------------------------------

def test_inches_are_converted_by_code_not_dropped():
    s = settle(spec(rainfall_mm=4, rainfall_unit="inches", rainfall_stated_as="4 inches"),
               q="what if 4 inches of rain falls?", slider=30)
    assert s["rainfall_mm"] == pytest.approx(101.6) and s["rainfall_source"] == "stated in the question"
    assert not s["ignored_figure"] and s["rainfall_asked_as"] == "4 inches"


def test_centimetres_and_millimetres():
    cm = settle(spec(rainfall_mm=10, rainfall_unit="cm", rainfall_stated_as="10 cm"), q="what if it rains 10 cm?")
    assert cm["rainfall_mm"] == 100.0
    mm = settle(spec(rainfall_mm=120, rainfall_unit="mm", rainfall_stated_as="120 mm"), q="what if 120 mm falls?")
    assert mm["rainfall_mm"] == 120.0
    plain = settle(spec(rainfall_mm=120, rainfall_stated_as="120 mm"), q="what if 120 mm falls?")
    assert plain["rainfall_mm"] == 120.0                          # no unit named: millimetres, as before


def test_a_model_that_converted_the_figure_itself_is_still_caught():
    """The number the model returns must be in the asker's words. 101.6 is not in "4 inches"."""
    s = settle(spec(rainfall_mm=101.6, rainfall_unit="inches", rainfall_stated_as="4 inches"),
               q="what if 4 inches falls?", slider=30)
    assert s["ignored_figure"] is True and s["rainfall_mm"] == 30.0


def test_fahrenheit_is_converted_to_celsius_by_code():
    s = settle(spec(heat_c=110, heat_unit="fahrenheit", heat_stated_as="110 degrees Fahrenheit"),
               q="what if it hits 110 degrees Fahrenheit?")
    assert s["heat_c"] == pytest.approx(43.333, abs=0.001) and s["heat_asked_as"] == "110 degrees Fahrenheit"
    c = settle(spec(heat_c=41, heat_unit="celsius", heat_stated_as="hits 41"), q="what if it hits 41?")
    assert c["heat_c"] == 41.0


def test_a_rainfall_beyond_anything_the_product_runs_is_flagged_not_run():
    s = settle(spec(rainfall_mm=5000, rainfall_unit="mm", rainfall_stated_as="5000 mm"), q="what if it rains 5000 mm?")
    assert s["out_of_range"] == {"asked": "5000 mm", "limit_mm": 1000.0}
    ok = settle(spec(rainfall_mm=300, rainfall_stated_as="300 mm"), q="what if it rains 300 mm?")
    assert ok["out_of_range"] is None


def test_the_unit_makes_the_range_check_honest_too():
    s = settle(spec(rainfall_mm=200, rainfall_unit="inches", rainfall_stated_as="200 inches"), q="what if 200 inches fall?")
    assert s["out_of_range"] is not None                         # 200 inches is 5080 mm


# ---- the closed lists: the model reads, code checks -----------------------------------------------------

def test_an_unknown_decline_or_window_is_never_trusted():
    s = settle(spec(decline="make_something_up", window_from="last tuesday"))
    assert s["decline"] == "none" and s["window_from"] == "wettest"


def test_a_known_decline_and_window_are_kept():
    s = settle(spec(decline="other_hazard", declined_detail="tsunami", window_from="tomorrow"), q="will there be a tsunami tomorrow?")
    assert s["decline"] == "other_hazard" and s["declined_detail"] == "tsunami" and s["window_from"] == "tomorrow"


def test_the_words_echoed_back_to_the_asker_must_be_the_askers_own():
    s = settle(spec(decline="other_hazard", declined_detail="alien invasion"), q="will there be a tsunami?")
    assert s["declined_detail"] == ""
    odd = settle(spec(decline="other_hazard", declined_detail="tsunami<script>"), q="will there be a tsunami<script>?")
    assert "<" not in odd["declined_detail"]


def test_no_decline_means_no_detail():
    assert settle(spec(decline="none", declined_detail="tsunami"), q="tsunami")["declined_detail"] == ""


# ---- a question with nothing to analyse is answered without the pipeline -----------------------------------

@pytest.mark.parametrize("kind", scenario.DECLINE_ONLY)
def test_a_decline_only_question_is_answered_from_fixed_text_and_runs_nothing(monkeypatch, kind):
    read_returns(monkeypatch, decline=kind, declined_detail="a thing", intent="analyse")
    no_engine(monkeypatch)
    no_forecast(monkeypatch)
    monkeypatch.setattr(narrator, "draft", lambda *a, **k: (_ for _ in ()).throw(AssertionError("no briefing")))
    out = coordinator.run(question="a thing", bbox=BOX, region=Region())
    assert out["kind"] == "declined" and out["answer_checked"] is True
    assert out["answer"] == declines.only(kind, "a thing")


def test_a_hazard_it_does_not_screen_is_never_answered_as_an_all_clear():
    text = declines.only("other_hazard", "a cyclone")
    assert "a cyclone" in text and "all-clear" in text and "no view" in text
    assert "no cyclone" not in text.lower()


def test_the_hazards_it_names_as_screened_come_from_the_one_list():
    from bih_agents import hazards
    assert hazards.labels() in declines.only("other_hazard", "x")


def test_no_decline_text_carries_a_digit_into_the_findings():
    for text in list(declines.ONLY.values()) + list(declines.ROUTED.values()):
        assert not any(c.isdigit() for c in text)
    for p in declines.PARTIAL.values():
        assert not any(c.isdigit() for c in p["why_not"] + p["asked_for"])


# ---- routed elsewhere -----------------------------------------------------------------------------------

def test_a_question_about_the_news_is_handed_to_the_news_agent_with_its_place(monkeypatch):
    read_returns(monkeypatch, decline="news", place="Edappally", focus="what is reported about Edappally")
    no_engine(monkeypatch)
    out = coordinator.run(question="any news about Edappally?", bbox=None, region=Region(), place_resolved="Edappally")
    assert out["kind"] == "handoff"
    assert out["handoff"] == {"tab": "news", "question": "what is reported about Edappally", "place": "Edappally"}


def test_a_question_about_a_past_event_is_handed_to_the_news_too(monkeypatch):
    read_returns(monkeypatch, decline="history")
    out = coordinator.run(question="what happened in the 2018 floods?", bbox=None, region=Region())
    assert out["kind"] == "handoff" and out["handoff"]["tab"] == "news"
    assert "record of past floods" in out["answer"]


def test_a_preparation_question_opens_the_plan_when_the_city_has_a_budget_and_an_area_is_open(monkeypatch):
    read_returns(monkeypatch, decline="preparations")
    out = coordinator.run(question="what should we do to prepare?", bbox=BOX, region=Region())
    assert out["kind"] == "handoff" and out["handoff"]["tab"] == "details"


def test_a_preparation_question_with_no_area_asks_for_one(monkeypatch):
    read_returns(monkeypatch, decline="preparations")
    out = coordinator.run(question="what should we do to prepare?", bbox=None, region=Region())
    assert out["kind"] == "needs_area" and "pick an area first" in out["answer"]


def test_a_city_with_no_documented_budget_says_so_rather_than_inventing_a_plan(monkeypatch):
    read_returns(monkeypatch, decline="preparations")
    r = Region()
    r.manifest = {"title": "Singapore", "timezone": "Asia/Singapore"}
    out = coordinator.run(question="what should we do to prepare?", bbox=BOX, region=r)
    assert out["kind"] == "declined" and "Singapore has none staged" in out["answer"]


def test_a_comparison_is_read_from_the_cities_own_manifests_and_is_never_a_score(monkeypatch):
    read_returns(monkeypatch, decline="comparison")
    sg = type("R", (Region,), {})()
    sg.manifest = {"title": "Singapore", "live_rainfall": {"source": "nea"},
                   "heat_standard": {"observed": {"source": "nea_wbgt"}},
                   "population_grid": {"validation": {"x": 1}}}
    ko = type("R", (Region,), {})()
    ko.manifest = {"title": "Kochi, Kerala", "official": "ksdma", "preparations": {"budget": 1}}
    out = coordinator.run(question="compare Kochi and Singapore", bbox=None, region=sg, all_regions=[ko, sg])
    t = out["answer"]
    assert out["kind"] == "declined"
    assert "Singapore has live rain gauges" in t and "Kochi does not" in t
    assert "Kochi has an official flood-depth layer" in t and "Singapore does not" in t
    assert "not a score" in t and "does not rank cities" in t


def test_comparing_with_one_city_staged_says_there_is_nothing_to_compare():
    assert "nothing to compare" in declines.compare([Region()])


def test_a_figure_past_the_range_is_refused_out_loud_and_nothing_runs(monkeypatch):
    read_returns(monkeypatch, rainfall_mm=5000, rainfall_unit="mm", rainfall_stated_as="5000 mm")
    no_engine(monkeypatch)
    out = coordinator.run(question="what if it rains 5000 mm?", bbox=BOX, region=Region())
    assert out["kind"] == "declined" and "1,000 millimetres" in out["answer"] and "5000 mm" in out["answer"]


# ---- a limit inside a question about an area: say it, then give what the data supports ----------------------

def _analysis(monkeypatch, **over):
    read_returns(monkeypatch, **over)
    monkeypatch.setattr(coordinator, "analyse_area", lambda *a, **k: result_fixture())
    monkeypatch.setattr(coordinator, "_add_supplementary_hazards", lambda *a, **k: None)
    monkeypatch.setattr(narrator, "draft", lambda *a, **k: (_ for _ in ()).throw(narrator.llm.LlmUnavailable("stop")))


@pytest.mark.parametrize("kind", scenario.DECLINE_AND_ANSWER)
def test_a_limit_travels_into_the_findings_so_the_narrator_says_it_first(monkeypatch, kind):
    _analysis(monkeypatch, decline=kind, topic="flood")
    out = coordinator.run(question="q", bbox=BOX, region=Region())
    note = out["facts"]["asked_for_but_not_given"]
    assert note["asked_for"] == declines.PARTIAL[kind]["asked_for"]
    assert note["why_not"] == declines.PARTIAL[kind]["why_not"]
    assert "Never answer the part that was declined" in note["what_to_do"]


def test_a_question_with_no_limit_carries_no_note(monkeypatch):
    _analysis(monkeypatch, decline="none", topic="flood")
    assert "asked_for_but_not_given" not in coordinator.run(question="q", bbox=BOX, region=Region())["facts"]


def test_a_limit_with_no_area_to_give_the_rest_for_says_the_limit_and_asks_for_an_area(monkeypatch):
    read_returns(monkeypatch, decline="single_building", scope="area")
    no_engine(monkeypatch)
    out = coordinator.run(question="will my house flood?", bbox=None, region=Region())
    assert out["kind"] == "declined"
    assert "one house, school or building" in out["answer"] and "Pick an area" in out["answer"]


def test_a_whole_city_question_cannot_carry_a_limit_so_it_says_it_instead(monkeypatch):
    read_returns(monkeypatch, decline="drainage", scope="city")
    no_engine(monkeypatch)
    out = coordinator.run(question="will the drains cope in Kochi?", bbox=None, region=Region())
    assert out["kind"] == "declined" and "storm-drain" in out["answer"]


# ---- a flood question is not answered with the heat index ------------------------------------------------

def test_a_flood_question_does_not_fetch_heat_or_air_quality(monkeypatch):
    called = []
    read_returns(monkeypatch, topic="flood")
    monkeypatch.setattr(coordinator, "analyse_area", lambda *a, **k: result_fixture())
    monkeypatch.setattr(coordinator, "_add_supplementary_hazards", lambda *a, **k: called.append(1))
    monkeypatch.setattr(narrator, "draft", lambda *a, **k: (_ for _ in ()).throw(narrator.llm.LlmUnavailable("stop")))
    coordinator.run(question="q", bbox=BOX, region=Region())
    assert called == []


def test_a_general_question_still_gets_the_other_hazards(monkeypatch):
    called = []
    read_returns(monkeypatch, topic="overview")
    monkeypatch.setattr(coordinator, "analyse_area", lambda *a, **k: result_fixture())
    monkeypatch.setattr(coordinator, "_add_supplementary_hazards", lambda *a, **k: called.append(1))
    monkeypatch.setattr(narrator, "draft", lambda *a, **k: (_ for _ in ()).throw(narrator.llm.LlmUnavailable("stop")))
    coordinator.run(question="q", bbox=BOX, region=Region())
    assert called == [1]


# ---- the window the asker meant reaches the findings -------------------------------------------------------

def _fc(qpf, first_utc_hour=8):
    hs = []
    for i, q in enumerate(qpf):
        t = first_utc_hour + i
        hs.append({"start": f"2026-10-{15 + t // 24:02d}T{t % 24:02d}:00:00Z", "qpf_mm": float(q), "probability_pct": 50})
    return forecast.summarise(hs, 10.0, 76.3)


def _run_window(monkeypatch, qpf, **over):
    read_returns(monkeypatch, mode="forecast", **over)
    monkeypatch.setattr(coordinator.forecast, "for_bbox", lambda bbox, hours=48: _fc(qpf))
    monkeypatch.setattr(coordinator, "analyse_area", lambda *a, **k: result_fixture())
    monkeypatch.setattr(coordinator, "_add_supplementary_hazards", lambda *a, **k: None)
    monkeypatch.setattr(coordinator, "_observed_now", lambda *a, **k: None)
    monkeypatch.setattr(narrator, "draft", lambda *a, **k: (_ for _ in ()).throw(narrator.llm.LlmUnavailable("stop")))
    return coordinator.run(question="q", bbox=BOX, region=Region())


STORM_TOMORROW = [0.1] * 6 + [0.0] * 20 + [9.0] * 6 + [0.0] * 16


def test_the_next_six_hours_are_the_next_six_hours_not_the_wettest_six_in_two_days(monkeypatch):
    out = _run_window(monkeypatch, STORM_TOMORROW, window_from="now", duration_h=6)
    r = out["facts"]["rainfall"]
    assert r["mm"] == pytest.approx(0.6) and r["window_starts_in_hours"] == 0
    assert "starting now" in r["window_is"]


def test_the_wettest_stretch_says_it_may_start_hours_from_now(monkeypatch):
    out = _run_window(monkeypatch, STORM_TOMORROW, window_from="wettest", duration_h=6)
    r = out["facts"]["rainfall"]
    assert r["mm"] == pytest.approx(54.0) and r["window_starts_in_hours"] == 26
    assert "may start hours from now" in r["window_is"]


def test_tomorrow_starts_at_the_next_local_midnight(monkeypatch):
    # 08:00 UTC is 13:30 in Kochi; the first forecast hour is local 13, so midnight is 11 hours away
    out = _run_window(monkeypatch, STORM_TOMORROW, window_from="tomorrow", duration_h=24)
    r = out["facts"]["rainfall"]
    assert r["window_starts_in_hours"] == 11 and "tomorrow" in r["window_is"]


def test_a_time_beyond_the_forecast_falls_back_and_says_so(monkeypatch):
    out = _run_window(monkeypatch, [1.0] * 6, window_from="tomorrow", duration_h=6)
    r = out["facts"]["rainfall"]
    assert r["the_time_asked_about_is_beyond_the_forecast_so_this_is_the_wettest_stretch"] is True


def test_a_window_the_forecast_ends_inside_is_flagged(monkeypatch):
    out = _run_window(monkeypatch, [1.0] * 30, window_from="tomorrow", duration_h=24)
    assert out["facts"]["rainfall"].get("window_is_cut_short_by_the_end_of_the_forecast") is True


def test_a_whatif_with_the_askers_own_units_echoes_what_they_wrote(monkeypatch):
    read_returns(monkeypatch, mode="whatif", rainfall_mm=4, rainfall_unit="inches", rainfall_stated_as="4 inches")
    monkeypatch.setattr(coordinator, "analyse_area", lambda *a, **k: result_fixture())
    monkeypatch.setattr(coordinator, "_add_supplementary_hazards", lambda *a, **k: None)
    monkeypatch.setattr(narrator, "draft", lambda *a, **k: (_ for _ in ()).throw(narrator.llm.LlmUnavailable("stop")))
    out = coordinator.run(question="what if 4 inches of rain falls?", bbox=BOX, region=Region())
    r = out["facts"]["rainfall"]
    assert r["the_asker_wrote"] == "4 inches" and r["mm"] == pytest.approx(101.6)


def test_the_city_headline_says_when_the_rain_starts():
    s = {"rainfall_mm": 3.2, "duration_h": 24.0, "mode": "forecast", "window_anchor": "wettest",
         "window_starts_in_hours": 9}
    assert coordinator.window_phrase(s) == "in the wettest 24-hour stretch of the next two days, starting in about 9 hours"
    assert coordinator.window_phrase(dict(s, window_anchor="now", window_starts_in_hours=0)) == "over the next 24 hours"
    assert coordinator.window_phrase(dict(s, window_anchor="tonight", duration_h=12.0)) == "tonight, over 12 hours"
    assert "starts now" in coordinator.window_phrase(dict(s, window_starts_in_hours=0))


# ---- "the next N hours" is checked in code, not requested politely ------------------------------------------

def _bundle(starts, real=True):
    return {"rainfall": {"is_a_real_forecast": real, "window_starts_in_hours": starts}}


def _brief(text):
    return {"headline": text, "bullets": [], "caveat": "Drains are not in the data."}


def test_calling_a_later_window_the_next_hours_is_caught():
    bad = verifier.check_window_wording(_bundle(9), _brief("About 3 mm is expected over the next 24 hours."))
    assert len(bad) == 1 and "starts in about 9 hours" in bad[0]["problem"]


@pytest.mark.parametrize("text", ["over the next 6 hours", "in the next six hours", "the coming 12 hours",
                                  "within the next 24-hour period", "over the next an hour"])
def test_every_way_of_saying_the_next_hours_is_caught(text):
    assert verifier.check_window_wording(_bundle(5), _brief(f"3 mm {text}."))


def test_the_next_hours_are_fine_when_the_window_does_start_now():
    assert verifier.check_window_wording(_bundle(0), _brief("3 mm over the next 6 hours.")) == []


def test_the_next_two_days_is_not_mistaken_for_the_next_n_hours():
    ok = _brief("The wettest 24-hour stretch of the next two days starts in about 9 hours.")
    assert verifier.check_window_wording(_bundle(9), ok) == []


def test_a_whatif_is_not_held_to_the_forecast_window_check():
    assert verifier.check_window_wording(_bundle(9, real=False), _brief("over the next 6 hours")) == []


def test_the_window_check_runs_as_part_of_verify(monkeypatch):
    monkeypatch.setattr(verifier, "check_claims", lambda *a, **k: [])
    problems = verifier.verify(_bundle(9), _brief("3 mm over the next 24 hours."))
    assert any("starts in about 9 hours" in p["problem"] for p in problems)


# ---- round 2: found by reading what the battery returned -----------------------------------------------------------

def test_a_worst_stretch_is_at_most_a_day_because_a_48_hour_wettest_is_just_the_whole_forecast(monkeypatch):
    out = _run_window(monkeypatch, STORM_TOMORROW, window_from="wettest", duration_h=48)
    assert out["facts"]["rainfall"]["over_hours"] == 24


def test_a_question_shorter_than_the_hourly_forecast_is_answered_with_the_hour_and_says_so(monkeypatch):
    out = _run_window(monkeypatch, STORM_TOMORROW, window_from="now", duration_h=0.5)
    r = out["facts"]["rainfall"]
    assert r["over_hours"] == 1
    assert r["the_forecast_is_hourly_so_this_is_the_whole_next_hour_not_a_shorter_stretch"] is True


def test_the_findings_say_when_it_first_rains_not_just_that_the_window_starts_now(monkeypatch):
    """'When will the rain start?' was answered 'now' because every window picked to start now does."""
    out = _run_window(monkeypatch, [0.0] * 5 + [2.0, 3.0] + [0.0] * 41, window_from="now", duration_h=6)
    r = out["facts"]["rainfall"]
    assert r["first_such_hour_is_in_hours"] == 5 and r["last_such_hour_is_in_hours"] == 6
    assert r["hours_with_at_least_a_tenth_of_a_millimetre"] == 2 and r["forecast_hours_looked_at"] == 48


def test_a_dry_forecast_says_so_rather_than_leaving_the_start_blank(monkeypatch):
    out = _run_window(monkeypatch, [0.0] * 48, window_from="now", duration_h=6)
    r = out["facts"]["rainfall"]
    assert r["hours_with_at_least_a_tenth_of_a_millimetre"] == 0 and r["first_such_hour_is_in_hours"] is None


NO_GAUGE = "this_city_has_no_live_rain_gauge_so_whether_it_is_raining_now_is_not_observed"


def test_a_city_with_no_rain_gauge_says_it_cannot_observe_rain_now(monkeypatch):
    out = _run_window(monkeypatch, [1.0] * 48, window_from="now", duration_h=6)
    assert out["facts"]["rainfall"][NO_GAUGE] is True


def test_a_city_with_a_gauge_does_not_make_that_claim(monkeypatch):
    read_returns(monkeypatch, mode="forecast", window_from="now", duration_h=6)
    monkeypatch.setattr(coordinator.forecast, "for_bbox", lambda bbox, hours=48: _fc([1.0] * 48))
    monkeypatch.setattr(coordinator, "analyse_area", lambda *a, **k: result_fixture())
    monkeypatch.setattr(coordinator, "_add_supplementary_hazards", lambda *a, **k: None)
    monkeypatch.setattr(coordinator, "_observed_now", lambda *a, **k: {"wettest_nearby_mm_per_5min": 0.0})
    monkeypatch.setattr(narrator, "draft", lambda *a, **k: (_ for _ in ()).throw(narrator.llm.LlmUnavailable("stop")))
    r = coordinator.run(question="q", bbox=BOX, region=with_manifest(live_rainfall={"source": "nea"}))["facts"]["rainfall"]
    assert NO_GAUGE not in r
    assert r["falling_now_at_gauges"]["wettest_nearby_mm_per_5min"] == 0.0


SLIDER = "this_is_a_what_if_at_the_rainfall_set_on_the_slider_not_a_forecast"


def test_rain_left_on_the_slider_is_marked_as_a_what_if_not_a_forecast(monkeypatch):
    read_returns(monkeypatch, mode="whatif", topic="flood")
    monkeypatch.setattr(coordinator, "analyse_area", lambda *a, **k: result_fixture())
    monkeypatch.setattr(narrator, "draft", lambda *a, **k: (_ for _ in ()).throw(narrator.llm.LlmUnavailable("stop")))
    assert coordinator.run(question="which roads first?", bbox=BOX, region=Region())["facts"]["rainfall"][SLIDER] is True


def test_a_what_if_the_asker_named_is_not_called_the_slider(monkeypatch):
    read_returns(monkeypatch, mode="whatif", rainfall_mm=150, rainfall_stated_as="150 mm", rainfall_unit="mm")
    monkeypatch.setattr(coordinator, "analyse_area", lambda *a, **k: result_fixture())
    monkeypatch.setattr(narrator, "draft", lambda *a, **k: (_ for _ in ()).throw(narrator.llm.LlmUnavailable("stop")))
    r = coordinator.run(question="what if 150 mm falls?", bbox=BOX, region=Region())["facts"]["rainfall"]
    assert SLIDER not in r


def test_a_probability_is_a_limit_that_is_said_not_a_number_that_is_invented():
    assert "probability" in scenario.DECLINE_AND_ANSWER
    p = declines.PARTIAL["probability"]
    assert "return period" in p["why_not"] and "intensity-duration-frequency" in p["why_not"]


def test_a_comparison_of_values_between_cities_says_why_it_will_not_pick_one():
    other = with_manifest(title="Singapore")
    t = declines.compare([Region(), other])
    assert t.startswith("I answer for one city at a time and I don't rank cities")
    assert "which is hotter, wetter or safer" in t


def test_a_city_wide_traffic_question_asks_for_an_area_instead_of_running_the_rain_overview(monkeypatch):
    read_returns(monkeypatch, topic="traffic", scope="city")
    no_engine(monkeypatch)
    no_forecast(monkeypatch)
    out = coordinator.run(question="which roads in the city will jam?", bbox=None, region=Region())
    assert out["kind"] == "declined" and out["answer"] == declines.TRAFFIC_NEEDS_AN_AREA
    assert "hold no traffic data" in out["answer"]


def test_the_decline_texts_no_longer_echo_a_phrase_into_a_sentence_it_does_not_fit():
    """'how much fell fell yesterday' and 'where MRT or buses is' were the echo going wrong."""
    assert "{what}" not in declines.ONLY["past_rainfall"] and "{what}" not in declines.ONLY["facilities"]
    assert "fell fell" not in declines.only("past_rainfall", "fell yesterday")
    assert "MRT or buses is" not in declines.only("facilities", "MRT or buses")


def test_the_router_prompt_keeps_other_cities_out_of_scope_city_and_orders_out_of_timing():
    assert "a place, never scope" in scenario.SYSTEM
    assert "asks for an order" in scenario.SCHEMA["properties"]["decline"]["description"]
    assert "particular hour of the day" in scenario.SCHEMA["properties"]["decline"]["description"]


# ---- round 3: the moment a question names ----------------------------------------------------------------------

def test_a_clock_time_and_an_offset_are_validated_not_trusted():
    s = settle(spec(at_hour=18, at_day="tomorrow"))
    assert s["starts_at"] == {"hour": 18, "day": "tomorrow"} and s["window_from"] == "later"
    assert settle(spec(in_hours=3))["starts_at"] == {"in_hours": 3.0}
    for bad in (dict(at_hour=24), dict(at_hour=-1), dict(in_hours=0), dict(in_hours=-2), dict(in_hours=48),
                dict(at_hour="six")):
        s = settle(spec(**bad))
        assert s["starts_at"] is None and s["window_from"] == "wettest", bad


def test_an_offset_beats_a_clock_time_when_both_are_given():
    assert settle(spec(at_hour=18, in_hours=2))["starts_at"] == {"in_hours": 2.0}


def test_an_unknown_day_is_read_as_none():
    assert settle(spec(at_hour=9, at_day="next week"))["starts_at"] == {"hour": 9, "day": "none"}


def test_a_moment_beats_the_coarse_anchor_the_model_also_gave():
    assert settle(spec(window_from="tomorrow", in_hours=3))["window_from"] == "later"


def test_rain_at_six_pm_is_the_hour_that_starts_at_six_pm_local_time(monkeypatch):
    # 08:00 UTC is 13:30 in Kochi, so the forecast's first hour is local 13 and 18:00 is 5 hours on
    qpf = [0.0] * 5 + [6.0] + [0.0] * 42
    out = _run_window(monkeypatch, qpf, window_from="later", at_hour=18, at_day="today")
    r = out["facts"]["rainfall"]
    assert r["window_starts_in_hours"] == 5 and r["mm"] == 6.0 and r["over_hours"] == 1
    assert "named" in r["window_is"]


def test_rain_in_three_hours_is_the_hour_three_hours_from_now(monkeypatch):
    qpf = [0.0] * 3 + [2.5] + [0.0] * 44
    out = _run_window(monkeypatch, qpf, window_from="later", in_hours=3)
    r = out["facts"]["rainfall"]
    assert r["window_starts_in_hours"] == 3 and r["mm"] == 2.5


def test_a_time_that_has_already_gone_is_said_to_have_gone_and_the_forecast_from_now_is_given(monkeypatch):
    out = _run_window(monkeypatch, [1.0] * 48, window_from="later", at_hour=9, at_day="today")
    r = out["facts"]["rainfall"]
    assert r["the_time_asked_about_has_already_passed_so_this_is_the_forecast_from_now"] is True
    assert r["window_starts_in_hours"] == 0


def test_a_moment_beyond_the_forecast_falls_back_and_says_so(monkeypatch):
    out = _run_window(monkeypatch, [1.0] * 12, window_from="later", in_hours=30)
    assert out["facts"]["rainfall"]["the_time_asked_about_is_beyond_the_forecast_so_this_is_the_wettest_stretch"] is True


def test_the_phrase_for_a_later_hour_says_how_far_off_it_is():
    s = {"duration_h": 1.0, "window_anchor": "later", "window_starts_in_hours": 5}
    assert coordinator.window_phrase(s) == "in the hour starting in about 5 hours"
    assert coordinator.window_phrase(dict(s, duration_h=3.0)) == "over 3 hours starting in about 5 hours"


def test_calling_a_later_hour_the_next_hour_is_caught():
    assert verifier.check_window_wording(_bundle(5), _brief("2 mm in the next 1 hour."))


# ---- round 4: "hotter tomorrow than today" needs a figure for each day --------------------------------------------

def _heat_hours(first_utc_iso_hour, values):
    from datetime import datetime, timedelta, timezone
    t0 = datetime.fromisoformat(first_utc_iso_hour).replace(tzinfo=timezone.utc)
    return [{"time": (t0 + timedelta(hours=i)).isoformat().replace("+00:00", "Z"), "heat_index_c": v,
             "temperature_c": None if v is None else v - 4} for i, v in enumerate(values)]


def test_each_local_day_gets_its_own_highest_heat_index_in_the_cities_own_zone():
    # 10:00 UTC is 18:00 in Singapore, so the first six hours are today and the next 24 are tomorrow
    values = [30, 31, 33, 32, 31, 30] + [28] * 10 + [38, 36] + [30] * 12
    b = {}
    facts.add_heat(b, {"current": {}, "peak": {}, "hours": _heat_hours("2026-10-15T10:00:00", values)},
                   tz="Asia/Singapore")
    days = b["heat"]["highest_by_day"]
    assert days[0] == {"day": "today", "highest_heat_index_c": 33, "hours_of_forecast_covered": 6}
    assert days[1] == {"day": "tomorrow", "highest_heat_index_c": 38, "hours_of_forecast_covered": 24}


def test_the_day_boundary_is_the_cities_midnight_not_utcs():
    # 17:00 UTC is 01:00 the next day in Singapore: it must count as tomorrow, not today
    b = {}
    facts.add_heat(b, {"current": {}, "peak": {}, "hours": _heat_hours("2026-10-15T15:00:00", [30, 35, 36])},
                   tz="Asia/Singapore")
    names = [d["day"] for d in b["heat"]["highest_by_day"]]
    assert names == ["today", "tomorrow"]
    assert b["heat"]["highest_by_day"][0]["hours_of_forecast_covered"] == 1       # only 23:00 local is still today


def test_an_hour_with_no_reading_is_skipped_not_counted_as_cool():
    hs = _heat_hours("2026-10-15T10:00:00", [30, None, 33])
    hs[1]["temperature_c"] = None
    b = {}
    facts.add_heat(b, {"current": {}, "peak": {}, "hours": hs}, tz="Asia/Singapore")
    assert b["heat"]["highest_by_day"][0] == {"day": "today", "highest_heat_index_c": 33, "hours_of_forecast_covered": 2}


def test_no_hours_gives_no_days_rather_than_failing():
    b = {}
    facts.add_heat(b, {"current": {}, "peak": {}, "hours": []}, tz="Asia/Singapore")
    assert b["heat"]["highest_by_day"] == []


def test_the_days_are_named_so_the_figures_can_be_compared_not_just_quoted():
    b = {}
    facts.add_heat(b, {"current": {}, "peak": {}, "hours": _heat_hours("2026-10-15T00:00:00", [30] * 72)},
                   tz="Asia/Singapore")
    assert [d["day"] for d in b["heat"]["highest_by_day"]] == ["today", "tomorrow", "the day after tomorrow"]


def test_a_traffic_line_is_only_asked_for_when_the_question_is_about_traffic():
    assert "only asks which roads WATER reaches" in narrator.SYSTEM


# ---- a model reply of the wrong shape degrades the turn, it does not end it ---------------------------------------

def _a2_returns(monkeypatch, data):
    monkeypatch.setattr(scenario, "read", lambda q, **k: type("R", (), {"data": data, "ms": 1, "model": "fake"}))


@pytest.mark.parametrize("data", [["not", "an", "object"], "just a string", 42,
                                  {"duration_h": "six", "rainfall_mm": "lots", "heat_c": "hot", "in_hours": "soon"},
                                  {"rainfall_mm": [1, 2], "rainfall_stated_as": {"a": 1}}])
def test_a_reply_of_the_wrong_shape_degrades_to_what_is_on_screen_instead_of_ending_the_turn(monkeypatch, data):
    _a2_returns(monkeypatch, data)
    monkeypatch.setattr(coordinator, "analyse_area", lambda *a, **k: result_fixture())
    monkeypatch.setattr(coordinator, "_add_supplementary_hazards", lambda *a, **k: None)
    monkeypatch.setattr(narrator, "draft", lambda *a, **k: (_ for _ in ()).throw(narrator.llm.LlmUnavailable("stop")))
    steps = []
    out = coordinator.run(question="will it flood?", bbox=BOX, region=Region(), emit=steps.append)
    assert out["kind"] == "analysis" and out["result"] is not None            # it answered, on the slider
    failed = [s for s in steps if s["agent"] == "A2 Scenario" and s["status"] == "failed"]
    assert failed and "unusable reply" in failed[0]["note"]                   # and said why in the trace


def test_a_scenario_agent_that_is_simply_down_still_says_so_as_before(monkeypatch):
    def down(q, **k):
        raise narrator.llm.LlmUnavailable("no key")
    monkeypatch.setattr(scenario, "read", down)
    monkeypatch.setattr(coordinator, "analyse_area", lambda *a, **k: result_fixture())
    monkeypatch.setattr(coordinator, "_add_supplementary_hazards", lambda *a, **k: None)
    monkeypatch.setattr(narrator, "draft", lambda *a, **k: (_ for _ in ()).throw(narrator.llm.LlmUnavailable("stop")))
    steps = []
    coordinator.run(question="q", bbox=BOX, region=Region(), emit=steps.append)
    assert any(s["agent"] == "A2 Scenario" and s["status"] == "failed" and s["note"] == "no key" for s in steps)
