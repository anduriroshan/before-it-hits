"""A8 Guide: answers questions about the product itself, with no analysis of any area.

"What does pooling rank mean?", "why no depths?", "how far can I trust this?" are not questions about
a place, so running the terrain engine to answer them would be theatre. They are answered from a
short written reference of what this product does and what it refuses to claim.

The honesty rules are the same as everywhere else, enforced not just asked:
  - It may use no number that is not in the reference or in the conversation so far, where the
    earlier answers' figures came from the engine. The arithmetic check removes any other.
  - It answers from the reference only. If the reference does not cover it, it says so.
Fails closed: if no clean answer can be had, the caller shows a fixed fallback instead.
"""
from __future__ import annotations

from . import facts, hazards, llm, scenario

REFERENCE = f"""WHAT THIS PRODUCT IS
Before It Hits shows what a hazard could do to a part of a city before it arrives. It is for
preparation and planning, never for live dispatch or evacuation. The hazards it can screen today:
{hazards.labels()}. If someone asks about a hazard that is not in that list, say plainly that it is
not available yet and do not describe how it would work.

For rain and flooding, an area on the map shows where rain water would collect, which roads run
through those places, and roughly how many people live there. It works from terrain (elevation) data
at about 30 m resolution, OpenStreetMap roads and buildings, and a live rainfall forecast.

For heat stress, the Heat tab shows the current heat index, temperature, humidity and wind for the
city centre, plus a forecast over the next 48 hours. Heat index combines air temperature and humidity
to show how hot it feels. A heat level (Normal, Caution, Extreme caution, Danger, Extreme danger) is
shown based on the heat index value. This is a forecast, not a measurement at any specific spot.

For air quality, the Air tab shows the current Air Quality Index (AQI), the dominant pollutant and
its concentration, plus an hourly AQI forecast. All values are the Google Air Quality API's own
numbers, shown as published. AQI is a regional reading — it does not vary at building level. This
product does not predict health effects for any individual.

THE PARTS OF THE SCREEN
- The Agent tab is a chat. Ask about a place, a whole city, or what any term means.
- The Rain tab shows the rainfall forecast. The Heat tab shows heat conditions and forecast.
  The Air tab shows air quality conditions and forecast.
- The Details tab holds the findings behind an answer.
- The News tab lists recent news reports about the city, and lets you ask questions about them. News
  is context only: it is never stored and it changes no figure on the map.
- The Layers button on the map toolbar toggles data layers: Traffic, Population density, Humidity,
  Heat index, and Air quality. Each layer has an opacity slider.

HOW TO READ WHAT IT SHOWS
- A "low spot" or "pooling spot" is a hollow in the terrain where rain runoff collects.
- Pooling rank runs from 1 to 5 and compares a spot with the other spots in the same box: 5 is the
  most water collecting. It is a ranking within the box, never an amount of water. Spots ranked
  4 or 5 are called the top-ranked ones; they are the ones that hold the most.
- Fill order says which spot fills first as rain builds up. It is an order, not a clock: it does
  not say when water arrives or when it clears.
- A road is marked where it runs through a spot that fills at the rainfall being looked at.
- The road playback shows how the marked roads grow as rain accumulates, assuming none of it drains
  away, which is the worst case. It is not traffic and not a forecast of standing water hours later.
- The people figure spreads the city's published population over its mapped building footprints by
  area, then adds up the footprint inside the worst spots. It is rough, by area only, and never a
  statement about one building or home.
- For Kochi, the Kerala State Disaster Management Authority publishes flood depth maps. They can be
  shown alongside our ranking, as published, and are never merged into it.
- For Kochi, a preparation plan ranks measures a city can afford from its documented budget lines.
  Beside it, a before-and-after view shows the best case: how many worst spots, people and road
  stretches would be left if every listed measure fully worked. It is an upper bound on what the plan
  could do, not a forecast, because a pump or an unblocked drain reduces a problem and rarely ends it.
- The live rainfall forecast comes from the Google Weather API. It is a model prediction for one
  point at the centre of the area, not a measurement. Singapore also has live rain gauges.

WHAT IT DELIBERATELY DOES NOT CLAIM
- No water depth in metres. At this terrain resolution the error is as large as the depth, so spots
  are ranked against each other instead.
- No storm-drain data is public anywhere, so the screening assumes drains do nothing. It therefore
  overstates pooling wherever drains work, most of all in a well-drained city such as Singapore.
- No timing for when water arrives or drains away.
- No claim about any single building.
- When it was checked against the May 2024 Kochi cloudburst, the ranking could not tell the places
  that flooded from the others at that extreme rainfall, because it flags most of the area then.
  That is reported openly as a limit.

COVERAGE
Two cities are set up: Kochi in India and Singapore. A place outside them cannot be analysed yet."""

SYSTEM = """You answer a question about a hazard-screening product for someone who is not an expert,
using ONLY the reference below and the conversation so far.

Rules, each checked afterwards:
- Two to five plain sentences. Explain any term you use. No jargon, no bullet lists.
- Say only what the reference or the conversation supports. If the reference does not cover the
  question, say plainly that this product does not claim it. Do not guess.
- Use no number that is not in the reference or the conversation. Never invent a count, a
  percentage, a depth, a time or a distance.
- If they want a prediction for a particular place or area, say you need to analyse an area first and
  ask them to name a place or draw a box on the map.
- Never give evacuation, dispatch or rescue instructions.

REFERENCE
""" + REFERENCE

FALLBACK = ("I could not put a checked answer together for that. Ask about an area on the map and I "
            "will analyse it, or try rephrasing the question.")


def answer(question: str, *, history: list | None = None, area_label: str | None = None,
           region_title: str | None = None, model: str = llm.FAST_MODEL) -> str | None:
    """Return the answer text, or None if no clean one could be had (the caller shows FALLBACK)."""
    history = scenario.clean_history(history)
    convo = scenario._history_block(history) if history else "(this is the first message)"
    prompt = (f"City: {region_title or 'not chosen'}\nSelected area: {area_label or 'none'}\n"
              f"Conversation so far:\n{convo}\n\nQuestion: {question}")
    allowed = facts.allowed_numbers({"reference": REFERENCE, "conversation": convo})
    for _ in range(2):
        try:
            text = llm.ask(prompt, model=model, system=SYSTEM, temperature=0.2,
                           thinking="low").text.strip()
        except llm.LlmUnavailable:
            return None
        if text and not facts.untraceable_numbers(text, allowed):
            return text
        prompt += ("\n\nYour previous reply used a number that is not in the reference or the "
                   "conversation. Rewrite it using only figures that are there, or none.")
    return None
