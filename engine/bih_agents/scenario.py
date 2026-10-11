"""A2 Scenario: turn a drawn box plus a free-text question into something the engine can run.

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

SCHEMA = {
    "type": "OBJECT",
    "properties": {
        "reading": {"type": "STRING",
                    "description": "One short line: what you understood the question to ask."},
        "mode": {"type": "STRING", "enum": list(MODES),
                 "description": "forecast = use the real forecast; whatif = a hypothetical "
                                "rainfall; replay = a named past storm."},
        "rainfall_mm": {"type": "NUMBER",
                        "description": "Only if the asker stated a rainfall figure. Otherwise -1. "
                                       "Never estimate one yourself."},
        "rainfall_stated_as": {"type": "STRING",
                               "description": "The exact words from the question that carry the "
                                              "figure, copied character for character. Empty if "
                                              "no figure was stated."},
        "duration_h": {"type": "NUMBER",
                       "description": "Hours the stated rainfall falls over, or -1."},
        "return_period_years": {"type": "INTEGER",
                                "description": "Only if the asker named one, else -1."},
        "focus": {"type": "STRING",
                  "description": "What they actually want to know, in one short line."},
        "audience": {"type": "STRING", "enum": ["officer", "resident"]},
        "needs_clarification": {"type": "BOOLEAN"},
        "question_back": {"type": "STRING",
                          "description": "If and only if the question is ambiguous, the single "
                                         "question to ask back. Otherwise empty."},
    },
    "required": ["reading", "mode", "rainfall_mm", "rainfall_stated_as", "duration_h",
                 "return_period_years", "focus", "audience", "needs_clarification",
                 "question_back"],
}

SYSTEM = """You read a question about flooding in one drawn box on a map and turn it into
parameters. You do not answer it and you do not know anything about the place.

Rules:
- Never invent a rainfall figure. If the asker gave one, copy it and the words it came from. If
  they said something vague like "heavy rain" or "a storm", set rainfall_mm to -1 and let the
  system use the forecast or the slider.
- Choose mode "forecast" only when the question is actually about what is coming: it mentions the
  forecast, tonight, tomorrow, the next few hours or days, or what is expected. "Which spots fill
  first here" is not about the forecast - that is "whatif", and the rainfall already on screen
  will be used. Choosing "forecast" silently replaces a figure the asker set themselves, so only
  choose it when they asked you to.
- Choose "replay" only when they name a specific past event.
- Ask back only when the question could mean two genuinely different analyses. Not knowing a
  rainfall figure is not ambiguity: that has a default.
- audience "resident" only if they clearly ask for wording for the public."""

_NUM = re.compile(r"-?\d[\d,]*(?:\.\d+)?")


def _figure_is_the_askers(spec: dict, question: str) -> bool:
    """Did the rainfall figure really come out of the asker's own words?"""
    quoted = (spec.get("rainfall_stated_as") or "").strip()
    if not quoted or quoted.lower() not in question.lower():
        return False
    return any(abs(float(m.group().replace(",", "")) - spec["rainfall_mm"]) < 1e-6
               for m in _NUM.finditer(quoted))


def read(question: str, *, context: str = "", model: str = llm.FAST_MODEL) -> llm.Reply:
    prompt = f"Question: {question}"
    if context:
        prompt += f"\n\nWhat is already on screen: {context}"
    return llm.ask(prompt, model=model, system=SYSTEM, schema=SCHEMA, temperature=0.0)


def settle(spec: dict, *, question: str, slider_mm: float,
           published_return_periods: list | None = None,
           forecast_peak_mm: float | None = None) -> dict:
    """Turn the model's reading into parameters, keeping where each one came from.

    Nothing here trusts the model with a number. Either the figure is in the asker's own words, or
    it comes from the forecast, or it is the value already on the slider.
    """
    periods = published_return_periods or []
    mode = spec.get("mode") if spec.get("mode") in MODES else "whatif"

    mm, source = float(slider_mm), "the rainfall already set on screen"
    stated = spec.get("rainfall_mm", NOT_STATED)
    if stated is not None and float(stated) > 0 and _figure_is_the_askers(spec, question):
        mm, source = float(stated), "stated in the question"
        mode = "whatif" if mode == "forecast" else mode
    elif mode == "forecast" and forecast_peak_mm is not None:
        mm, source = float(forecast_peak_mm), "the worst forecast window for this box"

    rp = spec.get("return_period_years", -1)
    rp = int(rp) if isinstance(rp, (int, float)) and int(rp) in periods else None

    duration = spec.get("duration_h", NOT_STATED)
    duration = float(duration) if duration is not None and float(duration) > 0 else None

    return {
        "reading": (spec.get("reading") or "").strip(),
        "focus": (spec.get("focus") or "").strip(),
        "audience": spec.get("audience") if spec.get("audience") in ("officer", "resident")
                    else "officer",
        "mode": mode,
        "rainfall_mm": mm,
        "rainfall_source": source,
        "duration_h": duration,
        "return_period_years": rp,
        "ignored_figure": bool(stated and float(stated) > 0
                               and not _figure_is_the_askers(spec, question)),
        "needs_clarification": bool(spec.get("needs_clarification")),
        "question_back": (spec.get("question_back") or "").strip(),
    }
