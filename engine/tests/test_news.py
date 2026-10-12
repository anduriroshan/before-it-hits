"""News parsing and the display-only contract, offline. The live GDELT call and the model are
stubbed - what can be wrong here is the dedup, the date parsing and the grounding discipline."""
import json

import pytest

from bih_agents import news
from bih_agents.llm import LlmUnavailable


class FakeResp:
    def __init__(self, payload):
        self._b = json.dumps(payload).encode()

    def read(self):
        return self._b

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def payload(*titles):
    return {"articles": [
        {"title": t, "url": f"https://{d}/x", "domain": d, "seendate": sd,
         "language": lang, "sourcecountry": "IN"}
        for t, d, sd, lang in titles]}


def test_dates_parse_and_bad_ones_become_none(monkeypatch):
    monkeypatch.setattr(news.urllib.request, "urlopen",
                        lambda *a, **k: FakeResp(payload(
                            ("Rain floods Kochi", "a.com", "20261010T120000Z", "English"),
                            ("No date here", "b.com", "not-a-date", "English"))))
    arts = news.fetch("Kochi flooding")
    assert arts[0]["seen"].startswith("2026-10-10")
    assert arts[1]["seen"] is None


def test_near_identical_reports_are_de_duplicated(monkeypatch):
    monkeypatch.setattr(news.urllib.request, "urlopen",
                        lambda *a, **k: FakeResp(payload(
                            ("Kochi flooded after cloudburst", "x.com", "20261010T000000Z", "English"),
                            ("Kochi flooded after cloudburst", "x.com", "20261010T010000Z", "English"),
                            ("Kochi flooded after cloudburst", "y.com", "20261010T020000Z", "English"))))
    arts = news.fetch("Kochi flooding")
    assert len(arts) == 2          # same domain+title collapses; a different domain survives


def test_an_empty_index_is_an_empty_list_not_an_error(monkeypatch):
    monkeypatch.setattr(news.urllib.request, "urlopen", lambda *a, **k: FakeResp({"articles": []}))
    assert news.fetch("Kochi flooding") == []


def test_for_region_always_marks_itself_display_only(monkeypatch):
    monkeypatch.setattr(news, "fetch", lambda q, **k: [])
    monkeypatch.setattr(news, "digest", lambda *a, **k: None)
    out = news.for_region("Kochi, Kerala")
    assert "not part of" in out["note"] and "not stored" in out["note"]
    assert out["query"].startswith('"Kochi"')
    assert out["articles"] == [] and out["digest"] is None


def test_the_digest_degrades_to_nothing_when_no_model_is_available(monkeypatch):
    monkeypatch.setattr(news.llm, "ask",
                        lambda *a, **k: (_ for _ in ()).throw(LlmUnavailable("no key")))
    assert news.digest("Kochi", [{"title": "t", "domain": "d"}]) is None


def test_no_articles_means_no_digest_without_even_calling_the_model(monkeypatch):
    called = {"n": 0}
    monkeypatch.setattr(news.llm, "ask", lambda *a, **k: called.__setitem__("n", 1))
    assert news.digest("Kochi", []) is None and called["n"] == 0
