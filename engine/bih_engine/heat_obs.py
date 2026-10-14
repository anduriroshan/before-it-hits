"""Live measured heat stress from Singapore's NEA WBGT stations (data.gov.sg), keyless.

The heat counterpart to rainfall_obs.py, and the same kind of thing: a national network reporting
what *is* happening, beside a forecast of what is coming. NEA rate heat stress on the wet-bulb globe
temperature, which folds in humidity, wind and solar radiation, so it is not an air temperature and
is never comparable with one.

This matters beyond being one more feed. WBGT cannot be computed from the hourly forecast, which
carries no solar radiation, so for a city with no station network there is no honest way to say
where the authority's own index stands. Singapore has the network; the other staged city does not.
That is a real difference in what can be known about two cities, and the product says so rather than
quietly modelling one and measuring the other.

Every figure here is a station's own reading, including NEA's own rating of it. Nothing is modelled.
"""
from __future__ import annotations

import json
import urllib.error
import urllib.request

from .rainfall_obs import _km

ENDPOINT = "https://api-open.data.gov.sg/v2/real-time/api/weather?api=wbgt"
UA = "before-it-hits-engine/0.1 (hackathon prototype)"


class HeatObsUnavailable(RuntimeError):
    """No usable reading: the station API was unreachable or returned nothing."""


def available(region) -> bool:
    """Does this city have a measured heat-stress network staged for it?"""
    std = (getattr(region, "manifest", {}) or {}).get("heat_standard") or {}
    return (std.get("observed") or {}).get("source") == "nea_wbgt"


def fetch(timeout: float = 15.0) -> list[dict]:
    """The latest reading from every station. Raises rather than returning an empty list, because
    'no stations' and 'no heat stress' are different claims."""
    req = urllib.request.Request(ENDPOINT, headers={"User-Agent": UA})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            payload = json.loads(r.read())
    except (urllib.error.URLError, TimeoutError, ValueError) as exc:
        raise HeatObsUnavailable(f"NEA WBGT API unreachable: {exc}") from exc
    if payload.get("code") != 0:
        raise HeatObsUnavailable(payload.get("errorMsg") or "NEA WBGT API returned an error")
    records = (payload.get("data") or {}).get("records") or []
    if not records:
        raise HeatObsUnavailable("NEA WBGT API returned no records")
    return records


def _rows(records: list[dict]) -> tuple[list[dict], str | None]:
    """Station readings from the newest record, as numbers. The API sends every field as a string,
    and a station mid-outage sends one that will not parse; those are dropped rather than zeroed."""
    newest = records[0]
    out = []
    for r in (newest.get("item") or {}).get("readings") or []:
        loc, st = r.get("location") or {}, r.get("station") or {}
        try:
            lat, lon, wbgt = float(loc["latitude"]), float(loc["longitude"]), float(r["wbgt"])
        except (KeyError, TypeError, ValueError):
            continue
        out.append({"name": st.get("name"), "lat": lat, "lon": lon,
                    "wbgt_c": round(wbgt, 1), "rated": r.get("heatStress")})
    return out, newest.get("datetime")


def near(bbox, *, k: int = 3, records: list[dict] | None = None) -> dict:
    """The nearest stations to the box, each with its own WBGT and NEA's own rating of it.

    `records` lets a caller or a test pass a fetched payload in. The hottest of the nearby stations
    is reported beside the nearest, because the nearest is not always the worst.
    """
    w, s, e, n = bbox
    clat, clon = (s + n) / 2.0, (w + e) / 2.0
    rows, at = _rows(records if records is not None else fetch())
    if not rows:
        raise HeatObsUnavailable("no station returned a reading")
    for r in rows:
        r["km"] = round(_km(clat, clon, r["lat"], r["lon"]), 1)
        r["in_box"] = bool(w <= r["lon"] <= e and s <= r["lat"] <= n)
    rows.sort(key=lambda r: r["km"])
    nearest = rows[:k]
    return {
        "available": True,
        "source": "NEA WBGT stations via data.gov.sg",
        "at": at,
        "stations_reporting": len(rows),
        "nearest": nearest,
        "hottest_nearby": max(nearest, key=lambda r: r["wbgt_c"]),
        "note": "Measured wet-bulb globe temperature, and the authority's own rating of it. It folds "
                "in humidity, wind and sunshine, so it is not an air temperature and must never be "
                "set beside one as though they were the same reading.",
    }
