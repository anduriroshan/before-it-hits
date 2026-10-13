"""Weather conditions at a grid of points across a region's bounding box.

Uses the same Google Weather API as forecast.py but extracts temperature, humidity, heat index
and wind instead of just precipitation. Returns current conditions (the first forecast hour)
at a sparse grid so the browser can render coloured markers.
"""
from __future__ import annotations

import json
import os
import urllib.error
import urllib.parse
import urllib.request

ENDPOINT = "https://weather.googleapis.com/v1/forecast/hours:lookup"


class WeatherUnavailable(RuntimeError):
    pass


def _fetch_point(lat: float, lon: float, api_key: str, timeout: float = 15.0) -> dict | None:
    params = {
        "key": api_key, "location.latitude": f"{lat:.4f}", "location.longitude": f"{lon:.4f}",
        "hours": "1", "pageSize": "1",
    }
    try:
        with urllib.request.urlopen(f"{ENDPOINT}?{urllib.parse.urlencode(params)}",
                                    timeout=timeout) as r:
            payload = json.loads(r.read())
    except (urllib.error.HTTPError, urllib.error.URLError, TimeoutError):
        return None

    hours = payload.get("forecastHours") or []
    if not hours:
        return None
    h = hours[0]
    temp = h.get("temperature") or {}
    humidity = h.get("relativeHumidity")
    wind = h.get("wind") or {}
    heat_index = h.get("heatIndex") or {}
    return {
        "temperature_c": temp.get("degrees"),
        "humidity_pct": humidity,
        "wind_speed_kmh": (wind.get("speed") or {}).get("value"),
        "heat_index_c": heat_index.get("degrees"),
        "time": (h.get("interval") or {}).get("startTime"),
    }


def grid(bbox: tuple, grid_size: int = 3, api_key: str | None = None) -> dict:
    """Fetch current weather on a sparse grid across the bbox.

    Returns {points: [{lat, lng, temperature_c, humidity_pct, heat_index_c, ...}], source, time}.
    grid_size controls how many points per axis (default 3x3 = 9 points).
    """
    key = api_key or os.environ.get("WEATHER_API_KEY") or os.environ.get("MAPS_API_KEY")
    if not key:
        raise WeatherUnavailable("no WEATHER_API_KEY configured")

    w, s, e, n = bbox
    lats = [s + (n - s) * i / (grid_size - 1) for i in range(grid_size)] if grid_size > 1 else [(s + n) / 2]
    lons = [w + (e - w) * i / (grid_size - 1) for i in range(grid_size)] if grid_size > 1 else [(w + e) / 2]

    points = []
    for lat in lats:
        for lon in lons:
            pt = _fetch_point(lat, lon, key)
            if pt and pt.get("temperature_c") is not None:
                points.append({"lat": round(lat, 5), "lng": round(lon, 5), **pt})

    if not points:
        raise WeatherUnavailable("no weather data returned for the region")

    return {
        "points": points,
        "source": "Google Maps Platform Weather API",
        "time": points[0].get("time"),
    }
