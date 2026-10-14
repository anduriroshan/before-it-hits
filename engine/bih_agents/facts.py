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
            "note": "A modelled distribution - a satellite-and-census population grid scaled to the "
                    "city's published population, placed on the mapped buildings in each cell, then "
                    "summed inside the worst spots. Rough and by area only; never a claim about any "
                    "one building or home.",
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
        # Two different owners in one block, so each is named. The readings are the provider's; the
        # band a reading falls in belongs to the scale, and crediting the band to the provider
        # reports an American scale as though the weather service had issued it.
        "source": heat_data.get("source", "Google Weather API"),
        "whose_scale_the_level_is_on": "the United States National Weather Service heat-index scale, "
                                       "not the forecast provider and not ours",
        "note": "A forecast at one point in the middle of this box, not a measurement and not a "
                "map of it. Heat is a separate hazard from the pooling above and the two are not "
                "combined into one ranking.",
    }


def add_heat_exposure(bundle: dict, ex: dict) -> None:
    """Append who is exposed to heat, measured against a named authority's published threshold.

    The whole value of this block is that the threshold is someone else's and the briefing has to say
    whose. So `judged_against` is carried as a sentence the Narrator can copy, and where the local
    authority could not be used, the reason travels with it rather than being dropped - a briefing
    that quietly judged Singapore by an American scale without saying so would be the failure mode.
    """
    if not ex or not ex.get("available"):
        return
    block = {
        "whose_threshold_this_is": ex["judged_against"],
        "what_is_being_read": ex["which_reading"],
        "the_bands": [{"band": b["label"], "starts_at_c": tidy(b["at_or_above_c"])}
                      for b in ex.get("bands", [])],
        "source": ex.get("source", "unknown"),
        "note": ex.get("what_this_count_is", ""),
    }
    for key, into in (("why_not_the_local_authority", "why_this_authority_and_not_the_local_one"),
                      ("only_half_checked", "this_threshold_is_only_half_checked")):
        if ex.get(key):
            block[into] = ex[key]
    if "highest_forecast_c" in ex:
        block["highest_forecast_c"] = tidy(ex["highest_forecast_c"])
        block["highest_is_hours_from_now"] = tidy(ex["highest_is_hours_from_now"])
        block["band_the_forecast_reaches"] = ex["band_the_forecast_reaches"]
        block["hours_of_forecast_read"] = tidy(ex["hours_of_forecast_read"])
        block["hours_in_each_band"] = [{"band": b["label"], "hours": tidy(b["hours"])}
                                       for b in ex.get("hours_in_each_band", [])]
    if "asked_about_c" in ex:
        block["the_figure_the_question_gave_c"] = tidy(ex["asked_about_c"])
        block["band_that_figure_reaches"] = ex["band_the_asked_figure_reaches"]
        block["where_that_figure_came_from"] = ex["where_the_asked_figure_came_from"]
    if "people_living_here" in ex:
        block["people_living_here"] = tidy(ex["people_living_here"])
        block["people_source"] = ex.get("people_source", "unknown")
    elif ex.get("people_unavailable"):
        block["people_unavailable"] = ex["people_unavailable"]
    bundle["heat_exposure"] = block


def add_measured_heat(bundle: dict, obs: dict) -> None:
    """Append the authority's own measured heat-stress readings, where a city has a station network.

    Kept apart from the forecast block on purpose: this is a reading someone took, the forecast is a
    model, and the index here is not an air temperature. Collapsing the three into one sentence is
    the mistake this separation exists to prevent."""
    if not obs or not obs.get("available"):
        return
    bundle["measured_heat_stress"] = {
        "nearest_stations": [{"station": r["name"], "wbgt_c": tidy(r["wbgt_c"]),
                              "the_authority_rates_this": r.get("rated"),
                              "km_from_here": tidy(r["km"]), "inside_this_area": r["in_box"]}
                             for r in obs.get("nearest", [])],
        "hottest_nearby_station": obs["hottest_nearby"]["name"],
        "stations_reporting": tidy(obs.get("stations_reporting")),
        "source": obs.get("source", "unknown"),
        "note": obs.get("note", ""),
    }


COMPASS = ["north", "north-east", "east", "south-east", "south", "south-west", "west", "north-west"]


def compass(deg: float) -> str:
    """Eight points, in plain words: 'south-west', not 225 degrees and not SOUTHWEST."""
    return COMPASS[int(round((float(deg) % 360) / 45.0)) % 8]


def add_wind(bundle: dict, heat_data: dict) -> None:
    """Append the wind forecast to a facts bundle: now, a few hours on, and the strongest hour.

    Directions are where the wind blows FROM, as in a weather report, and each reading also says where
    the air is going, because the map's arrows show that and the two are easily swapped. A point
    forecast at the middle of the box, said so. The prose has no digits, for the reason the heat
    notes have none: each would smuggle a small integer into the figures a briefing may quote."""
    hours = [h for h in (heat_data.get("hours") or []) if h]
    if not hours or all(h.get("wind_speed_kmh") is None for h in hours):
        return

    def reading(ahead: int) -> dict:
        h = hours[ahead]
        out = {"hours_from_now": ahead, "speed_kmh": tidy(h.get("wind_speed_kmh"))}
        if h.get("wind_gust_kmh") is not None:
            out["gusts_to_kmh"] = tidy(h["wind_gust_kmh"])
        if h.get("wind_dir_deg") is not None:
            out["blowing_from"] = compass(h["wind_dir_deg"])
            out["blowing_towards"] = compass(h["wind_dir_deg"] + 180)
        return out

    strongest = max(range(len(hours)), key=lambda i: hours[i].get("wind_speed_kmh") or 0)
    bundle["wind"] = {
        "now": reading(0),
        "later": [reading(i) for i in (6, 12, 24) if i < len(hours)],
        "strongest_in_the_forecast": reading(strongest),
        "direction_note": "Directions are where the wind blows FROM, as in a weather report: a wind "
                          "from the south-west carries air towards the north-east. The arrows on the "
                          "map point the way the air is going.",
        "source": heat_data.get("source", "Google Weather API"),
        "note": "A forecast at one point in the middle of this box, not a measurement. The wind "
                "between buildings, trees and water differs from it. Wind is a separate hazard from "
                "the pooling and is not combined with it into one ranking.",
    }


def add_surface_heat(bundle: dict, sh: dict) -> None:
    """Append the satellite picture of how hot the ground itself runs inside the box.

    This is the one heat source here that can say which parts of a box are hotter than others, so
    it is what lets a heat answer name a place instead of repeating one number for the whole area.
    The text says what it is NOT before it says what it is, because it is the thing most easily
    misread: it is a typical season rather than a forecast, and the surface rather than the air, so
    it must never be set beside the heat index as if the two measured the same thing. Written
    without digits for the same reason the dates are kept out of the bundle: each one would smuggle
    a small integer into the set of figures a briefing is allowed to quote.
    """
    if not sh or not sh.get("available"):
        return

    def where(p: dict) -> str:
        road = p.get("nearest_road")
        return f"{road['name']}, about {road['distance_m']} m away" if road else "no named road close by"

    delta = sh["vs_city_median_c"]
    block = {
        "what_this_is": "A satellite picture of how hot the GROUND itself typically runs inside this "
                        "box on a dry-season mid-morning: the surface of roofs, roads, bare ground and "
                        "leaves, not the air. It is a typical season, not today and not a forecast, "
                        "and it can never be compared with the heat index or the air temperature, "
                        "which are different things. Afternoon surfaces run hotter and further apart. "
                        "The sensor sees patches a few blocks across, so it never describes a single "
                        "building or roof, and finer detail than that is smoothing.",
        "most_of_the_ground_here_runs_from_c": tidy(sh["typical_low_c"]),
        "most_of_the_ground_here_runs_to_c": tidy(sh["typical_high_c"]),
        "typical_surface_in_the_box_c": tidy(sh["median_c"]),
        "typical_surface_across_the_whole_city_c": tidy(sh["city_median_c"]),
        "season": sh.get("season"),
        "source": "Landsat satellites (USGS), thermal band, at the scale of a city block",
        "note": "Surface temperature, typical of the season, shown as measured. Always call it the "
                "surface and never the air.",
    }
    if delta > 0:
        block["this_box_runs_hotter_than_the_whole_city_by_c"] = tidy(delta)
    elif delta < 0:
        block["this_box_runs_cooler_than_the_whole_city_by_c"] = tidy(abs(delta))
    if sh.get("hottest"):
        block["hottest_places"] = [
            {"near": where(p), "surface_c": tidy(p["surface_c"]),
             "hotter_than_the_box_typical_by_c": tidy(p["vs_box_median_c"])}
            for p in sh["hottest"]]
    if sh.get("coolest"):
        block["coolest_places"] = [
            {"near": where(p), "surface_c": tidy(p["surface_c"]),
             "cooler_than_the_box_typical_by_c": tidy(abs(p["vs_box_median_c"]))}
            for p in sh["coolest"]]
    bundle["surface_heat"] = block


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
