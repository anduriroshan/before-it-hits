"""The news cache: nothing user-facing waits on the news index. Offline - the index is stubbed with
fakes that are fast, slow or failing, because what matters here is the caching behaviour."""
import threading
import time

import pytest

from bih_agents import news, road_outlook


@pytest.fixture(autouse=True)
def clean_cache(monkeypatch):
    news._state.clear()
    yield
    news._state.clear()


def fake_region_news(calls, *, delay=0.0, fail=None, gate=None):
    def fake(title, query=None, **kw):
        calls.append(title)
        if gate is not None:
            gate.wait(5)
        if delay:
            time.sleep(delay)
        if fail:
            raise news.NewsUnavailable(fail)
        return {"articles": [{"title": f"{title} floods", "domain": "x.com"}], "digest": None, "note": "n"}
    return fake


def test_the_first_request_fetches_and_the_second_is_served_from_cache(monkeypatch):
    calls = []
    monkeypatch.setattr(news, "for_region", fake_region_news(calls))
    a = news.get("kochi", "Kochi, Kerala")
    b = news.get("kochi", "Kochi, Kerala")
    assert a["status"] == b["status"] == "ok" and len(calls) == 1
    assert b["stale"] is False and b["age_s"] >= 0


def test_two_cities_are_cached_separately(monkeypatch):
    calls = []
    monkeypatch.setattr(news, "for_region", fake_region_news(calls))
    news.get("kochi", "Kochi, Kerala")
    news.get("singapore", "Singapore")
    assert sorted(calls) == ["Kochi, Kerala", "Singapore"]


def test_a_stale_answer_is_served_at_once_and_refreshed_behind_the_request(monkeypatch):
    calls = []
    monkeypatch.setattr(news, "for_region", fake_region_news(calls))
    news.get("kochi", "Kochi, Kerala")
    monkeypatch.setattr(news, "FRESH_S", -1)            # everything cached is now stale
    t0 = time.monotonic()
    gate = threading.Event()
    monkeypatch.setattr(news, "for_region", fake_region_news(calls, gate=gate))   # the refresh is slow
    got = news.get("kochi", "Kochi, Kerala")
    assert got["status"] == "ok" and got["stale"] is True
    assert time.monotonic() - t0 < 1.0                   # did not wait for the slow refresh
    gate.set()


def test_a_slow_index_gives_pending_not_a_hang(monkeypatch):
    gate = threading.Event()
    monkeypatch.setattr(news, "for_region", fake_region_news([], gate=gate))
    got = news.get("kochi", "Kochi, Kerala", wait_s=0.2)
    assert got["status"] == "pending"
    gate.set()
    time.sleep(0.3)
    assert news.get("kochi", "Kochi, Kerala", wait_s=0.2)["status"] == "ok"      # ready on the next ask


def test_one_fetch_serves_everyone_who_asks_while_it_runs(monkeypatch):
    calls = []
    gate = threading.Event()
    monkeypatch.setattr(news, "for_region", fake_region_news(calls, gate=gate))
    news.get("kochi", "Kochi, Kerala", wait_s=0.1)
    news.get("kochi", "Kochi, Kerala", wait_s=0.1)
    news.get("kochi", "Kochi, Kerala", wait_s=0.1)
    gate.set()
    time.sleep(0.3)
    assert len(calls) == 1


def test_a_failure_is_reported_and_then_not_hammered(monkeypatch):
    calls = []
    monkeypatch.setattr(news, "for_region", fake_region_news(calls, fail="news index returned 429"))
    first = news.get("kochi", "Kochi, Kerala")
    assert first["status"] == "unavailable" and "429" in first["error"]
    again = news.get("kochi", "Kochi, Kerala")
    assert again["status"] == "unavailable" and len(calls) == 1      # inside the cooling-off period
    monkeypatch.setattr(news, "RETRY_AFTER_FAIL_S", -1)
    news.get("kochi", "Kochi, Kerala")
    assert len(calls) == 2                                           # ...and it tries again afterwards


def test_a_stale_answer_survives_a_failed_refresh(monkeypatch):
    calls = []
    monkeypatch.setattr(news, "for_region", fake_region_news(calls))
    news.get("kochi", "Kochi, Kerala")
    monkeypatch.setattr(news, "FRESH_S", -1)
    monkeypatch.setattr(news, "for_region", fake_region_news(calls, fail="news index returned 429"))
    got = news.get("kochi", "Kochi, Kerala")
    time.sleep(0.2)
    got2 = news.get("kochi", "Kochi, Kerala")
    assert got["status"] == got2["status"] == "ok" and got2["stale"] is True


def test_peek_returns_headlines_without_waiting(monkeypatch):
    gate = threading.Event()
    monkeypatch.setattr(news, "for_region", fake_region_news([], gate=gate))
    t0 = time.monotonic()
    assert news.peek("kochi", "Kochi, Kerala") is None              # nothing yet, and it did not block
    assert time.monotonic() - t0 < 1.0
    gate.set()
    time.sleep(0.3)
    assert news.peek("kochi", "Kochi, Kerala")[0]["title"].endswith("floods")


def test_warming_fills_each_city_once(monkeypatch):
    calls = []
    monkeypatch.setattr(news, "for_region", fake_region_news(calls))
    news.warm([("kochi", "Kochi, Kerala", None), ("singapore", "Singapore", None)], gap_s=0).join(5)
    assert sorted(calls) == ["Kochi, Kerala", "Singapore"]
    news.warm([("kochi", "Kochi, Kerala", None)], gap_s=0).join(5)
    assert len(calls) == 2                                           # already cached: not asked again


def test_calls_to_the_index_are_held_apart(monkeypatch):
    monkeypatch.setattr(news, "MIN_GAP_S", 0.3)
    news._last_call = 0.0
    t0 = time.monotonic()
    news._pace()
    news._pace()
    assert time.monotonic() - t0 >= 0.29


def test_the_road_outlook_uses_headlines_it_is_given_and_never_fetches(monkeypatch):
    monkeypatch.setattr(news, "fetch", lambda *a, **k: (_ for _ in ()).throw(AssertionError("fetched")))
    seen = {}
    monkeypatch.setattr(road_outlook, "write",
                        lambda title, findings, articles, **k: seen.update(articles=articles) or {"outlook": "x"})
    result = {"roads_timeline": {"features": []}}
    road_outlook.for_box(result, region_title="Kochi, Kerala", rainfall_mm=10, duration_h=3,
                         articles=[{"title": "t", "domain": "d"}])
    assert seen["articles"][0]["title"] == "t"
