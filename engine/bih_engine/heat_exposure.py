"""Who is exposed to dangerous heat, measured against a named authority's own threshold.

Three rules shape this module, and each is a thing a language model would get wrong if asked.

**The threshold is never ours.** It is the one the city's own weather authority publishes, and the
product names whose it is every time. Kochi is judged against IMD's heat-wave criterion, Singapore
against NEA's heat-stress bands. Inventing a "danger temperature" would be exactly the kind of
authored number this product exists to refuse, and the two cities do not even use the same index.

**A threshold we cannot fully check is said to be half-checked.** IMD declare a heat wave on the
day's maximum temperature AND its departure from that station's long-run normal. No normal is
staged, so this never declares a heat wave: it reports the half it can test and says the other half
is unchecked. NEA rate heat stress on the wet-bulb globe temperature, which needs solar radiation
the hourly forecast does not carry - so rather than converting one index into another, the forecast
is reported on a named forecast scale and NEA's own measured readings are shown beside it.

**This is exposure, not harm.** The count is how many people live where the forecast crosses a
published threshold. It is never how many will fall ill: that needs health data this product does
not have, and a model guessing it would be the most dangerous number on the screen.
"""
from __future__ import annotations

from . import heat, popgrid

# Used when a city has no authority staged, or when its authority's index cannot be read from a
# forecast. Named in the output every time, so a briefing can never imply the local authority said
# it. These are the bands heat.HEAT_LEVELS already classifies conditions by.
FALLBACK = {
    "authority": "the United States National Weather Service",
    "measure": "heat_index_c",
    "measure_label": "the heat index",
    "bands": [{"label": lv["label"].lower(), "min_c": lv["min_c"]}
              for lv in heat.HEAT_LEVELS if lv["min_c"] > 0],
    "source": "National Weather Service heat index categories",
}


def _standard(region):
    return ((getattr(region, "manifest", {}) or {}).get("heat_standard") or {}) if region else {}


def band_for(value: float | None, bands: list[dict]) -> str | None:
    """The band a reading falls in, or None when it is below the lowest one. Bands are tried from
    the highest down, so a reading only ever matches the most serious one it reaches."""
    if value is None:
        return None
    for b in sorted(bands, key=lambda b: -b["min_c"]):
        if value >= b["min_c"]:
            return b["label"]
    return None


def choose_standard(region, hours: list[dict]) -> tuple[dict, str | None]:
    """The standard to judge this forecast by, and why it is not the local one where it is not.

    A city's authority is used only when the forecast actually carries the quantity that authority
    rates. Singapore's does not - WBGT needs solar radiation - and the honest answer there is to
    name a scale the forecast CAN be read on, not to convert between two different indices.
    """
    std = _standard(region)
    if not std:
        return FALLBACK, "No local heat authority is staged for this city."
    measure = std.get("measure")
    if any(h.get(measure) is not None for h in hours):
        return std, None
    return FALLBACK, std.get("incomplete") or (
        f"The forecast does not carry {std.get('measure_label', 'that reading')}, "
        f"which is what {std.get('authority', 'the local authority')} rate heat stress on.")


def assess(region, bbox, hours: list[dict], *, stated_c: float | None = None) -> dict:
    """How hot it gets here against a published threshold, and how many people live here.

    `stated_c` is a figure the asker put in their own words ("what if it hits 41"). It is tested in
    place of the forecast peak and labelled as theirs, never as a prediction. The caller is
    responsible for having checked the figure really came from the question.
    """
    hours = [h for h in (hours or []) if h]
    std, fell_back = choose_standard(region, hours)
    measure, bands = std["measure"], std["bands"]
    values = [(i, h[measure]) for i, h in enumerate(hours) if h.get(measure) is not None]

    out: dict = {
        "available": bool(values) or stated_c is not None,
        "judged_against": std["authority"],
        "which_reading": std["measure_label"],
        "source": std["source"],
        "bands": [{"label": b["label"], "at_or_above_c": b["min_c"]} for b in bands],
    }
    if fell_back:
        out["why_not_the_local_authority"] = fell_back
    elif std.get("incomplete"):
        out["only_half_checked"] = std["incomplete"]

    if values:
        i, peak = max(values, key=lambda v: v[1])
        out["hours_of_forecast_read"] = len(values)
        out["highest_forecast_c"] = round(float(peak), 1)
        out["highest_is_hours_from_now"] = i
        out["band_the_forecast_reaches"] = band_for(peak, bands) or "none of the bands below"
        out["hours_in_each_band"] = [
            {"label": b["label"], "hours": sum(1 for _, v in values if v >= b["min_c"])}
            for b in sorted(bands, key=lambda b: -b["min_c"])]

    if stated_c is not None:
        out["asked_about_c"] = round(float(stated_c), 1)
        out["band_the_asked_figure_reaches"] = band_for(stated_c, bands) or "none of the bands below"
        out["where_the_asked_figure_came_from"] = "stated in the question, not forecast"

    if bbox is not None:
        r = popgrid.density_for(region, tuple(bbox))
        if r.get("available"):
            out["people_living_here"] = r["people_in_area"]
            out["people_source"] = r.get("source", "unknown")
        else:
            out["people_unavailable"] = r.get("reason", "no population estimate for this area")

    out["what_this_count_is"] = (
        "Everyone the population grid places inside this area. The band describes the forecast for "
        "the area, so this is how many people live where that forecast applies - not a count of "
        "people individually above a threshold, and never a count of anyone who will fall ill.")
    return out
