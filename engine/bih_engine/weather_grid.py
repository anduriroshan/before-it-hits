"""Weather on a grid of points across a region, for hours ahead: the data behind the map field.

Why a cube and not a reading: a field you can watch move tells you something a number cannot - that
the heat is building from the east, that the humid air arrives before the rain does. So every point
carries the whole forecast window, not just now, and the browser interpolates between the points to
draw it and between the hours to animate it.

What this is not: a measurement anywhere. The provider models a forecast at each point we ask about,
and between those points we are drawing an interpolation of a model. The grid spacing is returned
with the data so the UI can say how coarse it is, and it is coarse on purpose - every extra point is
another API call, and a denser grid would imply a precision the forecast does not have.
"""
from __future__ import annotations

import json
import os
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor

ENDPOINT = "https://weather.googleapis.com/v1/forecast/hours:lookup"

# One grid costs one API call per point, so it is cached. Scrubbing the time slider must never cost
# a request, and the weather model behind it does not refresh faster than this anyway.
CACHE_TTL_S = 600
_cache: dict[tuple, tuple[float, dict]] = {}
_lock = threading.Lock()

# What the browser can draw as a field. `key` is the per-hour value it reads; `floor`/`ceil` bound
# the colour ramp so the scale does not jump about as the hours step - a field whose legend changes
# every frame is unreadable. They are limits on the *colour*, never on the value shown.
FIELDS = {
    "heat": {"key": "heat_index_c", "fallback": "temperature_c", "label": "Heat index",
             "unit": "°C", "floor": 24.0, "ceil": 44.0,
             "note": "Heat index: what the temperature feels like at this humidity. Where the "
                     "provider gives no heat index for an hour, the air temperature is shown."},
    "temperature": {"key": "temperature_c", "fallback": None, "label": "Temperature",
                    "unit": "°C", "floor": 20.0, "ceil": 40.0,
                    "note": "Air temperature from the forecast model."},
    "humidity": {"key": "humidity_pct", "fallback": None, "label": "Humidity",
                 "unit": "%", "floor": 40.0, "ceil": 100.0,
                 "note": "Relative humidity from the forecast model."},
    "rain": {"key": "precip_mm", "fallback": None, "label": "Rain in the hour",
             "unit": "mm", "floor": 0.0, "ceil": 10.0,
             "note": "Rain the forecast expects in that hour. This is the provider's figure; the "
                     "screening runs on its own forecast window, not on this field."},
    "wind": {"key": "wind_speed_kmh", "fallback": None, "label": "Wind speed",
             "unit": "km/h", "floor": 0.0, "ceil": 40.0,
             "note": "Wind speed from the forecast model. The arrows and moving streaks point the way "
                     "the air is going; the forecast names the direction it comes from. The colour and "
                     "the arrow length carry the speed; how fast a streak moves does not."},
}


class WeatherUnavailable(RuntimeError):
    pass


def _parse_hour(h: dict) -> dict:
    temp = h.get("temperature") or {}
    wind = h.get("wind") or {}
    precip = (h.get("precipitation") or {}).get("qpf") or {}
    return {
        "time": (h.get("interval") or {}).get("startTime"),
        "temperature_c": temp.get("degrees"),
        "humidity_pct": h.get("relativeHumidity"),
        "heat_index_c": (h.get("heatIndex") or {}).get("degrees"),
        "wind_speed_kmh": (wind.get("speed") or {}).get("value"),
        "wind_dir_deg": (wind.get("direction") or {}).get("degrees"),
        "precip_mm": precip.get("quantity"),
    }


def _fetch_point(lat: float, lon: float, hours: int, api_key: str,
                 timeout: float = 20.0) -> list[dict] | None:
    """The forecast hours at one point, or None if that point did not answer.

    A point that fails is dropped rather than raised: the field is an interpolation across whatever
    points came back, and losing one corner of the grid degrades it instead of breaking it.
    """
    params = {
        "key": api_key, "location.latitude": f"{lat:.4f}", "location.longitude": f"{lon:.4f}",
        "hours": str(hours), "pageSize": str(min(24, hours)),
    }
    try:
        with urllib.request.urlopen(f"{ENDPOINT}?{urllib.parse.urlencode(params)}",
                                    timeout=timeout) as r:
            payload = json.loads(r.read())
    except (urllib.error.HTTPError, urllib.error.URLError, TimeoutError, ValueError):
        return None
    parsed = [_parse_hour(h) for h in (payload.get("forecastHours") or [])]
    return parsed[:hours] or None


def _axis(lo: float, hi: float, n: int) -> list[float]:
    if n <= 1:
        return [(lo + hi) / 2]
    return [lo + (hi - lo) * i / (n - 1) for i in range(n)]


def grid(bbox: tuple, grid_size: int = 4, hours: int = 24,
         api_key: str | None = None) -> dict:
    """Every forecast hour at every point of a sparse grid across the bbox.

    Returns {points: [{lat, lng, hours: [...]}], times: [...], grid_size, spacing_km, source}.
    Points are fetched in parallel because a 4x4 grid is sixteen round trips and doing them one
    after another is the difference between a tab that opens and one that hangs.
    """
    key = api_key or os.environ.get("WEATHER_API_KEY") or os.environ.get("MAPS_API_KEY")
    if not key:
        raise WeatherUnavailable("no WEATHER_API_KEY configured")

    grid_size = max(2, min(int(grid_size), 6))
    hours = max(1, min(int(hours), 48))
    w, s, e, n = bbox
    cache_key = (round(w, 4), round(s, 4), round(e, 4), round(n, 4), grid_size, hours)
    with _lock:
        hit = _cache.get(cache_key)
        if hit and time.time() - hit[0] < CACHE_TTL_S:
            return hit[1]

    coords = [(lat, lon) for lat in _axis(s, n, grid_size) for lon in _axis(w, e, grid_size)]
    with ThreadPoolExecutor(max_workers=8) as pool:
        fetched = list(pool.map(lambda c: _fetch_point(c[0], c[1], hours, key), coords))

    points = []
    for (lat, lon), hrs in zip(coords, fetched):
        if hrs:
            points.append({"lat": round(lat, 5), "lng": round(lon, 5), "hours": hrs})
    if not points:
        raise WeatherUnavailable("no weather data returned for the region")

    # The clock is whichever point returned the most hours: all points are asked for the same
    # window, so a short one is a truncated answer, not a different window.
    times = max((p["hours"] for p in points), key=len)
    km_per_deg_lat = 111.32
    out = {
        "points": points,
        "times": [h.get("time") for h in times],
        "grid_size": grid_size,
        "spacing_km": round(max(n - s, e - w) / max(1, grid_size - 1) * km_per_deg_lat, 1),
        "bbox": [w, s, e, n],
        "source": "Google Maps Platform Weather API (hourly forecast)",
    }
    with _lock:
        _cache[cache_key] = (time.time(), out)
    return out


def field(data: dict, kind: str = "heat") -> dict:
    """Reduce the cube to one variable, as parallel arrays the browser can animate.

    Each point becomes {lat, lng, values: [one per hour]}, with None where the provider gave the
    hour no value. The colour bounds are the data's own range widened to the field's floor and
    ceiling, so the legend is stable while the slider moves.
    """
    kind = kind if kind in FIELDS else "heat"
    spec = FIELDS[kind]
    n_hours = len(data.get("times") or []) or 1

    points, seen = [], []
    for p in data.get("points", []):
        vals: list[float | None] = []
        dirs: list[float | None] = []
        for i in range(n_hours):
            h = p["hours"][i] if i < len(p["hours"]) else {}
            v = h.get(spec["key"])
            if v is None and spec["fallback"]:
                v = h.get(spec["fallback"])
            vals.append(None if v is None else round(float(v), 1))
            if v is not None:
                seen.append(float(v))
            d = h.get("wind_dir_deg")
            dirs.append(None if d is None else round(float(d)))
        if any(v is not None for v in vals):
            pt = {"lat": p["lat"], "lng": p["lng"], "values": vals}
            if kind == "wind":
                # Degrees the wind blows FROM, one per hour, so the browser can draw which way the air
                # is going. A direction is only useful with its speed, so it rides on the speed field.
                pt["dirs"] = dirs
            points.append(pt)

    if not points:
        raise WeatherUnavailable(f"the forecast carries no {spec['label'].lower()} for this region")

    lo = min(spec["floor"], min(seen)) if seen else spec["floor"]
    hi = max(spec["ceil"], max(seen)) if seen else spec["ceil"]
    return {
        "points": points,
        "times": data.get("times") or [],
        "bbox": data.get("bbox"),
        "grid_size": data.get("grid_size"),
        "spacing_km": data.get("spacing_km"),
        "field": {"id": kind, "label": spec["label"], "unit": spec["unit"],
                  "min": round(lo, 1), "max": round(hi, 1), "note": spec["note"]},
        "source": data.get("source", ""),
    }
