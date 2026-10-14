"""Wind: which way it blows, offline, with every model and the weather API stubbed.

The one way this goes badly wrong is a 180 degree mistake. The provider names the direction the wind
comes FROM, an arrow on a map must show where the air is GOING, and a briefing that says "from the
north" when the air is heading north is worse than no briefing. So the tests pin both words for the same
reading, and check the bundle carries no digit in its prose.
"""
import re

import pytest

from bih_agents import coordinator, facts, hazards, narrator, scenario
from bih_engine import heat as heat_mod


def _hours(n=30, speed=12.0, deg=225, gust=20.0):
    return [{"time": f"t{i}", "wind_speed_kmh": speed + (5 if i == 9 else 0), "wind_gust_kmh": gust,
             "wind_dir_deg": deg if i < 12 else 90} for i in range(n)]


def _bundle(**kw):
    out = {}
    facts.add_wind(out, {"hours": _hours(**kw), "source": "a forecast"})
    return out


def test_the_compass_names_eight_points_in_plain_words():
    assert [facts.compass(d) for d in (0, 45, 90, 135, 180, 225, 270, 315)] == [
        "north", "north-east", "east", "south-east", "south", "south-west", "west", "north-west"]
    assert facts.compass(350) == "north" and facts.compass(359.9) == "north"
    assert facts.compass(225 + 180) == "north-east"                      # the air's heading, not its source


def test_a_wind_from_the_south_west_is_heading_north_east_and_both_are_said():
    now = _bundle()["wind"]["now"]
    assert now["blowing_from"] == "south-west" and now["blowing_towards"] == "north-east"
    assert now["speed_kmh"] == 12 and now["gusts_to_kmh"] == 20


def test_the_bundle_says_how_it_changes_later_and_when_it_is_strongest():
    w = _bundle()["wind"]
    assert [r["hours_from_now"] for r in w["later"]] == [6, 12, 24]
    assert w["later"][1]["blowing_from"] == "east"                       # it has swung round by hour twelve
    assert w["strongest_in_the_forecast"]["hours_from_now"] == 9 and w["strongest_in_the_forecast"]["speed_kmh"] == 17


def test_a_short_forecast_offers_only_the_hours_it_has():
    assert [r["hours_from_now"] for r in _bundle(n=8)["wind"]["later"]] == [6]


def test_the_prose_has_no_digit_so_it_adds_no_figure_a_briefing_could_quote():
    w = _bundle()["wind"]
    for key in ("direction_note", "note"):
        assert not re.search(r"\d", w[key]), key


def test_every_figure_a_briefing_may_quote_traces_to_the_reading():
    allowed = facts.allowed_numbers(_bundle())
    for quoted in (12, 20, 17, 6, 12, 24):
        assert facts.traces_to_facts(quoted, allowed), quoted
    assert not facts.traces_to_facts(55, allowed)                       # a gust nobody supplied
    facts.assert_no_own_depth(_bundle())


def test_no_wind_in_the_forecast_adds_nothing_and_a_missing_direction_is_not_invented():
    empty = {}
    facts.add_wind(empty, {"hours": [{"time": "t", "wind_speed_kmh": None}]})
    assert empty == {}
    calm = {}
    facts.add_wind(calm, {"hours": [{"time": "t", "wind_speed_kmh": 4.0}]})
    assert "blowing_from" not in calm["wind"]["now"] and calm["wind"]["now"]["speed_kmh"] == 4


class _Forecast:
    def __init__(self, hours):
        self._h = hours

    def as_dict(self):
        return {"hours": self._h, "source": "a forecast"}


def test_the_coordinator_fetches_the_wind_at_the_middle_of_the_box_and_traces_it(monkeypatch):
    seen = {}
    monkeypatch.setattr(heat_mod, "fetch", lambda lat, lon, hours=48, **k: seen.update(at=(lat, lon)) or _Forecast(_hours()))
    bundle, trace = {}, coordinator.Trace()
    coordinator._add_wind(bundle, 10.0, 76.3, trace)
    assert seen["at"] == (10.0, 76.3) and "wind" in bundle
    step = trace.steps[-1]
    assert step["agent"] == "Wind" and step["status"] == "ok" and "south-west" in step["note"]


def test_a_weather_service_that_fails_is_traced_not_raised(monkeypatch):
    def boom(*a, **k):
        raise heat_mod.HeatUnavailable("weather service unreachable")
    monkeypatch.setattr(heat_mod, "fetch", boom)
    bundle, trace = {}, coordinator.Trace()
    coordinator._add_wind(bundle, 10.0, 76.3, trace)
    assert bundle == {} and trace.steps[-1]["status"] == "unavailable"


class _Region:
    name = "kochi"
    bounds = (76.2, 9.85, 76.45, 10.1)
    official_return_periods = []
    manifest = {"title": "Kochi, Kerala"}


def test_a_wind_question_skips_the_flood_engine_and_answers_from_the_wind(monkeypatch):
    spec = {"reading": "wind", "mode": "whatif", "rainfall_mm": -1, "rainfall_stated_as": "", "duration_h": -1,
            "return_period_years": -1, "focus": "which way is the wind blowing here", "scope": "area",
            "intent": "analyse", "topic": "wind", "place": "", "same_rainfall_as_before": False,
            "audience": "officer", "needs_clarification": False, "question_back": ""}
    monkeypatch.setattr(scenario, "read", lambda q, **k: type("R", (), {"data": spec, "ms": 1, "model": "f"}))
    monkeypatch.setattr(heat_mod, "fetch", lambda *a, **k: _Forecast(_hours()))
    monkeypatch.setattr(coordinator, "analyse_area", lambda *a, **k: pytest.fail("the flood engine must not run"))
    seen = {}

    def draft(bundle, **k):
        seen.update(bundle)
        return type("R", (), {"data": {"headline": "The wind is from the south-west.", "bullets": [],
                                       "caveat": "A point forecast."}, "ms": 1, "model": "f"})
    monkeypatch.setattr(narrator, "draft", draft)
    monkeypatch.setattr(coordinator.verifier, "verify", lambda *a, **k: [])
    out = coordinator.run(question="which way is the wind blowing here", bbox=(76.3, 9.99, 76.32, 10.0),
                          region=_Region())
    assert out["scenario"]["topic"] == "wind" and out["briefing"]["headline"].startswith("The wind")
    assert set(seen) == {"question", "topic", "area", "limits", "wind"}      # nothing about flooding in it
    agents = [s["agent"] for s in out["trace"]]
    assert "Wind" in agents and "A4 Engine (no model)" not in agents


def test_the_scenario_agent_may_route_to_wind_and_nothing_it_invents_is_accepted():
    assert "wind" in scenario.SCHEMA["properties"]["topic"]["enum"]
    base = {"mode": "whatif", "rainfall_mm": -1, "rainfall_stated_as": "", "duration_h": -1,
            "return_period_years": -1, "focus": "x", "scope": "area", "intent": "analyse", "place": "",
            "same_rainfall_as_before": False, "audience": "officer"}
    settle = lambda topic: scenario.settle(dict(base, topic=topic), question="q", slider_mm=100)["topic"]   # noqa: E731
    assert settle("wind") == "wind" and settle("tornado") == "overview"


def test_every_hazard_offers_a_question_after_a_box_is_drawn_and_wind_is_among_them():
    live = hazards.live()
    assert "wind" in [h["id"] for h in live]
    for h in live:
        assert h["option"]["label"] and h["option"]["q"].rstrip().endswith("?"), h["id"]
