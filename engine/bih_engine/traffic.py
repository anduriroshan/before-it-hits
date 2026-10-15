"""What published studies say rain does to traffic, set beside the roads a rain forecast puts water on.

The product holds no traffic data: no counts, no speeds, no incidents, no closures, live or typical. Asked
"how would this affect traffic?" it used to say so and stop, and a model asked to be more useful would have
made a figure up from its memory of "similar events" - one nobody could check, on a screen where it would
look as solid as the flood figures. Neither is acceptable.

What CAN be said, honestly, is what published empirical studies found for rain of a given strength on roads
of a given kind, and which roads run through this area's low spots. Both are real and sourced. Putting them
side by side is not a prediction that any road will be slow, jammed or closed, and the findings say so.

Everything below is read from the FHWA Road Weather Management Program pages (ops.fhwa.dot.gov), which
summarise the Highway Capacity Manual 2000 and the studies it cites, and from the one study in FHWA's
empirical review that defines "light" and "heavy" rain (Smith et al. 2004, Hampton Roads, Virginia). Three
limits are carried with every use, because each would be misread without it:
  - They are American studies, mostly of freeways. No Singapore or Kerala study was found, so nothing
    here says how traffic there responds.
  - The arterial figure is for WET PAVEMENT, not for rain of any strength, and FHWA gives no rain-intensity
    breakdown for arterials. It is applied to arterials and to nothing else.
  - FHWA's own pages differ slightly at the ends of the ranges (light rain 2 to 13% on one, 3 to 13% on
    another). The ranges are quoted as ranges, never as a point.
"""
from __future__ import annotations

# Rain classes, by the wettest hour. Only Smith et al. (2004) defines them: light 0.01 to 0.25 inch an hour
# (0.25 to 6.4 mm), heavy above 0.25 inch (6.4 mm). Below the light class no study here gives a figure.
LIGHT_FROM_MM_PER_H = 0.25
HEAVY_ABOVE_MM_PER_H = 6.4

# FHWA, "Weather-Related Road Impacts": freeway traffic flow reductions. Percent, as (low, high).
# The light row is published as "Light Rain/Snow", so it is not purely a rain figure.
FREEWAY = {
    "light": {"average_speed": (3, 13), "free_flow_speed": (2, 13), "volume": (5, 10), "capacity": (4, 11)},
    "heavy": {"average_speed": (3, 16), "free_flow_speed": (6, 17), "volume": (14, 14), "capacity": (10, 30)},
}
# FHWA: "speed reductions on arterial routes range from 10 to 25 percent on wet pavement". Not by intensity.
ARTERIAL_WET_PAVEMENT_SPEED = (10, 25)

# OpenStreetMap's own highway classes, mapped to the road kinds the studies measured.
FREEWAY_CLASSES = {"motorway", "motorway_link", "trunk", "trunk_link"}
ARTERIAL_CLASSES = {"primary", "primary_link", "secondary", "secondary_link", "tertiary", "tertiary_link"}

MAX_ROADS = 8
M_PER_DEG_LAT, M_PER_DEG_LON = 110574.0, 111320.0


def road_mix(roads: list, bbox) -> dict:
    """Kilometres of each kind of road inside the box, from the staged OpenStreetMap roads.

    The published effects are for particular kinds of road, so which of them are worth quoting depends on
    which kinds are here. Quoting a freeway figure for an area of local streets, as the first version did,
    puts a number in front of the reader that is about nothing in the answer. Lengths are clipped to the box
    and measured on a local flat approximation, so they are 'about' figures and are only ever used to ask
    whether a kind of road is present, and to say roughly how much."""
    from math import cos, radians

    import numpy as np
    import shapely
    from shapely.geometry import box as _box
    w, s, e, n = bbox
    kx, ky = M_PER_DEG_LON * cos(radians((s + n) / 2)), M_PER_DEG_LAT
    window = _box(w, s, e, n)
    km = {"freeway": 0.0, "arterial": 0.0, "local street": 0.0}
    for r in roads:
        piece = r.geom.intersection(window)
        if not piece.is_empty:
            km[road_type(r.highway)] += shapely.transform(piece, lambda c: c * np.array([kx, ky])).length / 1000.0
    return {k: round(v, 1) for k, v in km.items()}


def rain_class(wettest_hour_mm: float) -> str:
    """'below_light', 'light' or 'heavy', by the studies' own definition and by the wettest hour: the
    hour that sets the speed, not the average that a long dry stretch would dilute. The caller says
    'unknown' itself when there is no rate to classify."""
    if wettest_hour_mm < LIGHT_FROM_MM_PER_H:
        return "below_light"
    return "heavy" if wettest_hour_mm > HEAVY_ABOVE_MM_PER_H else "light"


def road_type(highway: str) -> str:
    if highway in FREEWAY_CLASSES:
        return "freeway"
    if highway in ARTERIAL_CLASSES:
        return "arterial"
    return "local street"


def assess(total_mm: float, hours: float | None, hourly_mm: list[float] | None, roads: list[dict],
           mix: dict | None = None) -> dict:
    """The rain's strength, the published effect for that strength on each kind of road, and the roads.

    `hourly_mm` is the forecast's own hour-by-hour amounts for the window, when there is one; a what-if has
    none, and its rain is taken to fall evenly, which is said. `roads` is the engine's list of roads that
    run through the worst low spots, each with its OSM class and metres. `mix` is the kilometres of each
    kind of road in the whole box (road_mix); with it, only the published effects for kinds of road that are
    actually here are offered. Without it, both are, which is the cautious thing to do when it is not known."""
    even = hourly_mm is None or not hourly_mm
    hours = float(hours) if hours else (float(len(hourly_mm)) if not even else None)
    # The studies are by rain PER HOUR. A what-if that names a rainfall but no duration has no rate, and
    # guessing one (a cloudburst in an hour, or a drizzle over a day) would pick the answer.
    if hours is None:
        wettest, cls = None, "unknown"
    else:
        hours = max(hours, 1.0)
        wettest = (total_mm / hours) if even else max(hourly_mm)
        cls = rain_class(wettest)
    out = {
        "available": True,
        "total_mm": round(float(total_mm), 1),
        "over_hours": round(hours) if hours else None,
        "average_mm_per_hour": round(float(total_mm) / hours, 2) if hours else None,
        "wettest_hour_mm": round(float(wettest), 2) if wettest is not None else None,
        "assumed_even": even,
        "rain_class": cls,
        "light_from_mm_per_hour": LIGHT_FROM_MM_PER_H,
        "heavy_above_mm_per_hour": HEAVY_ABOVE_MM_PER_H,
        "freeway_effect": FREEWAY.get(cls) if (mix is None or mix.get("freeway", 0) > 0) else None,
        "arterial_effect_when_wet": (ARTERIAL_WET_PAVEMENT_SPEED
                                     if cls in ("light", "heavy") and (mix is None or mix.get("arterial", 0) > 0)
                                     else None),
        "road_mix_km": mix,
        "roads": [],
    }
    for r in roads[:MAX_ROADS]:
        kind = road_type(r.get("highway", ""))
        out["roads"].append({"road": r["road"], "class": r.get("highway"), "type": kind,
                             "metres_through_low_spots": round(r.get("length_m", 0))})
    return out
