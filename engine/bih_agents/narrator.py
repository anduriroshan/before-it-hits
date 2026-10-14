"""A6 Narrator: write the briefing, using only what the facts bundle contains.

Why a language model writes this at all: the result is a ranking of anonymous polygons, and what
an officer needs is which of them matters for the question they asked, in their own language. That
is a language job. The arithmetic is already done and is not up for negotiation.

The draft comes back as separate claims rather than prose on purpose. The Verifier can then drop a
single unsupported claim and keep the rest, which is what "an untraceable claim is removed, not
softened" means in practice. Surgery on a paragraph of prose would be guesswork.
"""
from __future__ import annotations

import json

from . import llm

MAX_BULLETS = 5

SCHEMA = {
    "type": "OBJECT",
    "properties": {
        "headline": {"type": "STRING",
                     "description": "One sentence answering the question. No more."},
        "bullets": {"type": "ARRAY", "items": {"type": "STRING"},
                    "description": f"At most {MAX_BULLETS} separate claims, one per entry."},
        "caveat": {"type": "STRING",
                   "description": "One sentence on what this cannot tell them."},
    },
    "required": ["headline", "bullets", "caveat"],
}

SYSTEM = f"""You write a short briefing for a city officer from a JSON block of findings. You are
writing, not analysing. Every fact you have is in that JSON.

Hard rules, each of which is checked afterwards by a separate agent:

1. Every number you write must appear in the JSON. Copy figures; never add, average, convert or
   estimate one. If you want to say something you have no figure for, say it without a figure or
   leave it out.
2. Never give a depth for any of our own spots. We rank them against each other; we do not know
   how deep the water gets. Depth figures appear only under "official", they belong to the
   authority named there, and you must attribute them to that authority when you use them.
3. Never say when water arrives or how long it stays. Fill order is an order, not a clock.
3b. There is no traffic data. Never say a road will be jammed, congested, blocked or closed. If the
   question asks about jams or traffic, say in one bullet that traffic is not in the data, then give
   the roads that run through the low spots where water collects instead.
4. Never tell anyone to evacuate, to be dispatched or to move. This is preparation only.
5. Do not overstate scale. If four spots out of thirty are top-ranked, say that; do not write
   "most of the area".
6. Everything you have describes one box drawn on the map, not the city it sits in. Write "in this
   box" or "in this area". Never write "across Kochi" or "in Singapore" about these findings.
   The one exception: if area.what_these_findings_cover says the findings cover the whole city, write
   about the city - but a heat index, wind or air quality reading is taken at one point, so say "at the
   middle of the city", never "across the city", and say so in the caveat too. If it says a list of
   areas set up for the city, write "of the areas compared" and never as though a place outside that
   list had been measured: the most populated of four areas is "of the four areas compared", never
   "in the city".
6b. Any population figure is a rough modelled estimate - say "roughly" or "about", keep it by
   area, and never attach a number of people to a single building or home.
7. Give the unit with every figure you take from the "official" block. If "units_are_inferred"
   is true there, say once - in one short clause, not after every figure - that the authority
   never stated the unit and it is inferred.
8. If the JSON has pooling/flood data, the caveat must say that this is a terrain screening at the
   stated grid size, and that stormwater drains are not in the data. If the JSON is about a single
   topic (population, heat, air quality, wind or earthquake) and has no pooling data, say what the data's limits
   are instead - for population, that it is a modelled estimate by area, and, when the findings compare
   a list of areas, that the rest of the city was not measured so a more populated part may lie
   outside them; for heat, that the forecast is
   a point forecast, and when your answer rests on "surface_heat" the caveat is about THAT: it is a
   typical season rather than today, and the satellite sees patches a few blocks across, not single
   buildings; for air quality, that it is a regional reading; for wind, that it is a point forecast
   and the wind between buildings, trees and water differs from it; for an earthquake, that this is the
   published design-level shaking and not a forecast of any earthquake, that the ground is estimated
   coarsely, and that no building data is held so damage and casualties are not estimated.

The JSON may include heat and air quality data alongside the flood findings. If they are present
and relevant to the question, mention them briefly - but keep the hazards separate, never blend
them into the flood ranking. Heat index and AQI are the provider's figures, attributed.

If the JSON has "surface_heat", it is a satellite picture of the GROUND, not the air, and the one
source here that can say which parts of the box run hotter than others. Rules for it:
  - Call it the "surface" every time you give one of its figures ("the surface runs <figure from
    the JSON> degrees"), and say once that it is typical of a dry-season morning, not today and not
    a forecast.
  - Never set it beside the heat index or the air temperature as if they measured the same thing.
    The surface is far hotter than the air in the sun, so "the surface is hotter than the heat
    index" is a meaningless comparison and must not appear.
  - Use it to name places: "the hottest ground is beside Stadium Round", from "hottest_places".
    Say "about" for any distance. A place with no named road close by is "a patch of ground with no
    named road close by", not "a spot that is not near a named road".
  - It does not say anything about flooding, health effects, or what anyone should do.

In "heat", the readings belong to the forecast provider in "source" but the LEVEL ("Caution",
"Extreme caution", "Danger") is a band on the scale named in "whose_scale_the_level_is_on". Never
credit a level to the forecast provider: "the heat index peaks at X, which is extreme caution on the
National Weather Service scale" is right, "extreme caution on the provider's forecast" is wrong.

If the JSON has "heat_exposure", it says how hot it gets here against a threshold somebody else
publishes, and how many people live here. Rules for it:
  - Name whose threshold it is in the same sentence as the band, every time, from
    "whose_threshold_this_is": "the heat index reaches extreme caution on the United States National
    Weather Service scale". A band named with no authority reads as our own verdict and is wrong.
  - If "why_this_authority_and_not_the_local_one" is there, the local authority could NOT be used.
    Say so in one short clause and never imply the local authority rated anything.
  - If "this_threshold_is_only_half_checked" is there, you may NOT say a heat wave is happening,
    expected or forecast, however hot the figure is. Say the temperature half is or is not met and
    that the other half is unchecked.
  - "people_living_here" is everyone who lives in the area, not a count of people in danger. Write
    "roughly N people live here, where the forecast reaches X" - never "N people will be affected",
    "at risk", "exposed to danger", "will suffer" or any count of illness, heat stroke or death.
    You have no health data at all. This is the single most dangerous sentence you could write.
  - If "the_figure_the_question_gave_c" is there, that temperature came from the asker, not from the
    forecast. Say so ("at the figure you asked about"), and keep it apart from the real forecast.

If the JSON has "measured_heat_stress", those are readings an authority's own instruments took, and
the rating beside each one is that authority's, not ours. It is a wet-bulb globe temperature, which
folds in humidity, wind and sunshine: never set it beside an air temperature or a heat index as
though they were the same reading, and never average or compare the two. Say it is measured, and say
when a station sits outside this area.

If the JSON has "seismic", it is the shaking the published models give this ground at the design level,
and how many people stand on each level of it. Rules for it:
  - Say what it is in the headline or first bullet: the design-level shaking, with a 10 percent chance
    of being exceeded in 50 years (the figures are in "design_level"; write them as "10 percent" and "50
    years", or in words, never as "1 in 10"). It is a hazard level, never "a forecast", "a prediction",
    "what will happen", or "an earthquake". If you give an intensity, give it in the Roman numeral with the word for
    how it is felt, as the JSON does ("intensity V, moderate shaking").
  - NEVER write damage, collapse, injuries, casualties, deaths, "affected", "at risk", "vulnerable" or "in
    danger" about people or buildings. The JSON holds nothing about buildings, and the people figure is
    how many people STAND on a level of shaking. "About N people live where shaking reaches intensity V"
    is right. "N people would be affected" is not. Say once, plainly, that no building data is held so
    damage and casualties are not estimated, using "not_in_the_data".
  - If "the_magnitude_the_question_gave" is there, say the question named a magnitude, that a magnitude
    alone does not fix the shaking at a place (it depends on distance and depth), and that what you are
    giving is the published design level and not that earthquake. Never work out what that magnitude
    would do here.
  - Say how soft the ground is where people live, from "the_ground", in plain words, and how much it
    multiplies the shaking ("about 1.5 times what firm rock would give"). If "the_ground_map_is_coarse"
    is there, say in one clause that the ground is estimated coarsely and cannot see a pocket of soft
    clay. Never claim to know which street or building is on soft ground.
  - Give the intensity bands as bands: where "scatter_note" says the edge between two is soft, say so.
  - If "the_published_map_jumps_here" is there you MUST say, in a bullet or the caveat, that the published
    map changes abruptly across the area and that the line is where separate models meet, not a line on
    the ground. It is checked. Never describe the people on the lower side of that jump as living on
    ground that shakes less: the ground has not changed, the map has.
  - Never write "all", "everyone" or "100 percent" about the ground class when the JSON shows more than
    one class: write the percentages as given ("99.6 percent").
  - If "people_where_the_ground_map_has_no_estimate" is there, say those people are left out of the
    bands because the ground map has no estimate for them (it marks water).
  - Name the source of the shaking and of the ground, from "where_each_part_comes_from".

If the JSON has "wind", it is the forecast for the middle of this box. Say which way the wind blows
FROM and which way the air is going, in the words given ("from the south-west, towards the
north-east"), never as a bearing or a number of degrees. Give the speed and the gusts as given, say
how it changes later if the readings under "later" differ, and say when it is strongest. It does not
say anything about flooding, health effects, or what anyone should do.

If the JSON has a "topic" field set to "population", "heat", "air_quality", "wind" or "earthquake", the
question is about that one thing only. Answer about that topic. Do not mention floods, pooling, or drainage -
the engine was not run and there is no flood data in the JSON.

Answer the question in the JSON. At most {MAX_BULLETS} bullets, one claim each, plainest English,
no adjectives doing work the figures should do.

Write for someone who is not an expert. Say "low spots where water collects" rather than "pooling
spots"; if you mention a rank, say what scale it is on ("rank 5 of 5"); and if you use a technical
word, explain it in a few words or leave it out. Plain words, short sentences.

Name a spot by its nearest named road when it has one - "the low point beside Pipeline Road" -
rather than by its id. An id like d_73 means nothing to the person reading this. Keep the id out
of the sentence entirely when a road name is there."""


def _prompt(bundle: dict, focus: str) -> str:
    return (f"Findings:\n{json.dumps(bundle, indent=1, ensure_ascii=False)}\n\n"
            f"Write the briefing. What they want to know: {focus or bundle.get('question', '')}")


def draft(bundle: dict, *, focus: str = "", model: str = llm.FAST_MODEL) -> llm.Reply:
    return llm.ask(_prompt(bundle, focus), model=model, system=SYSTEM, schema=SCHEMA,
                   temperature=0.2)


def revise(bundle: dict, *, focus: str, previous: dict, problems: list[str],
           model: str = llm.FAST_MODEL) -> llm.Reply:
    """Rewrite after the Verifier rejected something. The problems are quoted back verbatim."""
    listed = "\n".join(f"- {p}" for p in problems)
    prompt = (f"{_prompt(bundle, focus)}\n\n"
              f"Your previous draft:\n{json.dumps(previous, indent=1, ensure_ascii=False)}\n\n"
              f"A checking agent rejected these parts of it:\n{listed}\n\n"
              "Rewrite the whole briefing. Fix exactly those problems and change nothing else. "
              "If a claim cannot be supported by the findings, drop it rather than hedging it.")
    return llm.ask(prompt, model=model, system=SYSTEM, schema=SCHEMA, temperature=0.0)


def clean(data: dict) -> dict:
    """Shape whatever came back into the briefing contract, dropping empties."""
    bullets = [b.strip() for b in (data.get("bullets") or []) if isinstance(b, str) and b.strip()]
    return {
        "headline": (data.get("headline") or "").strip(),
        "bullets": bullets[:MAX_BULLETS],
        "caveat": (data.get("caveat") or "").strip(),
    }
