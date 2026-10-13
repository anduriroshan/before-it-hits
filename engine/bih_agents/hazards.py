"""Which hazards this product can screen for today.

One list, read by the welcome message, the Guide's reference and the news agent, so a hazard added
here appears everywhere at once instead of the product talking about rain in three places. Only
hazards whose engine is built and tested belong in `HAZARDS`; a planned one is not listed, because
the product must never describe a capability it does not have.
"""
from __future__ import annotations

HAZARDS = [
    {
        "id": "flood",
        "label": "Rain and flooding",
        "what": "where rain water collects in the terrain, which roads run through those places, and "
                "roughly how many people live there",
        "example": "Will {place} flood tonight?",
    },
    {
        "id": "heat",
        "label": "Heat stress",
        "what": "current and forecast heat index, temperature and humidity conditions, "
                "and whether the heat level is in a caution, warning or danger zone",
        "example": "How hot will {place} be today?",
    },
    {
        "id": "air",
        "label": "Air quality",
        "what": "current AQI, dominant pollutant and concentration levels, "
                "plus an hourly AQI forecast",
        "example": "How is the air quality in {place}?",
    },
]


def live() -> list[dict]:
    """The hazards the engine can screen right now."""
    return [dict(h) for h in HAZARDS]


def labels() -> str:
    """'Rain and flooding' or 'Rain and flooding and Heat' - for sentences."""
    names = [h["label"] for h in HAZARDS]
    return names[0] if len(names) == 1 else ", ".join(names[:-1]) + " and " + names[-1]
