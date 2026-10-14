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
4. Never tell anyone to evacuate, to be dispatched or to move. This is preparation only.
5. Do not overstate scale. If four spots out of thirty are top-ranked, say that; do not write
   "most of the area".
6. Everything you have describes one box drawn on the map, not the city it sits in. Write "in this
   box" or "in this area". Never write "across Kochi" or "in Singapore" about these findings.
6b. Any population figure is a rough modelled estimate - say "roughly" or "about", keep it by
   area, and never attach a number of people to a single building or home.
7. Give the unit with every figure you take from the "official" block. If "units_are_inferred"
   is true there, say once - in one short clause, not after every figure - that the authority
   never stated the unit and it is inferred.
8. If the JSON has pooling/flood data, the caveat must say that this is a terrain screening at the
   stated grid size, and that stormwater drains are not in the data. If the JSON is about a single
   topic (population, heat, or air quality) and has no pooling data, say what the data's limits
   are instead - for population, that it is spread by building area; for heat, that the forecast is
   a point forecast, and when your answer rests on "surface_heat" the caveat is about THAT: it is a
   typical season rather than today, and the satellite sees patches a few blocks across, not single
   buildings; for air quality, that it is a regional reading.

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
    Say "about" for any distance. If a place has no named road, say it is not near a named road.
  - It does not say anything about flooding, health effects, or what anyone should do.

If the JSON has a "topic" field set to "population", "heat", or "air_quality", the question is
about that one thing only. Answer about that topic. Do not mention floods, pooling, or drainage -
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
