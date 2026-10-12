"""The road-outlook agent: offline. The model is stubbed - what matters is that the summary is
built from the deterministic roads, and that an invented number is refused (fails closed)."""
from bih_agents import road_outlook
from bih_agents.llm import LlmUnavailable


def result():
    return {"roads_timeline": {"features": [
        {"properties": {"name": "Seaport Airport Rd", "highway": "primary", "length_m": 240,
                        "rain_to_flood_mm": 12.0, "severity": 5}},
        {"properties": {"name": "Pipeline Rd", "highway": "tertiary", "length_m": 120,
                        "rain_to_flood_mm": 60.0, "severity": 4}},
        {"properties": {"name": None, "highway": "residential", "length_m": 80,
                        "rain_to_flood_mm": 3.0, "severity": 2}},
    ]}}


def stub(monkeypatch, text):
    monkeypatch.setattr(road_outlook.llm, "ask",
                        lambda *a, **k: type("R", (), {"text": text}))


def test_the_summary_counts_segments_and_lists_the_worst_through_roads():
    f = road_outlook.summarise(result(), rainfall_mm=100, duration_h=6,
                               region_title="Kochi, Kerala")
    assert f["road_segments_crossing_a_pooling_spot"] == 3
    assert f["worst_through_roads"][0]["road"] == "Seaport Airport Rd"
    assert f["difficult_by_half_the_storm"] == 2       # the 12 and 3 mm roads; the 60 mm one is not


def test_a_clean_qualitative_outlook_is_returned_with_its_sources(monkeypatch):
    stub(monkeypatch, "Arterials through the worst spots are likely to slow first; this is "
                      "rain-pooling and ignores drains and live traffic.")
    f = road_outlook.summarise(result(), rainfall_mm=100, duration_h=6, region_title="Kochi")
    out = road_outlook.write("Kochi", f, [{"title": "t", "domain": "onmanorama.com"}])
    assert out["outlook"].startswith("Arterials")
    assert out["sources"] == ["onmanorama.com"]


def test_an_invented_measurement_is_refused(monkeypatch):
    # "45-minute delays" is a number no finding supports -> retried, still bad -> withheld.
    stub(monkeypatch, "Expect 45-minute delays on the main roads.")
    f = road_outlook.summarise(result(), rainfall_mm=100, duration_h=6, region_title="Kochi")
    assert road_outlook.write("Kochi", f, []) is None


def test_a_figure_from_the_findings_is_allowed(monkeypatch):
    stub(monkeypatch, "At 100 mm the arterials through the worst spots slow; pooling only, "
                      "no drains or live traffic.")
    f = road_outlook.summarise(result(), rainfall_mm=100, duration_h=6, region_title="Kochi")
    out = road_outlook.write("Kochi", f, [])
    assert out is not None and "100 mm" in out["outlook"]


def test_no_model_means_no_outlook(monkeypatch):
    monkeypatch.setattr(road_outlook.llm, "ask",
                        lambda *a, **k: (_ for _ in ()).throw(LlmUnavailable("no key")))
    f = road_outlook.summarise(result(), rainfall_mm=100, duration_h=6, region_title="Kochi")
    assert road_outlook.write("Kochi", f, []) is None
