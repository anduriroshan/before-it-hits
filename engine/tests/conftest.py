"""Test-wide setup."""
import pytest


@pytest.fixture(autouse=True)
def no_news_pacing(monkeypatch):
    """The news client waits 5.5 s between calls to the real index so as not to be rate-limited. No test
    talks to the real index, so that wait would only make the suite slow."""
    from bih_agents import news
    monkeypatch.setattr(news, "MIN_GAP_S", 0.0)
