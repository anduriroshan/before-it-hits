"""A7 Verifier: nothing reaches the screen that the results do not support.

Two checks, in this order, because they fail differently.

**The arithmetic check runs in code and cannot be talked out of it.** Every figure in the draft is
pulled out with a regular expression and matched against the figures in the facts bundle, allowing
ordinary rounding and percentage forms and nothing else. A model cannot argue with it, and it
holds even if the second check is having a bad day. This is what makes "no figure is ever produced
by a language model" a property of the system rather than a promise.

**The claim check needs judgement, so it uses a model.** It catches the thing the arithmetic check
is blind to: a draft whose every number is real and whose meaning is wrong. "Most of the area
floods" contains no numbers at all. So does "the water will be here by seven". The failure modes
it looks for are named rather than left to taste, and each one is a limit the product has already
written down.

It fails closed. An unsupported claim is removed, not softened, and if the claim check itself
cannot run the briefing is withheld rather than published unchecked - the whole value of the
briefing is that something checked it.
"""
from __future__ import annotations

import json
import os

from . import facts, llm

MAX_REVISIONS = 2     # the plan's "sends it back up to twice"
# How hard the claim check thinks. Measured on eight planted drafts, one per rule below plus a
# clean one: "low" catches all eight at 2.9 s a draft and passes the clean one, "medium" caught no
# more at 10.1 s, and the first version of this ran at "high" and took 63 s. Thinking harder buys
# nothing here, which makes sense - the findings are in the prompt, so this is a comparison
# rather than a deduction.
THINKING = os.environ.get("BIH_VERIFIER_THINKING", "low")

RULES = {
    "unsupported": "says something the findings do not contain",
    "own_depth": "gives a depth for one of our own spots, or attaches the authority's depth to "
                 "one of our spots",
    "unattributed": "quotes the authority's layer without saying whose it is, or without its unit",
    "scope": "describes findings about the one drawn box as if they covered the whole city",
    "timing": "says when water arrives or how long it stays",
    "dispatch": "tells anyone to evacuate, move or be dispatched",
    "overstated": "overstates how much of the area is affected",
    "certainty": "states as certain something the findings call uncertain, presents a forecast "
                 "or a what-if as a fact, or presents the satellite surface temperature as today's "
                 "weather or as the same thing as the air temperature or heat index",
}

SCHEMA = {
    "type": "OBJECT",
    "properties": {
        "findings": {
            "type": "ARRAY",
            "items": {
                "type": "OBJECT",
                "properties": {
                    "part": {"type": "STRING",
                             "description": "The part id exactly as given, e.g. bullet-2."},
                    "rule": {"type": "STRING", "enum": list(RULES)},
                    "problem": {"type": "STRING",
                                "description": "One line: what is wrong with it."},
                },
                "required": ["part", "rule", "problem"],
            },
        },
    },
    "required": ["findings"],
}

SYSTEM = """You check a draft briefing against the findings it was written from. The findings may
be about flooding, population density, heat, air quality, or a combination. You are the last
thing between a wrong sentence and a city officer acting on it. You do not rewrite anything
and you do not praise anything: you list what is wrong, and nothing else.

Report a part only if it breaks one of these rules:

- unsupported: says something the findings do not contain.
- own_depth: gives a depth for one of our own pooling spots, or attaches a depth from the
  "official" block to one of our spots. Our spots are ranked against each other and have no depth.
  The official figures are a separate authority's and describe their own flood layer.
- unattributed: uses a figure from the "official" block without naming whose layer it is, or
  quotes one of their values without the unit given in that block.
- scope: writes about the findings as if they covered the city, a district, or anywhere wider than
  the one drawn box. The box is named in the findings under "area" and it is usually a single
  neighbourhood. "111 spots across Kochi" is wrong when the 111 spots are all inside one box;
  "111 spots in this box" is right.
- timing: says when water arrives, how fast it rises or how long it stays. Fill order is an order,
  not a clock.
- dispatch: tells anyone to evacuate, to move, or to be dispatched. This product is preparation
  only.
- overstated: makes the extent sound larger than the findings say - "most of the area" when a few
  spots out of many are ranked top, or similar.
- certainty: presents a forecast or a hypothetical rainfall as something that will happen, or
  states as settled something the findings flag as uncertain or low confidence. The same rule
  covers the "surface_heat" block, which is a satellite picture of how hot the GROUND typically
  runs in the dry season. Report a part that presents it as today's or a forecast temperature, that
  calls a surface temperature "the temperature" or "how hot it is", or that compares it with the
  heat index or the air temperature as though they were the same kind of figure. Writing "the
  surface runs hotter than ..." about places within the box is fine.

Rephrasing is fine. Rounding a figure that is in the findings is fine. Saying less than the
findings do is fine. Return an empty list if the draft is clean."""


def parts(briefing: dict) -> list[tuple[str, str]]:
    """The draft as checkable pieces: (part id, text)."""
    out = [("headline", briefing.get("headline", ""))]
    out += [(f"bullet-{i + 1}", b) for i, b in enumerate(briefing.get("bullets", []))]
    out.append(("caveat", briefing.get("caveat", "")))
    return [(k, v) for k, v in out if v]


def check_numbers(briefing: dict, allowed: set[float]) -> list[dict]:
    """Figures with no fact behind them. Deterministic, and the guarantee the product rests on."""
    problems = []
    for part, text in parts(briefing):
        bad = facts.untraceable_numbers(text, allowed)
        if bad:
            shown = ", ".join(f"{n:g}" for n in dict.fromkeys(bad))
            problems.append({
                "part": part, "rule": "unsupported", "found_by": "arithmetic",
                "problem": f"cites {shown}, which is not in the findings",
                "text": text,
            })
    return problems


def check_caveat(briefing: dict, bundle: dict | None = None) -> list[dict]:
    """The one thing the briefing must always say, so it is checked rather than hoped for.

    When the bundle is a flood analysis (has pooling data or no explicit topic), the caveat must
    mention stormwater drains. For topic-specific bundles (population, heat, air_quality) the drain
    requirement does not apply — the engine was not run and there are no flood results to caveat."""
    caveat = (briefing.get("caveat") or "").lower()
    if not caveat:
        return [{"part": "caveat", "rule": "unsupported", "found_by": "arithmetic",
                 "problem": "the briefing has no caveat", "text": ""}]
    topic = (bundle or {}).get("topic") if bundle else None
    needs_drain_caveat = topic in (None, "flood", "overview")
    if needs_drain_caveat and "drain" not in caveat:
        return [{"part": "caveat", "rule": "unsupported", "found_by": "arithmetic",
                 "problem": "the caveat does not say that stormwater drains are missing from the "
                            "data, which is this product's largest known bias",
                 "text": briefing.get("caveat", "")}]
    return []


def check_official_units(bundle: dict, briefing: dict) -> list[dict]:
    """If the authority never stated its units, a briefing may not quote them as if it had.

    KSDMA publishes depth rasters with no unit anywhere in the metadata. The magnitudes read as
    centimetres and that is how everything records them, but "67 cm" written plainly is a firmer
    claim than the source supports. Deterministic rather than left to the claim check, because it
    is exactly checkable: if an official value appears, the word "inferred" has to appear too.
    """
    official = bundle.get("official") or {}
    if not official.get("units_are_inferred"):
        return []
    values = [v for v in (official.get("their_deepest_value_in_box"),
                          official.get("their_median_value_where_wet"))
              if isinstance(v, (int, float))]
    if not values:
        return []
    whole = " ".join(t for _, t in parts(briefing)).lower()
    if "inferred" in whole:
        return []
    for part, text in parts(briefing):
        if any(facts.traces_to_facts(n, set(values)) for n in facts.numbers_in_text(text)):
            return [{"part": part, "rule": "unattributed", "found_by": "arithmetic",
                     "problem": "quotes one of the authority's depth values, but their units are "
                                f"{official['units_note']} - the briefing has to say so",
                     "text": text}]
    return []


def check_claims(bundle: dict, briefing: dict, *, model: str = llm.CAREFUL_MODEL,
                 thinking: str = THINKING) -> list[dict]:
    """The judgement half. Raises LlmUnavailable - the caller must treat that as a failure."""
    numbered = "\n".join(f"[{part}] {text}" for part, text in parts(briefing))
    prompt = (f"Findings:\n{json.dumps(bundle, indent=1, ensure_ascii=False)}\n\n"
              f"Draft:\n{numbered}")
    reply = llm.ask(prompt, model=model, system=SYSTEM, schema=SCHEMA, temperature=0.0,
                    thinking=thinking)
    texts = dict(parts(briefing))
    out = []
    for f in (reply.data or {}).get("findings", []):
        part = f.get("part", "")
        if part not in texts:            # a finding against a part that does not exist is noise
            continue
        out.append({"part": part, "rule": f.get("rule", "unsupported"), "found_by": "claims",
                    "problem": (f.get("problem") or "").strip(), "text": texts[part]})
    return out


def enforce(briefing: dict, problems: list[dict]) -> tuple[dict | None, list[dict]]:
    """Remove what failed. Returns the briefing to publish, or None to withhold it.

    A failed bullet goes. A failed headline or caveat takes the whole briefing with it: the
    headline is the claim everyone reads, and a briefing without its caveat is the exact thing
    this product refuses to produce.
    """
    bad = {p["part"] for p in problems}
    removed = [p for p in problems if p["part"] in bad]
    if "headline" in bad or "caveat" in bad:
        return None, removed
    kept = [b for i, b in enumerate(briefing.get("bullets", []))
            if f"bullet-{i + 1}" not in bad]
    if not kept:
        return None, removed
    return {**briefing, "bullets": kept}, removed


def verify(bundle: dict, briefing: dict, *, model: str = llm.CAREFUL_MODEL) -> list[dict]:
    """Every problem with this draft. Arithmetic first, so a wrong figure is never argued about."""
    allowed = facts.allowed_numbers(bundle)
    return (check_numbers(briefing, allowed) + check_caveat(briefing, bundle)
            + check_official_units(bundle, briefing)
            + check_claims(bundle, briefing, model=model))
