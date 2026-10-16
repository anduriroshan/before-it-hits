"""Offline tests for the Watcher and the Pub/Sub alert publisher. Engine and Pub/Sub are mocked: the point is
the threshold logic (when is an alert raised at all) and that a missing alerting backend degrades, never raises."""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "service"))
import alerts  # noqa: E402
import watcher  # noqa: E402


class FakeRegion:
    def __init__(self, name, presets):
        self.name = name
        self.presets = presets


class FakeForecast:
    def as_dict(self):
        return {"hours": [], "peaks": {}}


def _result(ranks_that_fill):
    """A fake analyse_area result: one geojson feature per (pooling_index, fills) pair."""
    feats = [{"properties": {"id": f"d{i}", "pooling_index": r, "fills_at_rainfall": fills}}
             for i, (r, fills) in enumerate(ranks_that_fill)]
    return {"geojson": {"features": feats},
            "screening": {"routes_affected": [{"road": "Pipeline Road", "highway": "primary", "length_m": 120}]},
            "exposure": {"available": True, "people_in_worst_spots": 110}}


def _patch(monkeypatch, window, result):
    monkeypatch.setattr(watcher.forecast, "for_bbox", lambda *a, **k: FakeForecast())
    monkeypatch.setattr(watcher.forecast, "pick_window", lambda *a, **k: window)
    monkeypatch.setattr(watcher, "analyse_area", lambda *a, **k: result)


WINDOW = {"mm": 35.0, "starts": "2026-10-16T00:00Z", "starts_in_hours": 2, "hours": 24, "anchor": "wettest"}


def test_an_alert_is_raised_when_a_top_ranked_spot_fills(monkeypatch):
    _patch(monkeypatch, WINDOW, _result([(5, True), (5, True), (2, True)]))
    a = watcher.evaluate_area(FakeRegion("kochi", []), "Edappally", [76.3, 10.0, 76.33, 10.03])
    assert a is not None
    assert a["area"] == "Edappally" and a["forecast_mm"] == 35.0
    assert a["top_spots_that_fill"] == 2 and a["spots_that_fill"] == 3
    assert a["roughly_people_in_worst_spots"] == 110 and a["severity"] == "watch"
    assert "not an evacuation" in a["note"].lower() and "water depth" in a["note"].lower()   # a preparation trigger, not dispatch
    assert not any("depth" in str(k) for k in a)                                 # no depth figure of ours as a field


def test_no_alert_when_only_low_ranked_or_dry_spots(monkeypatch):
    _patch(monkeypatch, WINDOW, _result([(3, True), (5, False), (2, True)]))   # nothing rank>=4 that fills
    assert watcher.evaluate_area(FakeRegion("kochi", []), "Edappally", [76.3, 10.0, 76.33, 10.03]) is None


def test_many_top_spots_make_it_a_high_watch(monkeypatch):
    _patch(monkeypatch, WINDOW, _result([(5, True)] * 12))
    a = watcher.evaluate_area(FakeRegion("kochi", []), "Edappally", [76.3, 10.0, 76.33, 10.03])
    assert a["severity"] == "high"


def test_no_forecast_means_no_alert(monkeypatch):
    def down(*a, **k):
        raise watcher.forecast.ForecastUnavailable("no weather")
    monkeypatch.setattr(watcher.forecast, "for_bbox", down)
    assert watcher.evaluate_area(FakeRegion("kochi", []), "Edappally", [76.3, 10.0, 76.33, 10.03]) is None


def test_the_sweep_covers_presets_and_survives_a_bad_area(monkeypatch):
    calls = {"n": 0}

    def flaky(region, label, bbox):
        calls["n"] += 1
        if label == "boom":
            raise RuntimeError("bad area")
        return {"area": label} if label == "ok" else None
    monkeypatch.setattr(watcher, "evaluate_area", flaky)
    regions = [FakeRegion("kochi", [{"label": "ok", "bbox": [0, 0, 1, 1]},
                                    {"label": "boom", "bbox": [0, 0, 1, 1]},
                                    {"label": "quiet", "bbox": [0, 0, 1, 1]}])]
    out = watcher.evaluate(regions)
    assert calls["n"] == 3 and [a["area"] for a in out] == ["ok"]


# ---- the Pub/Sub publisher: degrades, never raises -----------------------------------------------

def test_publishing_nothing_is_a_clean_no_op():
    out = alerts.publish([])
    assert out["published"] == 0 and "no alerts" in out["status"]


def test_alerting_can_be_switched_off(monkeypatch):
    monkeypatch.setattr(alerts, "OFF", True)
    assert alerts.publish([{"region": "kochi"}])["status"].startswith("alerting disabled")
    assert alerts.recent()["status"].startswith("alerting disabled")


def test_a_pubsub_outage_is_reported_not_raised(monkeypatch):
    monkeypatch.setattr(alerts, "OFF", False)

    def boom():
        raise RuntimeError("no creds")
    monkeypatch.setattr(alerts, "_publisher_or_raise", boom)
    out = alerts.publish([{"region": "kochi", "severity": "watch"}])
    assert out["published"] == 0 and "unavailable" in out["status"]


def test_publish_sends_one_message_per_alert(monkeypatch):
    monkeypatch.setattr(alerts, "OFF", False)
    sent = []

    class FakeFuture:
        def result(self, timeout=None):
            return "mid"

    class FakePub:
        def topic_path(self, proj, topic):
            return f"projects/{proj}/topics/{topic}"

        def publish(self, path, data, **attrs):
            sent.append((path, data, attrs))
            return FakeFuture()
    monkeypatch.setattr(alerts, "_publisher_or_raise", lambda: FakePub())
    out = alerts.publish([{"region": "kochi", "severity": "high"}, {"region": "singapore", "severity": "watch"}])
    assert out["published"] == 2 and out["status"] == "ok"
    assert sent[0][2] == {"region": "kochi", "severity": "high"}       # attributes travel for filtering
