"""A2 Scenario: turn a message in an ongoing conversation into something the engine can run.

A language model is the right tool here and nowhere near the answer: the input is paraphrase,
colloquial place names and more than one language, while the output is a handful of parameters.

The guard that matters is about figures. If someone asks "what if 120 mm falls in an hour", 120 is
*their* number and using it is fine. If someone asks "what if there is a heavy storm", the model
must not decide what heavy means. So the spec carries the exact words the figure came from, and
`settle` checks those words really are in the question before the figure is used. A figure the
model invented cannot survive that check, and the fallback is the slider the user can see.

Sentinels rather than nulls (-1 for "not stated"): a required number with a sentinel is
unambiguous in every schema dialect, where nullable fields are not.
"""
from __future__ import annotations

import re

from . import llm

NOT_STATED = -1.0

MODES = ("forecast", "whatif", "replay")
ANCHORS = ("now", "tonight", "tomorrow", "wettest")

# What the asker may be running into that this product does not do. Closed lists: the model only READS
# which limit a question meets; what is then said about it is fixed, reviewed text (declines.py), so the
# same question gets the same honest answer every time and a model cannot talk its way past a limit.
#   DECLINE_ONLY        nothing to analyse: say so, and say what it can do instead
#   DECLINE_AND_ANSWER  the limit is part of a question about an area: say it first, then give what the
#                       data does support for that area
#   ROUTED              a real capability that lives somewhere other than the chat's analysis
DECLINE_ONLY = ("long_term", "past_rainfall", "other_hazard", "facilities", "health")
DECLINE_AND_ANSWER = ("evacuation", "single_building", "water_depth", "water_timing", "drainage",
                      "damage_or_casualties", "personal_advice", "hazard_ranking", "probability",
                      "beyond_forecast")
ROUTED = ("news", "history", "preparations", "comparison")
DECLINES = ("none",) + DECLINE_ONLY + DECLINE_AND_ANSWER + ROUTED

TOPICS = ("flood", "traffic", "population", "heat", "air_quality", "wind", "earthquake", "overview")
RAIN_UNIT_MM = {"mm": 1.0, "cm": 10.0, "inches": 25.4}
MAX_WHATIF_MM = 1000.0     # the API's own ceiling for a what-if; the slider stops at 300

SCHEMA = {
    "type": "OBJECT",
    "properties": {
        "reading": {"type": "STRING",
                    "description": "One short line: what you understood the question to ask."},
        "mode": {"type": "STRING", "enum": list(MODES),
                 "description": "forecast = use the real forecast; whatif = a hypothetical "
                                "rainfall; replay = a named past storm."},
        "window_from": {"type": "STRING", "enum": list(ANCHORS),
                        "description": "When the rain being asked about falls. now = the next few "
                                       "hours, today, right now, 'the next N hours'; tonight; "
                                       "tomorrow (including 'tomorrow morning'); wettest = no time "
                                       "was named, or they want the worst case or the peak."},
        "at_hour": {"type": "INTEGER",
                    "description": "Only if the asker named a clock time for the rain ('at 6pm', "
                                   "'around noon', 'at 2am'): that hour on a 24-hour clock, 0 to 23. "
                                   "Otherwise -1."},
        "at_day": {"type": "STRING", "enum": ["today", "tomorrow", "none"],
                   "description": "The day that clock time is on. none if no day was named."},
        "in_hours": {"type": "NUMBER",
                     "description": "Only if the asker said how many hours from now ('in 3 hours', "
                                    "'in an hour'). Otherwise -1."},
        "rainfall_mm": {"type": "NUMBER",
                        "description": "Only if the asker stated a rainfall figure: the number "
                                       "EXACTLY AS WRITTEN, in the unit they wrote it, never "
                                       "converted. Otherwise -1. Never estimate one yourself."},
        "rainfall_unit": {"type": "STRING", "enum": ["mm", "cm", "inches", "none"],
                          "description": "The unit the rainfall figure was written in. none if no "
                                         "figure was stated."},
        "rainfall_stated_as": {"type": "STRING",
                               "description": "The exact words from the question that carry the "
                                              "figure, copied character for character. Empty if "
                                              "no figure was stated."},
        "duration_h": {"type": "NUMBER",
                       "description": "Hours the stated rainfall falls over; or, for a forecast "
                                      "question, the window asked about. -1 if unstated."},
        "return_period_years": {"type": "INTEGER",
                                "description": "Only if the asker named one, else -1."},
        "focus": {"type": "STRING",
                  "description": "What they actually want to know, in one short line."},
        "intent": {"type": "STRING", "enum": ["analyse", "explain"],
                   "description": "analyse = a prediction or finding for an area; explain = what a "
                                  "term means, how this works, how far to trust it, why the last "
                                  "answer said what it said, a greeting or thanks, or anything that "
                                  "is not about a hazard for a place."},
        "topic": {"type": "STRING",
                  "enum": list(TOPICS),
                  "description": "What the question is about. 'flood' = pooling, roads, drainage, "
                                 "water. 'traffic' = jams, congestion, driving, commuting, whether "
                                 "roads will be slow or blocked; which roads WATER reaches or "
                                 "floods is 'flood', not traffic. 'population' = how many people, "
                                 "density. 'heat' = "
                                 "temperature, heat index, humidity, which parts are hotter or "
                                 "cooler, urban heat, hot spots. 'air_quality' = pollution, "
                                 "AQI, air. 'wind' = wind speed, gusts, which way the wind blows. "
                                 "'earthquake' = earthquakes, seismic shaking, tremors, soft ground, "
                                 "how many people would feel or stand on shaking. "
                                 "'overview' = general analysis, multiple things at once, or "
                                 "'analyse this area'."},
        "scope": {"type": "STRING", "enum": ["area", "city"],
                  "description": "city = the message asks about the whole selected city or region "
                                 "(by its name, or 'the whole city', 'this city', or generally). "
                                 "area = about a specific neighbourhood, road, or the drawn box."},
        "place": {"type": "STRING",
                  "description": "A place the asker names that is not the selected area AND not "
                                 "the selected city itself, exactly as written in THIS message. "
                                 "Empty if none, and empty when the named place IS the city."},
        "heat_c": {"type": "NUMBER",
                   "description": "Only if the asker stated a temperature or heat-index figure to "
                                  "test ('what if it hits 41', 'if it reaches 110 F'): the number "
                                  "EXACTLY AS WRITTEN, never converted. Otherwise -1. Never "
                                  "estimate one yourself."},
        "heat_unit": {"type": "STRING", "enum": ["celsius", "fahrenheit", "none"],
                      "description": "The unit the temperature was written in. none if no figure "
                                     "was stated."},
        "heat_stated_as": {"type": "STRING",
                           "description": "The exact words from the question that carry the "
                                          "temperature figure, copied character for character. "
                                          "Empty if no figure was stated."},
        "magnitude": {"type": "NUMBER",
                      "description": "Only if the asker stated an earthquake magnitude ('a magnitude 7', "
                                     "'an 8.1 quake'). Otherwise -1. Never estimate one yourself."},
        "magnitude_stated_as": {"type": "STRING",
                                "description": "The exact words from the question that carry the "
                                               "magnitude, copied character for character. Empty if no "
                                               "magnitude was stated."},
        "earlier_area": {"type": "STRING",
                         "description": "Only when the message points back at an area an earlier "
                                        "answer named, without naming it ('the most populated area', "
                                        "'the one you just said', 'that area'): that area's name "
                                        "copied exactly as the earlier answer wrote it. Otherwise "
                                        "empty."},
        "decline": {"type": "STRING", "enum": list(DECLINES),
                    "description": "Whether the question asks for something this product does not "
                                   "do or keeps elsewhere. none for anything it can screen. "
                                   "evacuation = where to go, whether to evacuate, rescue. "
                                   "single_building = a verdict on one house, school, shop or "
                                   "building. water_depth = how deep the water gets. water_timing = "
                                   "when water arrives or drains, how long flooding lasts, whether it "
                                   "floods at a particular hour of the day. It is not when the RAIN "
                                   "starts or whether it will rain: the forecast gives that, so those "
                                   "are none. Asking which roads or spots are affected FIRST asks for "
                                   "an order, which the product gives, so that is none. probability = "
                                   "the chance or probability "
                                   "of flooding, 'is this a 100-year flood', a return period. drainage "
                                   "= whether drains, sewers or pumps will cope. damage_or_casualties "
                                   "= damage, costs, injuries, deaths, buildings collapsing. health = "
                                   "illness or disease. facilities = hospitals, schools, shelters, "
                                   "power, or services like the MRT, buses, trains, flights. "
                                   "other_hazard = a hazard that is not rain or flooding, heat, air "
                                   "quality, wind or earthquakes (tsunami, landslide, cyclone, "
                                   "drought, wildfire, storm surge...). long_term = climate change, "
                                   "sea-level rise, years or decades ahead, 'this year'. past_rainfall "
                                   "= how much rain FELL earlier (yesterday, last week). "
                                   "beyond_forecast = rain or weather more than about two days ahead "
                                   "(this weekend, next week). personal_advice = is it safe, should I "
                                   "travel, leave, stay or avoid somewhere. hazard_ranking = which "
                                   "hazard or risk is biggest, an overall risk score. comparison = "
                                   "comparing or ranking the cities. news = what is being reported "
                                   "now. history = what happened in a past flood or event. "
                                   "preparations = what to do to prepare, what the city can do, what "
                                   "it would cost."},
        "declined_detail": {"type": "STRING",
                            "description": "The words from the message that name what is declined "
                                           "(the hazard, the facility, the thing asked about), copied "
                                           "exactly. Empty if decline is none."},
        "same_rainfall_as_before": {"type": "BOOLEAN",
                                    "description": "True only if they ask to reuse the previous "
                                                   "answer's storm ('the same rainfall')."},
        "audience": {"type": "STRING", "enum": ["officer", "resident"]},
        "needs_clarification": {"type": "BOOLEAN"},
        "question_back": {"type": "STRING",
                          "description": "If and only if the question is ambiguous, the single "
                                         "question to ask back. Otherwise empty."},
    },
    "required": ["reading", "mode", "window_from", "at_hour", "at_day", "in_hours", "rainfall_mm",
                 "rainfall_unit", "rainfall_stated_as",
                 "duration_h", "return_period_years", "focus", "scope", "intent", "topic", "place",
                 "earlier_area", "heat_c", "heat_unit", "heat_stated_as", "magnitude",
                 "magnitude_stated_as", "decline", "declined_detail",
                 "same_rainfall_as_before", "audience", "needs_clarification", "question_back"],
}

SYSTEM = """You read one message in an ongoing conversation about flooding on a map and turn it
into parameters. You do not answer it and you do not know anything about any place.

You may be given the earlier turns and the area currently selected. Use them only to work out what
the message refers to: "and the roads?" is about the same area and storm as the last answer.
`focus` must restate the message as a complete question in its own right ("which roads are hit
first in Palarivattom") so that nothing downstream needs the history. The selected area is a box on
the map and its name may be a street ("Bendemeer Road"): the name labels the box, it does not narrow
the question to that street. For "here" or "this area" write "in this area" in `focus`, not the name,
unless the message itself asks about that street or place alone.

Rules:
- scope: "city" when the message asks about the whole selected city by name, or says "the whole
  city", "this city", or asks generally about what is expected across the city without naming a
  specific neighbourhood. If the only place they name IS the selected city, that is scope "city"
  and place should be empty - do not treat the city name as a place to look up. "area" otherwise.
- intent "analyse" when they want a data value or finding for an area: how many people live here,
  what is the heat index, what is the air quality, which spots fill first. Anything that needs fresh
  data from the selected area or a named place. intent "explain" when they ask what a term means,
  how the product works, how far to trust it, or why the last answer said what it said, and for a
  greeting ("hi"), thanks, "what can you do", or anything that is not about a hazard for a place
  ("write a poem", "what is the capital of France"). A greeting is never an analysis.
- topic: what data the question is really about. "population" for how many people, density, who
  lives here. "heat" for temperature, heat index, how hot, and which parts of an area are hotter
  or cooler than others, urban heat, hot spots. "air_quality" for AQI, pollution, air. "wind" for
  wind speed, gusts, which way the wind blows, breeze. "flood" for pooling, water, roads, drainage.
  "earthquake" for earthquakes, seismic shaking, tremors, how hard the ground shakes, soft or
  low-lying ground, and how many people would feel or stand on strong shaking. "traffic" for jams,
  congestion, driving, commuting, or whether roads will be slow or blocked. "overview" when the
  question is general ("analyse this area"), covers more than one HAZARD, or is primarily about
  flood risk.
- A question that pairs ONE hazard with how many people ("how hot does it get here and how many
  people live here", "who is exposed to this heat", "how many people live where the air is worst")
  is that hazard's topic, NOT "population" and NOT "overview". Each hazard already carries who lives
  in the area, so naming the hazard is enough. Choose "population" only when people are the whole
  question and no hazard is named.
- place: only a place the message itself names that is not the selected area AND not the selected
  city itself, exactly as written. Never take one from the history. Empty otherwise. A city or country
  that is not the selected city ("Tokyo", "Mumbai") is a place, never scope "city": scope city means the
  selected city only.
- earlier_area: only when the message points back at an area an earlier answer named but does not
  name it itself - "the most populated area", "the one you just said", "that area", "the hottest one".
  Copy that area's name exactly as the earlier answer wrote it. It is a different thing from `place`:
  leave it empty when the message names the place itself, and when it only means the area already
  selected ("and the roads?"). Such a message is about that one area, so its scope is "area".
- heat_c: the same rule as rainfall, for heat. Only when the asker names a temperature or heat-index
  figure to test ("what if it hits 41", "if it reaches 45 degrees"). Copy the exact words into
  heat_stated_as. "If it gets hotter" or "a heat wave" names no figure: leave it at -1 and the real
  forecast is used. Never work out what a heat wave means for this city - an authority decides that.
- magnitude: the same rule again, for an earthquake. Only a magnitude the asker states ("a magnitude 7",
  "an 8.1 quake"), with the exact words in magnitude_stated_as. It is never a rainfall and never a
  temperature, so do not put it in rainfall_mm or heat_c. A question that names a magnitude is still
  the earthquake topic: do not work out what shaking, damage or deaths that magnitude would cause.
- Never invent a rainfall figure. If the asker gave one in THIS message, copy it and the words it
  came from. If they said something vague like "heavy rain" or "a storm", set rainfall_mm to -1.
  If they ask to reuse the last storm ("the same rainfall", "same storm", "as before"), set
  same_rainfall_as_before and leave rainfall_mm at -1: the system copies the figure itself.
- mode "forecast" when the message is about what is coming for real: it mentions the forecast,
  tonight, tomorrow, the next few hours or days, or what is expected - AND for any question in the
  future tense about real conditions with no "what if" in it ("will it flood", "will my house flood",
  "is it safe", "should I avoid this road", "will the roads be slow"). Those are not hypotheticals, and
  answering them with the rainfall left on a slider would invent a storm nobody forecast. "Which spots
  fill first here" is a "whatif": the rainfall already on screen is used. Choosing "forecast" silently
  replaces a figure the asker set themselves, so never choose it when they gave a figure.
- window_from: when the rain falls. "the next N hours", "right now", "today" -> now. "tonight" ->
  tonight. "tomorrow", "tomorrow morning" -> tomorrow. wettest only when no time is named or they ask
  for the worst case or the peak. Never wettest for "the next N hours": that would answer with a storm
  that may be tomorrow.
- at_hour, at_day, in_hours: when the asker names a moment rather than a stretch. "at 6pm" is at_hour 18;
  "tomorrow at noon" is at_hour 12 and at_day tomorrow; "in 3 hours" is in_hours 3. Leave all three at
  their empty values for "tonight", "tomorrow" and "the next N hours": those use window_from. Do not do
  the clock arithmetic yourself; the system converts a clock time to hours in the city's own time.
- rainfall_mm and rainfall_unit: copy the number exactly as written and name the unit written ("4
  inches" -> 4 and inches, "10 cm" -> 10 and cm). Never convert it yourself: the system converts.
  heat_c and heat_unit work the same way ("110 degrees Fahrenheit" -> 110 and fahrenheit).
- decline: use it when the question asks for something in that list that this product does not do or
  keeps elsewhere, and none for anything it can screen. A question can have both a topic and a decline
  ("will my house flood" is topic flood and decline single_building). Pick the closest single value.
  "hospital", "MRT", "buses", "school" are facilities. "tsunami", "landslide", "cyclone", "drought"
  are other_hazard. "what happened in the 2018 floods" is history; "is there any news" is news.
  Copy the words that name the thing into declined_detail, character for character.
- duration_h: the hours a stated rainfall falls over; or, for a forecast question, the window they
  ask about ("the next 6 hours" is 6, "tonight" is 12, "today" or "tomorrow" is 24). -1 if unstated.
- Choose "replay" only when they name a specific past event.
- Ask back only when the message could mean two genuinely different analyses. Not knowing a
  rainfall figure is not ambiguity: that has a default.
- audience "resident" only if they clearly ask for wording for the public."""

MAX_HISTORY = 6


def clean_history(raw) -> list[dict]:
    """The client's record of the conversation, reduced to what A2 may read. It is the user's own
    session, so nothing here is trusted as evidence - it only helps resolve what a message refers
    to - but it is still bounded and typed so a malformed one cannot reach a prompt as-is."""
    out = []
    for t in (raw or [])[-MAX_HISTORY:]:
        if not isinstance(t, dict):
            continue

        def txt(k, n=240):
            v = t.get(k)
            return " ".join(str(v).split())[:n] if v else None

        def num(k, lo, hi):
            v = t.get(k)
            ok = isinstance(v, (int, float)) and not isinstance(v, bool) and lo <= v <= hi
            return float(v) if ok else None

        out.append({"q": txt("q") or "", "area": txt("area", 80), "mode": txt("mode", 12),
                    "rainfall_mm": num("rainfall_mm", 0, 1000), "duration_h": num("duration_h", 0, 240),
                    "headline": txt("headline")})
    return out


def _history_block(history: list[dict]) -> str:
    lines = []
    for t in history:
        bits = [f'asked: "{t["q"]}"']
        if t.get("area"):
            bits.append(f'area: {t["area"]}')
        if t.get("rainfall_mm") is not None:
            r = f'rainfall used: {t["rainfall_mm"]:g} mm'
            if t.get("duration_h"):
                r += f' over {t["duration_h"]:g} h'
            if t.get("mode"):
                r += f' ({t["mode"]})'
            bits.append(r)
        if t.get("headline"):
            bits.append(f'answered: "{t["headline"]}"')
        lines.append("- " + "; ".join(bits))
    return "\n".join(lines)


_NUM = re.compile(r"-?\d[\d,]*(?:\.\d+)?")


def _figure_is_the_askers(spec: dict, question: str, *, value: str = "rainfall_mm",
                          words: str = "rainfall_stated_as") -> bool:
    """Did this figure really come out of the asker's own words?

    Used for every number a question may carry - a rainfall, a temperature - because the rule is the
    same for all of them: the model must quote the words the figure came from, those words must be in
    the message, and the number must be in those words. A figure the model invented cannot pass.
    """
    quoted = (spec.get(words) or "").strip()
    if not quoted or quoted.lower() not in question.lower():
        return False
    return any(abs(float(m.group().replace(",", "")) - spec[value]) < 1e-6
               for m in _NUM.finditer(quoted))


def read(question: str, *, history: list | None = None, area_label: str | None = None,
         region_title: str | None = None, model: str = llm.FAST_MODEL) -> llm.Reply:
    history = clean_history(history)
    prompt = f"Selected city: {region_title or 'unknown'}\n"
    prompt += f"Selected area: {area_label or 'none'}\n"
    if history:
        prompt += f"Earlier in this conversation (oldest first):\n{_history_block(history)}\n"
    prompt += f"\nMessage: {question}"
    return llm.ask(prompt, model=model, system=SYSTEM, schema=SCHEMA, temperature=0.0)


def _place_is_in_the_message(place: str, question: str) -> bool:
    """A named place must literally be in the message. The model may not carry one over from the
    history or make one up, because a place decides which part of the map gets analysed."""
    return bool(place) and place.lower() in " ".join(question.lower().split())


def _declined_detail(spec: dict, question: str) -> str:
    """The words that name what is being declined, echoed back to the asker ("I don't screen tsunamis").
    Echoed text must be the asker's own, so it has to be in the message, character for character."""
    detail = " ".join(str(spec.get("declined_detail") or "").split())[:60]
    detail = "".join(c for c in detail if c.isalnum() or c in " .,'-&()/")
    return detail if detail and detail.lower() in " ".join(question.lower().split()) else ""


def _clean_place(spec: dict, question: str) -> str:
    place = " ".join(str(spec.get("place") or "").split())[:80]
    place = "".join(c for c in place if c.isalnum() or c in " .,'-&()/")
    return place if _place_is_in_the_message(place, question) else ""


def _earlier_area(spec: dict, history: list[dict], labels: list[str]) -> str:
    """An area the message points back at ("the most populated one"). The model only reads the
    reference; code decides it is real: it has to be one of the city's own preset areas, and the
    conversation has to have said it. What comes back is the preset's own spelling, never the model's."""
    named = " ".join(str(spec.get("earlier_area") or "").split()).lower()
    said = " ".join(str(t.get(k) or "") for t in history for k in ("q", "area", "headline")).lower()
    return next((lb for lb in labels if lb.lower() == named and named in said), "")


def settle(spec: dict, *, question: str, slider_mm: float,
           published_return_periods: list | None = None,
           forecast_peak_mm: float | None = None,
           history: list | None = None, preset_labels: list | None = None) -> dict:
    """Turn the model's reading into parameters, keeping where each one came from.

    Nothing here trusts the model with a number. Either the figure is in the asker's own words, or
    it comes from the forecast, or it is carried over by *code* from the previous answer, or it is
    the value already on the slider.
    """
    periods = published_return_periods or []
    history = clean_history(history)
    mode = spec.get("mode") if spec.get("mode") in MODES else "whatif"

    mm, source = float(slider_mm), "the rainfall already set on screen"
    stated = spec.get("rainfall_mm", NOT_STATED)
    figure_ok = stated is not None and float(stated) > 0 and _figure_is_the_askers(spec, question)
    # The model copies the number as written ("4" and "inches"); converting it is arithmetic, and
    # arithmetic is code's. Without this "4 inches" was dropped as "not in your words" and the slider's
    # rainfall was answered silently instead.
    asked_mm = float(stated) * RAIN_UNIT_MM.get(spec.get("rainfall_unit"), 1.0) if figure_ok else None

    duration = spec.get("duration_h", NOT_STATED)
    duration = float(duration) if duration is not None and float(duration) > 0 else None

    last = next((t for t in reversed(history) if t.get("rainfall_mm") is not None), None)
    reuse = bool(spec.get("same_rainfall_as_before")) and last is not None and not figure_ok

    if figure_ok:
        mm, source = asked_mm, "stated in the question"
        mode = "whatif" if mode == "forecast" else mode
    elif reuse:
        if last.get("mode") == "forecast":
            # "The same storm" after a forecast means the same kind of prediction for the new place,
            # which is a fresh live forecast, not last time's number pasted onto it.
            mode, source = "forecast", "the live forecast, as in the previous answer"
        else:
            mm, source = float(last["rainfall_mm"]), "the same rainfall as the previous answer"
            mode = "whatif"
            duration = duration or last.get("duration_h")
    elif mode == "forecast" and forecast_peak_mm is not None:
        mm, source = float(forecast_peak_mm), "the worst forecast window for this box"

    rp = spec.get("return_period_years", -1)
    rp = int(rp) if isinstance(rp, (int, float)) and int(rp) in periods else None

    # A temperature to test instead of the forecast, and only ever the asker's own.
    stated_heat = spec.get("heat_c", NOT_STATED)
    heat_ok = (stated_heat is not None and float(stated_heat) > 0
               and _figure_is_the_askers(spec, question, value="heat_c", words="heat_stated_as"))
    heat_c = None
    if heat_ok:
        heat_c = ((float(stated_heat) - 32.0) * 5.0 / 9.0 if spec.get("heat_unit") == "fahrenheit"
                  else float(stated_heat))
    stated_mag = spec.get("magnitude", NOT_STATED)
    mag_ok = (stated_mag is not None and 0 < float(stated_mag) < 11
              and _figure_is_the_askers(spec, question, value="magnitude", words="magnitude_stated_as"))

    topic = spec.get("topic") if spec.get("topic") in TOPICS else "overview"
    decline = spec.get("decline") if spec.get("decline") in DECLINES else "none"

    # A moment named in the question. The model read it; code will do the arithmetic. Anything outside a
    # clock or the 48 hours of forecast is dropped rather than trusted.
    at_hour, in_hours = spec.get("at_hour", -1), spec.get("in_hours", NOT_STATED)
    at_hour = int(at_hour) if isinstance(at_hour, (int, float)) and 0 <= int(at_hour) <= 23 else None
    in_hours = float(in_hours) if isinstance(in_hours, (int, float)) and 0 < float(in_hours) <= 47 else None
    starts_at = None
    if in_hours is not None:
        starts_at = {"in_hours": in_hours}
    elif at_hour is not None:
        day = spec.get("at_day") if spec.get("at_day") in ("today", "tomorrow") else "none"
        starts_at = {"hour": at_hour, "day": day}

    return {
        "reading": (spec.get("reading") or "").strip(),
        "focus": (spec.get("focus") or "").strip(),
        "scope": "city" if spec.get("scope") == "city" else "area",
        "intent": "explain" if spec.get("intent") == "explain" else "analyse",
        "topic": topic,
        "place": _clean_place(spec, question),
        "earlier_area": _earlier_area(spec, history, preset_labels or []),
        "heat_c": heat_c,
        "heat_asked_as": (spec.get("heat_stated_as") or "").strip() if heat_ok else "",
        "magnitude": float(stated_mag) if mag_ok else None,
        "window_from": "later" if starts_at else (spec.get("window_from") if spec.get("window_from") in ANCHORS
                                                   else "wettest"),
        "starts_at": starts_at,
        "decline": decline,
        "declined_detail": _declined_detail(spec, question) if decline != "none" else "",
        # A figure past anything this product runs is refused out loud, not run and not quietly capped.
        "out_of_range": ({"asked": (spec.get("rainfall_stated_as") or "").strip(), "limit_mm": MAX_WHATIF_MM}
                         if figure_ok and asked_mm > MAX_WHATIF_MM else None),
        "rainfall_asked_as": (spec.get("rainfall_stated_as") or "").strip() if figure_ok else "",
        "reused_previous_rainfall": reuse,
        "audience": spec.get("audience") if spec.get("audience") in ("officer", "resident")
                    else "officer",
        "mode": mode,
        "rainfall_mm": mm,
        "rainfall_source": source,
        "duration_h": duration,
        "return_period_years": rp,
        "ignored_figure": bool(stated and float(stated) > 0 and not figure_ok),
        "needs_clarification": bool(spec.get("needs_clarification")),
        "question_back": (spec.get("question_back") or "").strip(),
    }
