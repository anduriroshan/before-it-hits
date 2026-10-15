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
import re

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
    "unattributed": "quotes the authority's layer without saying whose it is, or without its unit, "
                    "or names a heat band without saying whose scale it is",
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

- unsupported: says something the findings do not contain. This covers any claim about health,
  injury or damage: the findings never contain one. A population count under "heat_exposure" or
  "seismic" is how many people LIVE somewhere, so writing that they are "at risk", "in danger",
  "affected", "vulnerable", "will suffer", or any count of illness, injury, collapse or death is
  unsupported, and so is declaring a heat wave when the findings say the threshold is only half
  checked. For "seismic" the findings hold nothing at all about buildings, so any statement about
  damage, collapse or casualties - or what a stated magnitude "would do" - is unsupported. A sentence
  that says damage is NOT estimated is fine; a sentence that estimates it is not. Naming a band is
  fine; naming a consequence is not. For "traffic" the findings hold no traffic data at all, so any
  statement that a road will be, or is likely to be, slow, jammed, congested, blocked or closed, any
  travel time, delay or speed, and any freeway figure applied to a road that is not a freeway, is
  unsupported; so is a figure or an event "like this one" that is not in the findings. A road that is only
  missing from "worst_spots" or "roads_through_the_worst_spots" (short lists, when
  "worst_spots_and_roads_are_short_lists" is present) is not shown to be clear: saying it is not among them,
  not affected, clear or safe is unsupported unless "roads_the_question_names" says so for that road.
- own_depth: gives a depth for one of our own pooling spots, or attaches a depth from the
  "official" block to one of our spots. Our spots are ranked against each other and have no depth.
  The official figures are a separate authority's and describe their own flood layer.
- unattributed: uses a figure from the "official" block without naming whose layer it is, or
  quotes one of their values without the unit given in that block. The same applies to
  "heat_exposure": the band comes from the authority named in "whose_threshold_this_is", and a
  draft that names a band without naming that authority reports it as though it were our own
  verdict. Where "why_this_authority_and_not_the_local_one" is present, a draft that credits the
  band to the city's own authority is wrong.
- scope: writes about the findings as if they covered the city, a district, or anywhere wider than
  the one drawn box. The box is named in the findings under "area" and it is usually a single
  neighbourhood. "111 spots across Kochi" is wrong when the 111 spots are all inside one box;
  "111 spots in this box" is right. If "area" says the findings cover the whole city, or a list of
  areas set up for it, then writing about exactly that is right, and writing about anywhere wider
  is wrong: "the most populated place in the city" when only four areas were compared is a scope
  error, "the most populated of the four areas compared" is right.
- timing: says when water arrives, how fast it rises or how long it stays. Fill order is an order,
  not a clock.
- dispatch: tells anyone to evacuate, to move, or to be dispatched. This product is preparation
  only.
- overstated: makes the extent sound larger than the findings say - "most of the area" when a few
  spots out of many are ranked top, or similar.
- certainty: presents a forecast or a hypothetical rainfall as something that will happen, or
  states as settled something the findings flag as uncertain or low confidence. Presenting the
  "seismic" design-level shaking as a forecast, a prediction, or a scenario of a particular
  earthquake is the same error: it is a published hazard level, a one in ten chance in fifty years,
  and nothing that has happened or will. The same rule
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


# Words by which a briefing can honestly say the published hazard map jumps. Any one will do: the point
# is that it is said, not how.
SEAM_WORDS = ("abrupt", "jump", "regional model", "separate model", "where models meet", "joined", "seam")


def check_seismic_statements(bundle: dict, briefing: dict) -> list[dict]:
    """What an earthquake briefing must say, checked rather than hoped for, as the drain caveat is.

    Two things, each of which a reader would otherwise draw the wrong conclusion without. The caveat must
    say that no building data is held, because without that a count of people on shaking reads as a count
    of people in danger. And where the published hazard map jumps across the area, the briefing must say
    so: otherwise a band of people on the lower side of the jump reads as ground that shakes less, when it
    is the join between two models and nothing to do with the ground."""
    if "seismic" not in (bundle or {}):          # key presence, not truthiness: an empty block still counts
        return []
    seismic = bundle["seismic"]
    out = []
    if "building" not in (briefing.get("caveat") or "").lower():
        out.append({"part": "caveat", "rule": "unsupported", "found_by": "arithmetic",
                    "problem": "the caveat does not say that no building data is held, so damage and "
                               "casualties are not estimated - without that, a count of people on "
                               "shaking reads as a count of people in danger",
                    "text": briefing.get("caveat", "")})
    if seismic.get("the_published_map_jumps_here"):
        whole = " ".join(t for _, t in parts(briefing)).lower()
        if not any(w in whole for w in SEAM_WORDS):
            out.append({"part": "caveat", "rule": "unsupported", "found_by": "arithmetic",
                        "problem": "the findings say the published hazard map changes abruptly across "
                                   "this area, where separate regional models are joined, and the "
                                   "briefing does not - so people on the lower side of the jump read as "
                                   "ground that shakes less, when it is the join between two models",
                        "text": briefing.get("caveat", "")})
    return out


_NEGATION = re.compile(r"\b(no|not|never|without|none|nothing)\b|n't", re.I)


def check_traffic_statements(bundle: dict, briefing: dict) -> list[dict]:
    """A traffic answer must say the product holds no traffic data. Checked in code, as the drain caveat
    is: without that sentence, rain figures and road names read as a traffic forecast, which is the one
    thing this product cannot give and a model would otherwise be glad to supply."""
    if "traffic" not in (bundle or {}):       # key presence, not truthiness: an empty block still counts
        return []
    sentences = re.split(r"[.!?]", " ".join(t for _, t in parts(briefing)))
    if any("traffic" in s.lower() and _NEGATION.search(s) for s in sentences):
        return []
    return [{"part": "headline", "rule": "unsupported", "found_by": "arithmetic",
             "problem": "a question about traffic must say plainly that this product holds no traffic data; "
                        "without that, the rain figures and road names read as a traffic forecast",
             "text": briefing.get("headline", "")}]


_CALLED_CLEAR = re.compile(r"\bnot (?:among|listed|affected|on the list)\b|\bdoes not (?:appear|run|cross|pass)\b|"
                           r"\bno low spots?\b|\bis (?:clear|safe)\b", re.I)


def check_named_roads(bundle: dict, briefing: dict) -> list[dict]:
    """A road whose own entry says a real length of it lies in low spots that fill at this rainfall must not be
    called clear. Checked in code because it was seen: "not among the worst low spots" for a road the engine had
    filling below the forecast."""
    for r in (bundle or {}).get("roads_the_question_names") or []:
        if not r.get("a_real_length_of_it_is_in_low_spots_that_fill_at_this_rainfall"):
            continue
        for part, text in parts(briefing):
            if any(r["road"].lower() in s.lower() and _CALLED_CLEAR.search(s) for s in re.split(r"[.!?]", text)):
                return [{"part": part, "rule": "unsupported", "found_by": "arithmetic",
                         "problem": f"calls {r['road']} clear or not affected, but the findings have a real length of it "
                                    f"in low spots that fill at this rainfall",
                         "text": text}]
    return []


_ODDS = re.compile(r"\b(?:un)?likely\b|\bprobabl[ye]\b|\bimprobable\b", re.I)


def check_odds(bundle: dict, briefing: dict) -> list[dict]:
    """No odds in words either. The product declines to give a probability ("what is the chance it floods"), so a
    model writing "likely" or "unlikely" is giving one, with nothing behind it. It reads naturally in a chat voice,
    which is why the narrator is told not to and this makes sure. Checked in code: the words are exactly checkable."""
    for part, text in parts(briefing):
        m = _ODDS.search(text)
        if m:
            return [{"part": part, "rule": "certainty", "found_by": "arithmetic",
                     "problem": f"says \"{m.group(0)}\", which is an odds judgment the findings do not hold - say what the "
                                f"forecast and the findings show (\"on this forecast\", \"fills at\") instead",
                     "text": text}]
    return []


_NUMBER_WORD = (r"(?:\d+(?:\.\d+)?|a|an|one|two|three|four|five|six|seven|eight|nine|ten|eleven|twelve|"
                r"twenty[- ]four|forty[- ]eight)")
_NEXT_N_HOURS = re.compile(rf"\bnext\s+{_NUMBER_WORD}[- ]hours?\b|\bcoming\s+{_NUMBER_WORD}[- ]hours?\b", re.I)


def check_window_wording(bundle: dict, briefing: dict) -> list[dict]:
    """'The next N hours' is only true of hours that start now. A forecast window that starts later (the
    wettest stretch of the next two days, tonight, tomorrow) must not be called that, which is what the
    product did for every question before it knew when its window began. Checked in code because it is
    exactly checkable: the findings say when the window starts, and the wording either contradicts it or
    does not."""
    rain = (bundle or {}).get("rainfall") or {}
    starts = rain.get("window_starts_in_hours")
    if not rain.get("is_a_real_forecast") or not starts:
        return []
    for part, text in parts(briefing):
        m = _NEXT_N_HOURS.search(text)
        if m:
            return [{"part": part, "rule": "certainty", "found_by": "arithmetic",
                     "problem": f"says \"{m.group(0)}\", but the rain being described starts in about "
                                f"{starts} hours - say when it starts, or that it is the wettest stretch of "
                                f"the forecast, not the next hours",
                     "text": text}]
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
            + check_official_units(bundle, briefing) + check_seismic_statements(bundle, briefing)
            + check_window_wording(bundle, briefing) + check_traffic_statements(bundle, briefing)
            + check_named_roads(bundle, briefing) + check_odds(bundle, briefing) + check_claims(bundle, briefing, model=model))
