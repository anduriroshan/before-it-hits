"""The facts bundle: the only thing a language model is ever shown about a result.

Everything the Narrator writes has to come from here, and the Verifier's hard check is simply
this: a number may appear in the briefing only if it appears in the bundle that produced it.
That makes "the model never produces a figure" an auditable property of the code rather than an
instruction in a prompt that may or may not be followed.

Two things are deliberately kept out of the bundle:

  - Any depth in metres from our own model. Depths exist inside the engine only, to drop
    depressions shallower than the terrain error. `assert_no_own_depth` fails loudly if one leaks
    in, because a leak here would be invisible everywhere else.
  - Dates and version strings. They are real provenance and the UI shows them, but each one
    smuggles small integers into the allowed set and loosens the check for no benefit.

The authority's own depths *are* included, under `official`, labelled with whose they are. That is
the validated tier, and quoting it as published is the whole point of having it.
"""
from __future__ import annotations

import re

# How far a figure may be rounded before it stops being the same figure. The Narrator may say
# "nearly 3 times" for 2.78; it may not say 4.
_ROUND_DP = (0, 1, 2)
_TOL = 1e-9

_NUMBER = re.compile(r"-?\d[\d,]*(?:\.\d+)?")

MAX_SPOTS = 6     # the worst few, not a listing: a briefing nobody reads is not honest either
MAX_ROADS = 5

LIMITS = [
    "No depth is claimed for our own spots: at this terrain resolution the vertical error is the "
    "same size as the depth we would be quoting, so spots are ranked against each other instead.",
    "No storm-drain data is public anywhere, so pooling is overstated wherever drains work.",
    "Fill order is an order, not a clock. It says which spot fills first, never when.",
    "Nothing here is dispatch or evacuation advice. It is for preparation only.",
]


def tidy(value):
    """A whole number is written whole. The Narrator copies figures exactly, so 100.0 in the
    bundle becomes "100.0 mm" in a sentence, and that reads like machine output."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return value
    return int(value) if float(value).is_integer() else round(float(value), 2)


def _numbers_in(obj) -> set[float]:
    """Every number reachable in the bundle, including inside strings and keys."""
    out: set[float] = set()
    if isinstance(obj, bool) or obj is None:
        return out                                    # a flag is not a figure; True is not 1
    if isinstance(obj, (int, float)):
        out.add(float(obj))
    elif isinstance(obj, str):
        for m in _NUMBER.finditer(obj):
            try:
                out.add(float(m.group().replace(",", "")))
            except ValueError:
                pass
    elif isinstance(obj, dict):
        for k, v in obj.items():
            out |= _numbers_in(k)
            out |= _numbers_in(v)
    elif isinstance(obj, (list, tuple)):
        for v in obj:
            out |= _numbers_in(v)
    return out


def allowed_numbers(bundle: dict) -> set[float]:
    return _numbers_in(bundle)


def traces_to_facts(value: float, allowed: set[float]) -> bool:
    """Is this figure one of the facts, possibly rounded or written as a percentage?"""
    for a in allowed:
        candidates = [a, -a]
        if 0.0 < a < 1.0:
            candidates.append(a * 100.0)              # 0.214 may be written as 21.4%
        for c in candidates:
            if abs(value - c) < _TOL:
                return True
            for dp in _ROUND_DP:
                if abs(value - round(c, dp)) < _TOL:
                    return True
    return False


def numbers_in_text(text: str) -> list[float]:
    out = []
    for m in _NUMBER.finditer(text):
        try:
            out.append(float(m.group().replace(",", "")))
        except ValueError:
            pass
    return out


def untraceable_numbers(text: str, allowed: set[float]) -> list[float]:
    """Figures in the text that no fact supports. The hard guarantee of the whole agent layer."""
    return [n for n in numbers_in_text(text) if not traces_to_facts(n, allowed)]


def assert_no_own_depth(bundle: dict) -> None:
    """Our depths must never reach a prompt, so a leak fails here rather than on stage."""
    def walk(obj, path=""):
        if isinstance(obj, dict):
            for k, v in obj.items():
                p = f"{path}.{k}"
                if "depth" in str(k).lower() and not path.startswith(".official"):
                    raise AssertionError(f"our own depth reached the facts bundle at {p}")
                walk(v, p)
        elif isinstance(obj, list):
            for i, v in enumerate(obj):
                walk(v, f"{path}[{i}]")
    walk(bundle)


def build(result: dict, *, question: str, rainfall: dict, region_title: str | None = None) -> dict:
    """Reduce a full engine result to the few facts a briefing can be written from.

    `rainfall` is the forcing as the Coordinator settled it: how much, over what window, and where
    the figure came from - a forecast, the user's own words, or the slider.
    """
    screening = result.get("screening", {})
    feats = result.get("geojson", {}).get("features", [])
    grid = result.get("grid", {})

    by_id: dict[str, dict] = {}
    for f in feats:
        by_id.setdefault(f["properties"]["id"], f["properties"])
    deps = screening.get("depressions", [])
    fills = sum(1 for p in by_id.values() if p.get("fills_at_rainfall"))
    top = [d for d in deps if d["pooling_index"] >= 4]

    worst = []
    for d in deps[:MAX_SPOTS]:
        p = by_id.get(d["id"], {})
        road = d.get("nearest_road")
        worst.append({
            "id": d["id"],
            # Where it is, in words an officer can act on. Absent when no named road is close
            # enough to be a landmark rather than decoration.
            "nearest_named_road": None if not road else
                f"{road['name']}, about {road['distance_m']} m away",
            "pooling_rank": d["pooling_index"],
            "fill_order": d["fill_order"],
            "land_draining_into_it_ha": d["contributing_ha"],
            "fills_at_this_rainfall": bool(p.get("fills_at_rainfall", False)),
            "below_terrain_error": bool(p.get("low_confidence", False)),
        })

    bundle: dict = {
        "question": question,
        "area": {
            # Spelled out because it is the overreach a briefing slides into on its own: every
            # finding below describes one drawn box, never the city the box happens to sit in.
            "what_these_findings_cover": "one box drawn on the map, usually a single "
                                         "neighbourhood, and nothing outside it",
            "box_km2": tidy(grid.get("area_km2")),
            "the_box_is_inside": region_title,
            "analysis_grid_m": tidy(grid.get("res_m")),
            "pooling_rank_scale": "1 is the least water trapped, 5 the most, within this box only",
        },
        "rainfall": {k: tidy(v) for k, v in rainfall.items()},
        "pooling": {
            "spots_found": len(deps),
            "spots_ranked_4_or_5": len(top),
            "spots_that_fill_at_this_rainfall": fills,
            "spots_left_out": len(screening.get("excluded", [])),
            "worst_spots": worst,
        },
        "roads_through_the_worst_spots": [
            {"road": r["road"], "kind": r["highway"], "segments": r["segments"],
             "length_m": r["length_m"]}
            for r in screening.get("routes_affected", [])[:MAX_ROADS]
        ],
        "confidence": screening.get("confidence", {}),
        "limits": LIMITS,
    }

    official = result.get("official")
    if official:
        # Key names are prompt text: the Narrator reads them literally, and a field called
        # `share_..._pct` came back in a briefing as "a 1.2 pct share of box". They are written
        # here the way a sentence would want them.
        raw_units = str(official["units"])
        inferred = "inferred" in raw_units.lower()
        bundle["official"] = {
            "whose": official["source"],
            "layer": str(official["return_period_years"]) + "-year return period",
            "units": raw_units.split(" (")[0],          # "cm", said once in a sentence
            "units_are_inferred": inferred,
            "units_note": raw_units,
            # A percentage rather than a fraction: 0.0122 read out in a briefing is noise, and the
            # arithmetic check accepts either form anyway.
            "percent_of_the_box_they_call_inundated": round(official["inundated_share"] * 100, 1),
            "their_deepest_value_in_box": official["deepest"],
            "their_median_value_where_wet": official["median_where_wet"],
            "note": "Published by the authority and shown unchanged. These are their depths, "
                    "never ours, and they are not attached to any of our spots.",
        }
    elif (result.get("region") or {}).get("official") is None:
        bundle["official"] = {
            "whose": None,
            "note": "No authority publishes a depth-valued flood layer here, which is the normal "
                    "case nearly everywhere.",
        }

    exposure = result.get("exposure")
    if exposure and exposure.get("available"):
        bundle["people"] = {
            "roughly_in_the_worst_spots_that_fill_at_this_rainfall": exposure["people_in_worst_spots"],
            "roughly_in_the_whole_box": exposure["people_in_box"],
            "note": "A modelled distribution - the city's published population spread across "
                    "building footprints by area, then summed inside the worst spots. Rough and "
                    "by area only; never a claim about any one building or home.",
        }

    agreement = result.get("agreement")
    if agreement:
        bundle["agreement"] = {
            "our_top_spots_checked": agreement["checked"],
            "of_those_inside_their_flood_extent": agreement["inside"],
            "times_better_than_chance": agreement["enrichment"],
            "percent_of_the_box_inundated_at_this_return_period": round(agreement["baseline"] * 100, 1),
            "still_tells_a_good_ranking_from_a_bad_one": agreement["discriminating"],
            "note": agreement["note"],
        }

    missing = (result.get("region") or {}).get("not_available") or []
    if missing:
        bundle["not_staged_for_this_city"] = missing

    assert_no_own_depth(bundle)
    return bundle


def add_heat(bundle: dict, heat_data: dict) -> None:
    """Append heat conditions to a facts bundle. The Verifier's number check covers these
    automatically — every number must trace to the bundle."""
    cur = heat_data.get("current") or {}
    peak = heat_data.get("peak") or {}
    cur_level = heat_data.get("current_level") or {}
    peak_level = heat_data.get("peak_level") or {}

    bundle["heat"] = {
        "heat_index_c": tidy(cur.get("heat_index_c")),
        "temperature_c": tidy(cur.get("temperature_c")),
        "humidity_pct": tidy(cur.get("humidity_pct")),
        "wind_speed_kmh": tidy(cur.get("wind_speed_kmh")),
        "level": cur_level.get("label", "Normal"),
        "peak_heat_index_c": tidy(peak.get("heat_index_c") or peak.get("temperature_c")),
        "peak_level": peak_level.get("label", "Normal"),
        "source": heat_data.get("source", "Google Weather API"),
        "note": "A forecast at one point in the middle of this box, not a measurement and not a "
                "map of it. Heat is a separate hazard from the pooling above and the two are not "
                "combined into one ranking.",
    }


def add_air_quality(bundle: dict, aq_data: dict) -> None:
    """Append air quality conditions to a facts bundle."""
    bundle["air_quality"] = {
        "aqi": tidy(aq_data.get("aqi")),
        "category": aq_data.get("category", "Unknown"),
        "dominant_pollutant": aq_data.get("dominant_pollutant"),
        "source": aq_data.get("source", "Google Air Quality API"),
        "note": "A regional reading from the provider, shown as published. It does not vary at "
                "street or building level, so it describes the air over this box rather than "
                "anything about the box itself, and it is a separate hazard from the pooling.",
    }
    polls = aq_data.get("pollutants", [])
    if polls:
        bundle["air_quality"]["pollutants"] = [
            {"name": p.get("display_name", p.get("code", "")),
             "concentration": tidy(p.get("concentration")),
             "unit": p.get("unit", "")}
            for p in polls[:5]
        ]
