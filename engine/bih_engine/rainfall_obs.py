"""Live observed rainfall from Singapore's NEA gauge network (data.gov.sg), keyless.

The counterpart to forecast.py. The Weather API says what *will* fall; this says what *is* falling,
from the national rain-gauge network - about ninety gauges at a five-minute cadence, with no key
required. No other staged city has an equivalent open feed: India's IMD gateway needs JWT plus IP
whitelisting, which is real friction for a short build, so this is Singapore-specific.

Display and context only. The screening and the agent still run off the forecast; this is a "what
is coming down right now, and where" readout for a drawn box and the nearest few gauges to it.
Every figure is a gauge's own millimetres - nothing here is modelled or inferred.
"""
from __future__ import annotations

import json
import math
import urllib.error
import urllib.request

ENDPOINT = "https://api-open.data.gov.sg/v2/real-time/api/rainfall"
UA = "before-it-hits-engine/0.1 (hackathon prototype)"


class RainfallObsUnavailable(RuntimeError):
    """No usable observation: the gauge API was unreachable or returned nothing."""


def _km(a_lat: float, a_lon: float, b_lat: float, b_lon: float) -> float:
    """Great-circle distance in km. Good enough to rank gauges by nearness to a box."""
    r = 6371.0
    p1, p2 = math.radians(a_lat), math.radians(b_lat)
    dp = math.radians(b_lat - a_lat)
    dl = math.radians(b_lon - a_lon)
    h = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * r * math.asin(min(1.0, math.sqrt(h)))


def fetch(timeout: float = 15.0) -> dict:
    """The raw `data` block from the NEA rainfall endpoint. Raises rather than returning zeros,
    because a silent empty reads as 'no rain' and that is a different claim."""
    req = urllib.request.Request(ENDPOINT, headers={"User-Agent": UA})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            payload = json.loads(r.read())
    except (urllib.error.URLError, TimeoutError, ValueError) as exc:
        raise RainfallObsUnavailable(f"NEA rainfall API unreachable: {exc}") from exc
    if payload.get("code") != 0:
        raise RainfallObsUnavailable(payload.get("errorMsg") or "NEA rainfall API returned an error")
    return payload.get("data") or {}


def near(bbox, *, k: int = 5, near_km: float = 10.0, data: dict | None = None) -> dict:
    """The nearest `k` gauges to the box centre, each with its latest five-minute total, plus the
    wettest gauge in or near the box. `data` lets a caller (or a test) pass a fetched payload in."""
    w, s, e, n = bbox
    clat, clon = (s + n) / 2.0, (w + e) / 2.0
    data = data if data is not None else fetch()
    stations = data.get("stations", [])
    readings = data.get("readings", [])
    latest = readings[0] if readings else {"timestamp": None, "data": []}
    value_by_id = {x["stationId"]: x["value"] for x in latest.get("data", [])}

    rows = []
    for st in stations:
        loc = st.get("location") or {}
        lat, lon = loc.get("latitude"), loc.get("longitude")
        v = value_by_id.get(st.get("id"))
        if lat is None or lon is None or v is None:
            continue
        rows.append({
            "name": st.get("name"), "lat": lat, "lon": lon,
            "mm_5min": round(float(v), 1),
            "km": round(_km(clat, clon, lat, lon), 1),
            "in_box": bool(w <= lon <= e and s <= lat <= n),
        })
    if not rows:
        raise RainfallObsUnavailable("no gauge returned a reading")
    rows.sort(key=lambda r: r["km"])
    nearest = rows[:k]
    pool = [r for r in rows if r["in_box"] or r["km"] <= near_km] or nearest
    wettest = max(pool, key=lambda r: r["mm_5min"])
    return {
        "available": True,
        "source": "NEA rain gauges via data.gov.sg (five-minute totals)",
        "at": latest.get("timestamp"),
        "unit": "mm per 5 min",
        "gauges_reporting": len(rows),
        "nearest": nearest,
        "wettest_nearby": wettest,
        "note": "Observed rainfall from the national gauge network - not a forecast and not our "
                "model. Each figure is a gauge's own five-minute total in millimetres.",
    }
