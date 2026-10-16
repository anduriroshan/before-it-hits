"""The Watcher (plan F14): on a schedule, check each staged area's live forecast against the terrain engine and
raise an alert when the wettest forecast would pool water in a TOP-ranked low spot.

It is a PREPARATION trigger for a city officer - "the forecast now crosses the line where your worst spots
start to fill; prepare them first" - never an evacuation order, never a push to a resident, and never a water
depth. Every figure in an alert is the engine's own, like everywhere else in the product.
"""
from __future__ import annotations

from datetime import datetime, timezone

from bih_engine import analyse_area, forecast

TOP_RANK = 4          # a spot ranked 4 or 5 holds enough water that an officer would act on it
HIGH_FROM = 10        # this many top spots filling is a "high" watch rather than a plain one


def evaluate_area(region, label: str, bbox) -> dict | None:
    """One alert dict when the wettest 24-hour forecast fills a top-ranked spot in this area, else None."""
    try:
        f = forecast.for_bbox(tuple(bbox), hours=48)
    except forecast.ForecastUnavailable:
        return None
    w = forecast.pick_window(f.as_dict(), 24, "wettest")
    if not w:
        return None
    mm = w["mm"]
    res = analyse_area(tuple(bbox), rainfall_mm=mm, res_m=10.0, region=region)
    res.pop("debug", None)
    res.pop("debug_excluded", None)
    props = [feat["properties"] for feat in res.get("geojson", {}).get("features", [])]
    top_fill = [p for p in props if p.get("fills_at_rainfall") and p.get("pooling_index", 0) >= TOP_RANK]
    if not top_fill:
        return None                       # below the threshold: no alert, which is the common and correct case
    fills = sum(1 for p in props if p.get("fills_at_rainfall"))
    roads = [r["road"] for r in res["screening"].get("routes_affected", [])[:3]]
    ex = res.get("exposure") or {}
    people = ex.get("people_in_worst_spots") if ex.get("available") else None
    return {
        "region": region.name,
        "area": label,
        "forecast_mm": mm,
        "window_hours": int(w["hours"]),
        "starts_in_hours": w["starts_in_hours"],
        "top_spots_that_fill": len(top_fill),
        "spots_that_fill": fills,
        "roads_through_worst_spots": roads,
        "roughly_people_in_worst_spots": people,
        "severity": "high" if len(top_fill) >= HIGH_FROM else "watch",
        "message": (f"Wettest forecast for {label} is {mm} mm over {int(w['hours'])} h: it would pool water in "
                    f"{len(top_fill)} top-ranked low spots. Prepare the worst spots first."),
        "issued_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "note": "A preparation trigger from the live forecast and a terrain screening, not an evacuation order "
                "and not a water depth. Pooling is overstated where stormwater drains work.",
    }


def evaluate(regions) -> list[dict]:
    """Run the check over every preset area of every staged region. `regions` is an iterable of Region."""
    out = []
    for r in regions:
        for p in getattr(r, "presets", []):
            try:
                alert = evaluate_area(r, p["label"], p["bbox"])
            except Exception:             # one bad area must not stop the sweep
                alert = None
            if alert:
                out.append(alert)
    return out
