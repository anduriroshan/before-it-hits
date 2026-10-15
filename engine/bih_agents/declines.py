"""What the product says when it is asked for something it does not do.

Users ask for things the builder did not think of: traffic when there is none, a hospital with no layer
behind it, a hazard that is not screened, rain next week, a verdict on one house. Before this, a question
the router did not recognise fell into the 20-second rain pipeline and came back as irrelevant flood
numbers ("Traffic is not in the data, but 285 low spots fill at 100 mm"), a greeting returned a rain
analysis, and "is a cyclone coming?" was answered "no cyclone in the data", which a reader can take as
reassurance when it only means the product has no view.

So a limit is named, not stumbled into. A2 only READS which limit a question meets, from a closed list
(scenario.DECLINES). What is then said about it is written here, once, and reviewed: the same question
gets the same honest answer every time, a model cannot talk its way past a limit, and a decline that has
nothing to analyse costs one model call instead of the whole pipeline.

Two shapes:
  ONLY      nothing to analyse. A fixed text says what cannot be given, why, and what can be.
  PARTIAL   the limit is part of a question about an area ("will my house flood"). The limit travels into
            the findings and the Narrator must say it first, then give what the data does support. It is
            never answered around.

Each reason below is one of the limits written down before building (README, "What this will not claim").
The prose has no digits: a digit in the findings becomes a figure a briefing may quote.
"""
from __future__ import annotations

from . import hazards

PARTIAL = {
    "evacuation": {
        "asked_for": "where people should go, or whether to evacuate",
        "why_not": "This product is for preparation and planning, never for live dispatch or evacuation. "
                   "It does not route anyone and it does not tell anyone to move.",
    },
    "single_building": {
        "asked_for": "a verdict on one house, school or building",
        "why_not": "It never makes a claim about a single building. It ranks the places where water would "
                   "collect across an area, and one building is too small for the data to speak to.",
    },
    "water_depth": {
        "asked_for": "how deep the water gets",
        "why_not": "The terrain is known only to about thirty metres, so its error is as large as any depth "
                   "it could report. Places are ranked against each other instead of being given a depth.",
    },
    "water_timing": {
        "asked_for": "when water arrives, how long it stays, or when it drains away",
        "why_not": "The order in which places fill is an order, not a clock. Timing needs the storm-drain "
                   "network, which is not public anywhere.",
    },
    "drainage": {
        "asked_for": "whether the drains will cope",
        "why_not": "No storm-drain data is public anywhere, so the screening assumes drains do nothing. It "
                   "therefore overstates pooling wherever drains work, most of all in a well-drained city.",
    },
    "damage_or_casualties": {
        "asked_for": "damage, costs, injuries or deaths",
        "why_not": "That needs building and health data this product does not hold. It says where water "
                   "would collect or how hard the ground would shake, and how many people live there, and "
                   "stops.",
    },
    "personal_advice": {
        "asked_for": "whether it is safe, or what a person should do",
        "why_not": "This product screens an area. It does not give personal safety advice, and it does not "
                   "combine different hazards into one verdict of safe or unsafe.",
    },
    "hazard_ranking": {
        "asked_for": "which hazard is the biggest risk",
        "why_not": "Hazards are kept separate. Putting rain, heat, air and earthquakes on one scale would "
                   "mean inventing an exchange rate between them, so each is reported on its own.",
    },
    "probability": {
        "asked_for": "the probability of flooding, a chance, or whether it is a one-in-so-many-years event",
        "why_not": "The screening says which places would fill at a given rainfall. It does not give a chance "
                   "to that rainfall or to flooding, which would need local intensity-duration-frequency "
                   "curves that are not sourced for these cities, so it never turns a forecast into a "
                   "return period.",
    },
    "beyond_forecast": {
        "asked_for": "the weather further ahead than about two days",
        "why_not": "The rainfall forecast reaches about two days ahead. It cannot speak to later weather, so "
                   "what follows is that forecast and nothing more.",
    },
}

ONLY = {
    "other_hazard": (
        "I don't screen {what}, so I can't tell you anything about it either way. The hazards I can screen "
        "are {hazards}. Please don't read my silence as an all-clear: not being on that list means this "
        "product has no view on {what}, not that there is no risk. Ask me about one of those for a place, "
        "or about the whole city."),
    "long_term": (
        "I don't make long-range or climate projections. My rainfall forecast reaches about two days ahead, "
        "and saying what a city will be like in years or decades needs climate models this product does not "
        "include. I can screen what the next two days could do to a place, the published design-level "
        "earthquake shaking for it, or how many people live there."),
    "past_rainfall": (
        "I don't hold a record of past rainfall, so I can't say how much fell in the past. My forecast looks "
        "about two days ahead, and in Singapore the live rain gauges show the last few minutes. I can tell "
        "you what is forecast from now, or you can ask what has been reported about past rain and I will "
        "search the news."),
    "facilities": (
        "I don't have hospitals, schools, shelters, utilities or transport services on the map, so I can't "
        "say where they are, how many there are, or whether one would be cut off or running. What the data "
        "does support is which roads run through the low spots where rain water would collect. Draw a box or "
        "pick an area and ask about those roads."),
    "health": (
        "I hold no health data, so I can't say how many people would be ill or hurt, or what disease might "
        "follow a hazard. What I can say is who lives where a hazard applies: roughly how many people live in "
        "an area, or who lives where a published heat threshold would be met."),
}

ROUTED = {
    "news": ("That is a question about what is being reported, which is a different source from the "
             "screening, so I have opened the News tab and asked it for you. It shows headlines with their "
             "publishers and marks anything it cannot support."),
    "history": ("I don't hold a record of past floods or events. What was reported about them is in the "
                "news, so I have opened the News tab and asked it. It searches recent coverage, so an older "
                "event may not be there, and it will say so."),
    "preparations": ("The preparation plan for an area is in the Details tab. It is built from the rain "
                     "just analysed for that area and the city's own documented budget, so I have opened it."),
}

NO_BUDGET = ("A preparation plan ranks measures against a documented city budget, and {city} has none "
             "staged, so I can't build one. Kochi has one. What I can do for {city} is show where rain water "
             "would collect and which roads run through those places.")
NEEDS_AREA_FOR_PLAN = ("The preparation plan is built for one area from the rain just analysed there, so "
                       "pick an area first. Draw a box or choose one below and I will run the rain forecast "
                       "for it, then the plan is in the Details tab.")
OUT_OF_RANGE = ("{asked} is far beyond the rainfall I screen. I run what-ifs up to {limit} millimetres in "
                "a single event, and the slider stops at three hundred. Ask for a figure in that range, or "
                "ask what the live forecast says.")
AREA_NEEDED_AFTER = " Pick an area and I will show what the data does support."
TRAFFIC_NEEDS_AN_AREA = (
    "I hold no traffic data, so I can't say how any road in the city will behave. What I can do is take one "
    "area, say how strong the rain is against what published studies found for roads of each kind, and name "
    "the roads that run through the low spots where water would collect. Pick an area and ask again.")


def _cities(region_titles: list[str]) -> str:
    names = [t.split(",")[0] for t in region_titles]
    return names[0] if len(names) == 1 else ", ".join(names[:-1]) + " and " + names[-1]


def only(kind: str, detail: str = "") -> str:
    """The fixed answer to a question with nothing to analyse."""
    return ONLY[kind].format(what=detail or "that", hazards=hazards.labels())


def partial_note(kind: str) -> dict:
    """What travels into the findings so the Narrator says the limit before anything else."""
    p = PARTIAL[kind]
    return {"asked_for": p["asked_for"], "why_not": p["why_not"],
            "what_to_do": "Say plainly, in the headline or the first bullet, that this cannot be given and "
                          "why, then give what the findings do support for the same area. Never answer the "
                          "part that was declined."}


def partial_only(kind: str) -> str:
    """The same limit as a plain answer, for when there is no area to give the rest for."""
    p = PARTIAL[kind]
    return f"I can't give {p['asked_for']}. {p['why_not']}" + AREA_NEEDED_AFTER


def out_of_range(asked: str, limit_mm: float) -> str:
    return OUT_OF_RANGE.format(asked=asked or "That figure", limit=f"{int(limit_mm):,}")


def capabilities(region) -> dict:
    """What a staged city lets this product see, read from its own manifest and nothing else."""
    m = region.manifest
    return {
        "live_rain_gauges": bool(m.get("live_rainfall")),
        "measured_heat_stress": ((m.get("heat_standard") or {}).get("observed") or {}).get("source") == "nea_wbgt",
        "official_flood_depth_layer": bool(m.get("official")),
        "population_checked_against_a_census_by_small_area": bool((m.get("population_grid") or {}).get("validation")),
        "documented_preparedness_budget": bool(m.get("preparations")),
    }


# (key, how it reads when a city has it)
_ROWS = [
    ("live_rain_gauges", "live rain gauges reporting every few minutes"),
    ("measured_heat_stress", "the national weather agency's own measured heat-stress readings"),
    ("official_flood_depth_layer", "an official flood-depth layer to check the screening against"),
    ("population_checked_against_a_census_by_small_area",
     "population placement checked against a census by neighbourhood"),
    ("documented_preparedness_budget", "a documented budget to plan preparations against"),
]


def compare(regions: list) -> str:
    """The cities side by side, as what can be KNOWN about each. Deliberately not a score: ranking cities
    would need an exchange rate between very different things, and this product does not invent one."""
    if len(regions) < 2:
        return ("Only one city is set up here, so there is nothing to compare it with. The product covers "
                "the cities it has data for and says so.")
    caps = {r.manifest["title"].split(",")[0]: capabilities(r) for r in regions}
    names = list(caps)
    lines = ["I answer for one city at a time and I don't rank cities, so I can't say which is hotter, wetter "
             "or safer. What I can compare is what each city lets this product see:"]
    for key, words in _ROWS:
        have = [n for n in names if caps[n][key]]
        lacks = [n for n in names if not caps[n][key]]
        if have and lacks:
            lines.append(f"{_cities(have)} has {words}; {_cities(lacks)} does not.")
        elif have:
            lines.append(f"Every city has {words}.")
    lines.append("Both have the rain forecast, heat, air quality, wind, earthquake shaking and terrain: "
                 "the same screening runs on both.")
    lines.append("These are differences in what can be known about each city, not a score. This product does "
                 "not rank cities or say which is safer.")
    return "\n".join(lines)
