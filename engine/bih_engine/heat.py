"""Heat hazard screening: current and forecast heat conditions for a region.

Uses the Google Weather API to fetch temperature, humidity, heat index and
apparent temperature at a grid of points, then ranks areas by heat stress.

This is forecast-only (no Landsat satellite data yet). It shows:
- Current heat index / apparent temperature at the centre
- A simple ranking of whether conditions are in advisory/warning/danger zones
- Humidity levels that affect perceived heat

Deliberately does not claim air temperature at a specific spot - the API gives
a point forecast, not a measured temperature at that location.
"""
from __future__ import annotations

import json
import os
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, field

ENDPOINT = "https://weather.googleapis.com/v1/forecast/hours:lookup"

HEAT_LEVELS = [
    {"label": "Extreme danger", "min_c": 54, "color": "#7c0a02"},
    {"label": "Danger", "min_c": 41, "color": "#c0392b"},
    {"label": "Extreme caution", "min_c": 33, "color": "#e67e22"},
    {"label": "Caution", "min_c": 27, "color": "#f1c40f"},
    {"label": "Normal", "min_c": 0, "color": "#27ae60"},
]


class HeatUnavailable(RuntimeError):
    pass


@dataclass
class HeatForecast:
    hours: list[dict] = field(default_factory=list)
    current: dict = field(default_factory=dict)
    peak: dict = field(default_factory=dict)
    lat: float = 0.0
    lon: float = 0.0

    def heat_level(self, heat_index_c: float | None) -> dict:
        if heat_index_c is None:
            return HEAT_LEVELS[-1]
        for level in HEAT_LEVELS:
            if heat_index_c >= level["min_c"]:
                return level
        return HEAT_LEVELS[-1]

    def as_dict(self) -> dict:
        current_level = self.heat_level(self.current.get("heat_index_c"))
        peak_level = self.heat_level(self.peak.get("heat_index_c"))
        return {
            "point": {"lat": round(self.lat, 5), "lon": round(self.lon, 5)},
            "current": self.current,
            "current_level": current_level,
            "peak": self.peak,
            "peak_level": peak_level,
            "hours": self.hours,
            "source": "Google Maps Platform Weather API (hourly forecast)",
            "caveats": [
                "A point forecast at the centre of the region, not a spatial heat map.",
                "Heat index is based on air temperature and humidity from a weather model.",
                "Actual conditions vary significantly within an urban area due to land cover, "
                "shade, and building density.",
            ],
        }


def _parse_hour(h: dict) -> dict:
    temp = h.get("temperature") or {}
    humidity = h.get("relativeHumidity")
    wind = h.get("wind") or {}
    heat_index = h.get("heatIndex") or {}
    dew_point = h.get("dewPoint") or {}
    return {
        "time": (h.get("interval") or {}).get("startTime"),
        "temperature_c": temp.get("degrees"),
        "humidity_pct": humidity,
        "heat_index_c": heat_index.get("degrees"),
        "dew_point_c": dew_point.get("degrees"),
        "wind_speed_kmh": (wind.get("speed") or {}).get("value"),
    }


def fetch(lat: float, lon: float, hours: int = 48,
          api_key: str | None = None, timeout: float = 20.0) -> HeatForecast:
    key = api_key or os.environ.get("WEATHER_API_KEY") or os.environ.get("MAPS_API_KEY")
    if not key:
        raise HeatUnavailable("no WEATHER_API_KEY configured")
    hours = max(1, min(int(hours), 240))

    collected: list[dict] = []
    page_token = None
    while len(collected) < hours:
        params = {
            "key": key, "location.latitude": f"{lat:.5f}", "location.longitude": f"{lon:.5f}",
            "hours": str(hours), "pageSize": "24",
        }
        if page_token:
            params["pageToken"] = page_token
        try:
            with urllib.request.urlopen(f"{ENDPOINT}?{urllib.parse.urlencode(params)}",
                                        timeout=timeout) as r:
                payload = json.loads(r.read())
        except urllib.error.HTTPError as exc:
            detail = exc.read()[:200].decode("utf-8", "replace")
            raise HeatUnavailable(f"weather service returned {exc.code}: {detail}") from exc
        except (urllib.error.URLError, TimeoutError) as exc:
            raise HeatUnavailable(f"weather service unreachable: {exc}") from exc

        page = payload.get("forecastHours") or []
        if not page:
            break
        collected.extend(page)
        page_token = payload.get("nextPageToken")
        if not page_token:
            break

    if not collected:
        raise HeatUnavailable("weather service returned no forecast hours")

    parsed = [_parse_hour(h) for h in collected[:hours]]
    current = parsed[0] if parsed else {}
    peak = max(parsed, key=lambda h: h.get("heat_index_c") or h.get("temperature_c") or 0)

    return HeatForecast(hours=parsed, current=current, peak=peak, lat=lat, lon=lon)


def for_bbox(bbox, hours: int = 48, **kw) -> HeatForecast:
    w, s, e, n = bbox
    return fetch((s + n) / 2, (w + e) / 2, hours=hours, **kw)
