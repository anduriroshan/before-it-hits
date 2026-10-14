"""Which hazards this product can screen for today.

One list, read by the welcome message, the Guide's reference, the news agent and the options offered
once a box is drawn, so a hazard added here appears everywhere at once instead of the product talking
about rain in four places. Only hazards whose engine is built and tested belong in `HAZARDS`; a
planned one is not listed, because the product must never describe a capability it does not have.

`option` is what the chat offers after a box is drawn: a short label and the question it asks.
"""
from __future__ import annotations

HAZARDS = [
    {
        "id": "flood",
        "label": "Rain and flooding",
        "what": "where rain water collects in the terrain, which roads run through those places, and "
                "roughly how many people live there",
        "example": "Will {place} flood tonight?",
        "option": {"label": "Rain and flooding", "q": "Which spots fill first here?"},
    },
    {
        "id": "heat",
        "label": "Heat stress",
        "what": "current and forecast heat index, temperature and humidity conditions, "
                "whether the heat level is in a caution, warning or danger zone, and which parts "
                "of an area run hotter at the ground, from a satellite",
        "what_more": "how hot it gets against the threshold this city's own weather authority "
                     "publishes, and how many people live where that applies",
        "example": "How hot will {place} be today?",
        "option": {"label": "Heat forecast", "q": "How hot will it get here over the next two days?"},
        "options_more": [{"label": "Who is exposed to heat",
                          "q": "How hot does it get here against the official threshold, and how "
                               "many people live here?"}],
    },
    {
        "id": "wind",
        "label": "Wind",
        "what": "wind speed, gusts and which way the wind blows, now and over the next two days, "
                "drawn on the map as arrows and moving streaks",
        "example": "Which way is the wind blowing in {place}?",
        "option": {"label": "Wind and direction", "q": "Which way is the wind blowing here, and how strong will it get?"},
    },
    {
        "id": "earthquake",
        "label": "Earthquake shaking",
        "what": "the published design-level shaking for this ground, how much the ground under it "
                "amplifies that, and how many people stand on each level of it; it stops at shaking "
                "and never estimates damage or casualties",
        "example": "How strong could earthquake shaking be in {place}?",
        "option": {"label": "Earthquake shaking",
                   "q": "How strong could earthquake shaking be here, and how many people live on which ground?"},
    },
    {
        "id": "air",
        "label": "Air quality",
        "what": "current AQI, dominant pollutant and concentration levels, "
                "plus an hourly AQI forecast",
        "example": "How is the air quality in {place}?",
        "option": {"label": "Air quality", "q": "How is the air quality here?"},
    },
]


def live() -> list[dict]:
    """The hazards the engine can screen right now."""
    return [dict(h) for h in HAZARDS]


def labels() -> str:
    """'Rain and flooding' or 'Rain and flooding and Heat' - for sentences."""
    names = [h["label"] for h in HAZARDS]
    return names[0] if len(names) == 1 else ", ".join(names[:-1]) + " and " + names[-1]
