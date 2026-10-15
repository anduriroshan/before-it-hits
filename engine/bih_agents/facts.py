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
REAL_LENGTH_M = 90     # three cells of the 30 m DeltaDTM both staged cities use; shorter is a pit the data cannot resolve

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


def _plain(text: str) -> str:
    """Lower case, words only, 'rd' and 'ave' spelled out, so 'Bendemeer Rd' finds 'Bendemeer Road'."""
    words = re.sub(r"[^a-z0-9]+", " ", str(text).lower()).split()
    return " " + " ".join({"rd": "road", "ave": "avenue"}.get(w, w) for w in words) + " "


def _roads_named(question: str, result: dict, rainfall_mm) -> list[dict]:
    """The roads the question names, looked up among EVERY road that crosses a low spot in the box.

    `roads_through_the_worst_spots` is only the longest few, so a road missing from it is not shown to be
    clear: a briefing once said a road was "not among the worst low spots" when the engine had a low spot
    on it filling below the forecast. "Will this road flood" is answered here instead: the rain that fills
    low spots along a real length of the road, set beside the rain of this question.

    Why "a real length" and not the least rain on any bit of it: moving the box 100 m moved Bendemeer Road's
    least rain from about 2 mm to about 12 mm, because the first stretch to fill was 55-70 m long, a pit the
    terrain data cannot resolve, and it appears and vanishes with the grid. The rain that puts 90 m of the
    road in filled low spots held between 11 and 15 mm in all eight box positions tried.
    """
    by_road: dict[str, list[dict]] = {}
    for f in (result.get("roads_timeline") or {}).get("features", []):
        if f["properties"].get("name"):
            by_road.setdefault(f["properties"]["name"], []).append(f["properties"])
    left, out = _plain(question), []
    for name, rows in sorted(by_road.items(), key=lambda kv: -len(kv[0])):    # longest first: "A Road East" before "A Road"
        if _plain(name) not in left:
            continue
        left = left.replace(_plain(name), " ")
        worst, metres, enough = max(rows, key=lambda r: r["severity"]), 0.0, None
        for r in sorted(rows, key=lambda r: r["rain_to_flood_mm"]):
            metres += r["length_m"]
            if metres >= REAL_LENGTH_M:
                enough = r["rain_to_flood_mm"]
                break
        sooner = min(r["rain_to_flood_mm"] for r in rows)
        entry = {"road": name, "kind_of_road": worst["highway"],
                 "a_real_length_of_it_is_in_low_spots_that_fill_at_this_rainfall":
                     bool(enough is not None and rainfall_mm is not None and enough <= rainfall_mm),
                 "a_real_length_is_at_least_metres": REAL_LENGTH_M,
                 "highest_pooling_rank_of_the_low_spots_on_it": worst["severity"]}
        if enough is not None:
            entry["least_rain_that_fills_low_spots_along_a_real_length_of_it_mm"] = tidy(enough)
        if enough is None or sooner < enough:
            entry["shorter_bits_of_it_may_fill_with_less_rain_but_the_terrain_data_cannot_resolve_them"] = True
        out.append(entry)
    return out[:3]


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
    routes = screening.get("routes_affected", [])
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
            for r in routes[:MAX_ROADS]
        ],
        "confidence": screening.get("confidence", {}),
        "limits": LIMITS,
    }
    if len(deps) > MAX_SPOTS or len(routes) > MAX_ROADS:
        bundle["worst_spots_and_roads_are_short_lists"] = (
            "worst_spots and roads_through_the_worst_spots hold only the worst few, so a road or place missing "
            "from them is not shown to be clear, safe or unaffected")
    named = _roads_named(question, result, rainfall.get("mm"))
    if named:
        bundle["roads_the_question_names"] = named

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


def _peak_by_day(hours: list, tz: str | None) -> list[dict]:
    """The highest heat index on each local day the forecast touches, so "will it be hotter tomorrow than
    today" can be answered by comparing two figures that are both in the findings instead of by quoting
    one peak that happens to fall on either. Days are the city's own, not UTC's. Today is usually partial:
    the hours already gone are not in a forecast, so each day says how many hours it covers."""
    from datetime import datetime
    from zoneinfo import ZoneInfo
    days: dict = {}
    for h in hours or []:
        t = h.get("time")
        v = h.get("heat_index_c") if h.get("heat_index_c") is not None else h.get("temperature_c")
        if not t or v is None:
            continue
        dt = datetime.fromisoformat(str(t).replace("Z", "+00:00"))
        try:
            dt = dt.astimezone(ZoneInfo(tz)) if tz else dt
        except Exception:
            pass
        days.setdefault(dt.date(), []).append(v)
    names = ("today", "tomorrow", "the day after tomorrow")
    return [{"day": names[i], "highest_heat_index_c": tidy(max(days[d])), "hours_of_forecast_covered": len(days[d])}
            for i, d in enumerate(sorted(days)[:3])]


def add_heat(bundle: dict, heat_data: dict, tz: str | None = None) -> None:
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
        "highest_by_day": _peak_by_day(heat_data.get("hours"), tz),
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


ROMAN = {1: "I", 2: "II", 3: "III", 4: "IV", 5: "V", 6: "VI", 7: "VII", 8: "VIII", 9: "IX", 10: "X"}
# USGS ShakeMap's words for how the shaking is felt. Only what people feel, never what it does to a
# building: this product holds nothing about buildings, so it has no standing to name a damage level.
FELT_AS = {1: "not felt", 2: "weak", 3: "weak", 4: "light", 5: "moderate", 6: "strong",
           7: "very strong", 8: "severe", 9: "violent", 10: "extreme"}


def add_seismic(bundle: dict, ex: dict, magnitude: float | None = None) -> None:
    """Append the published design-level shaking for the ground, and who stands on each level of it.

    Written so the limit travels with the figures. The numbers say how hard the ground is shaken by
    the level the published models call a one in ten chance in fifty years, and how many people stand
    on each level; the prose says what that is NOT (a forecast, a scenario, a damage estimate), because
    a briefing that read these as a prediction of harm would be the most dangerous thing this product
    could write. The prose is digit-free, for the reason the heat notes are."""
    if not ex or not ex.get("available"):
        return

    def level(k: int) -> dict:
        return {"intensity": ROMAN[k], "felt_as": FELT_AS[k]}

    ground = ex["ground"]
    block = {
        "what_this_is": "The shaking that the published national hazard models give this ground at the "
                        "design level: the level with a one in ten chance of being exceeded in fifty "
                        "years. It is a hazard level for planning. It is not a forecast, it is not an "
                        "earthquake that has happened, and it is not a scenario of any particular one.",
        # The same definition as numbers, because "a 10% chance in 50 years" is the standard way to write
        # it and the digits have to be in the findings for a briefing to be allowed to use them.
        "design_level": {
            "chance_of_being_exceeded_percent": tidy(ex["design_level"]["chance_of_being_exceeded_percent"]),
            "within_years": tidy(ex["design_level"]["within_years"]),
            "also_called_the_return_period_in_years": tidy(ex["design_level"]["return_period_years"]),
        },
        "where_each_part_comes_from": {
            "shaking_on_firm_rock": "GEM Global Seismic Hazard Map, GEM Foundation",
            "how_soft_the_ground_is": "USGS global Vs30 map, estimated from the shape of the land "
                                      "(Allen and Wald; Heath and others 2020)",
            "ground_to_shaking": "Borcherdt (1994), as USGS tabulate it",
            "shaking_to_intensity": "Worden and others (2012), as USGS ShakeMap implements it",
        },
        "firm_rock_shaking_as_a_fraction_of_gravity": {"lowest": tidy(ex["rock_pga_g"]["lowest"]),
                                                       "highest": tidy(ex["rock_pga_g"]["highest"])},
        "people_in_the_area": tidy(ex["people_in_area"]),
        "people_by_intensity_on_this_ground": [
            {**level(b["intensity"]), "people": tidy(b["people"])} for b in ex["people_by_intensity"]],
        "intensity_most_people_feel": level(ex["intensity_most_people_feel"]),
        "intensity_on_firm_rock_for_comparison": level(ex["intensity_on_firm_rock"]),
        "ground_makes_shaking_this_many_times_what_firm_rock_would": tidy(ex["ground_amplifies_by"]),
        "the_ground": {
            "typical_speed_m_per_s": tidy(ground["vs30_typical"]),
            "most_people_stand_on_ground_between_m_per_s": [tidy(ground["vs30_most_people_from"]),
                                                            tidy(ground["vs30_most_people_to"])],
            # One decimal, not a whole number: 99.6% rounded to 100 hides the few who stand on firmer
            # ground and reads as "everyone", which is a different claim.
            "share_of_people_by_class": [
                {"class": c["class"], "described_as": c["described_as"],
                 "percent": tidy(round(100 * c["share_of_people"], 1))} for c in ground["classes"]],
        },
        "typical_scatter_in_intensity_units": tidy(ex["scatter_in_intensity"]),
        "scatter_note": "Turning shaking into an intensity is accurate to about this many units either "
                        "way, so the edge between two intensity bands is soft and a place near one could "
                        "fall either side.",
        "the_ground_map_is_coarse": "The ground is estimated from the shape of the land at a scale of "
                                    "about nine hundred metres. In a flat city nearly everything looks "
                                    "alike to that method, and it cannot see a pocket of soft clay or "
                                    "made ground. It separates one part of a city from another only "
                                    "where the land itself differs.",
        "not_in_the_data": "What buildings stand here and how they are built. This product holds none, "
                           "so it says nothing about damage, injury or death, and no magnitude can be "
                           "turned into a casualty count here. Shaking is as far as it goes.",
    }
    if ex.get("published_map_steps_here"):
        block["the_published_map_jumps_here"] = (
            "Across part of this area the published hazard map changes abruptly, from one level to about "
            "double it. A jump that sharp is not the ground: the map is joined from separate regional "
            "models, and the line between two levels is where they meet. Treat it as a feature of the map.")
    if ex.get("people_with_no_ground_estimate"):
        block["people_where_the_ground_map_has_no_estimate"] = tidy(ex["people_with_no_ground_estimate"])
    if magnitude is not None:
        block["the_magnitude_the_question_gave"] = tidy(magnitude)
        block["why_that_does_not_give_shaking_here"] = (
            "How hard the ground shakes at a place depends on how far away the earthquake is and how "
            "deep, which a magnitude alone does not say. Nothing here is a scenario of that earthquake; "
            "the shaking shown is the published design level, which is a different thing.")
    bundle["seismic"] = block


def _range(lo: float, hi: float) -> list | float:
    """A published range as [low, high]; a single published value stays a single value."""
    return tidy(lo) if lo == hi else [tidy(lo), tidy(hi)]


def add_traffic(bundle: dict, tr: dict) -> None:
    """Append what published studies say rain does to traffic, beside the roads that run through this
    area's low spots. This is NOT traffic data and the findings say so first: there is none, so nothing here
    may be turned into a statement that a road will be slow, jammed or closed.

    Written so the three limits that would otherwise be misread travel with the figures: the studies are
    American and mostly of freeways; the arterial figure is for wet pavement and not for rain strength; and
    the effect of rain on traffic and the water standing on a road are different things that must not be
    added. The prose is digit-free, for the reason the heat and seismic notes are."""
    if not tr or not tr.get("available"):
        return
    cls = tr["rain_class"]
    block = {
        "what_this_is": "Not traffic data. A published finding about how rain of this strength changes "
                        "traffic on roads of a given kind in other places, set beside the roads that run "
                        "through this area's low spots. It is never a prediction that any road here will "
                        "be slow, jammed or closed.",
        "no_traffic_data": "This product holds no traffic counts, speeds, incidents or closures for any "
                           "road, live or typical, so it cannot say how a road will behave.",
        "the_rain": {
            "total_mm": tidy(tr["total_mm"]),
            **({"over_hours": tidy(tr["over_hours"]),
                "average_mm_per_hour": tidy(tr["average_mm_per_hour"]),
                "wettest_hour_mm": tidy(tr["wettest_hour_mm"]),
                "rain_is_assumed_to_fall_evenly": bool(tr["assumed_even"])} if cls != "unknown" else {}),
            "which_class_the_studies_would_call_it": {
                "below_light": "lighter than the lightest class the studies measured, so no published "
                               "figure applies to it",
                "light": "light rain",
                "heavy": "heavy rain",
                "unknown": "not known: the studies are by rain per hour and no duration was given, so "
                           "there is no rate to classify. Ask how long the rain lasts rather than guess.",
            }[cls],
            "how_the_studies_define_the_classes": {
                "light_rain_from_mm_per_hour": tidy(tr["light_from_mm_per_hour"]),
                "heavy_rain_above_mm_per_hour": tidy(tr["heavy_above_mm_per_hour"]),
                "defined_by": "one study, Smith and others (2004, Hampton Roads, Virginia); the other "
                              "studies in the review do not define light or heavy",
            },
        },
        **({"roads_in_this_area_by_kind_in_km": {
            "freeways": tidy(tr["road_mix_km"]["freeway"]),
            "arterial_roads": tidy(tr["road_mix_km"]["arterial"]),
            "local_streets": tidy(tr["road_mix_km"]["local street"])}} if tr.get("road_mix_km") else {}),
        "roads_through_this_areas_low_spots": [
            {"road": r["road"], "kind_of_road": r["type"], "metres_through_the_low_spots": tidy(r["metres_through_low_spots"])}
            for r in tr["roads"]],
        "these_are_two_different_things": "Rain slowing traffic is one thing, from published studies of rain. "
                                          "Water standing on a road is another, from this product's terrain "
                                          "screening, which overstates it where drains work. They are not "
                                          "added together, and neither one is traffic data.",
        "limits_of_the_studies": [
            "They are American studies, mostly of freeways. No study from Singapore or Kerala was found, so "
            "nothing here says how traffic in this city responds to rain.",
            "The arterial figure is for wet pavement, not for rain of any strength, and the review gives no "
            "breakdown by rain strength for arterials.",
            "The reviewing agency's own pages differ slightly at the ends of the ranges, so each is quoted "
            "as a range and never as a single figure.",
            "No published figure for a local street was used, so none is given.",
        ],
        "source": "US Federal Highway Administration, Road Weather Management Program, summarising the "
                  "Highway Capacity Manual 2000 and the studies it cites",
    }
    effects = []
    if tr["freeway_effect"]:
        f = tr["freeway_effect"]
        effects.append({
            "applies_to": "freeways only (motorways and trunk roads)",
            "for": "rain of this class",
            "average_speed_falls_by_percent": _range(*f["average_speed"]),
            "free_flow_speed_falls_by_percent": _range(*f["free_flow_speed"]),
            "traffic_volume_falls_by_percent": _range(*f["volume"]),
            "capacity_falls_by_percent": _range(*f["capacity"]),
            "note": "The light-rain row in the source is published as light rain or snow together." if cls == "light"
                    else "",
        })
    if tr["arterial_effect_when_wet"]:
        effects.append({
            "applies_to": "arterial roads only (primary, secondary and tertiary roads)",
            "for": "wet pavement, once rain has wetted the road, not for any rain strength",
            "speed_falls_by_percent": _range(*tr["arterial_effect_when_wet"]),
        })
    if effects:
        block["published_effects"] = effects
    elif cls == "unknown":
        block["published_effects"] = "none: the strength of the rain is not known"
    elif cls == "below_light":
        block["published_effects"] = "none: the rain is lighter than the studies measured"
    else:
        block["published_effects"] = ("none: the published figures are for freeways and arterial roads, and "
                                      "none of those are in this area, only local streets")
    bundle["traffic"] = block


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
