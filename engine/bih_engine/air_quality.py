"""Air quality current conditions and forecast from Google Air Quality API.

Uses the same WEATHER_API_KEY as the Weather API (it's a Google Cloud API key with
Air Quality API enabled). The MAPS_API_KEY is referrer-restricted and cannot be used
server-side.

The Air Quality API returns:
- Current conditions: AQI (universal and local), dominant pollutant, health recommendations
- Hourly forecast: up to 96 hours of AQI predictions
- Heatmap tiles: rendered browser-side with the MAPS_API_KEY

Every number shown is the provider's, attributed. We never rewrite or reinterpret AQI values.
"""
from __future__ import annotations

import json
import os
import urllib.request
from dataclasses import dataclass, field


class AirQualityUnavailable(Exception):
    pass


AQ_CATEGORIES = [
    {"range": (0, 50), "label": "Good", "color": "#68bc00"},
    {"range": (51, 100), "label": "Moderate", "color": "#f5c400"},
    {"range": (101, 150), "label": "Unhealthy for sensitive groups", "color": "#f47920"},
    {"range": (151, 200), "label": "Unhealthy", "color": "#dc3545"},
    {"range": (201, 300), "label": "Very unhealthy", "color": "#8b3f9e"},
    {"range": (301, 500), "label": "Hazardous", "color": "#7e0023"},
]


# The provider names its units as enum constants. They are correct and they are unreadable, so they
# are written the way a chemist or a newspaper would write them. The value is untouched.
UNITS = {
    "PARTS_PER_BILLION": "ppb",
    "MICROGRAMS_PER_CUBIC_METER": "µg/m³",
    "PARTS_PER_MILLION": "ppm",
    "MILLIGRAMS_PER_CUBIC_METER": "mg/m³",
}


def _unit(raw: str) -> str:
    return UNITS.get(str(raw).upper(), str(raw).replace("_", " ").lower())


def _category(aqi: int | None) -> dict:
    if aqi is None:
        return {"label": "Unknown", "color": "#999"}
    for cat in AQ_CATEGORIES:
        if cat["range"][0] <= aqi <= cat["range"][1]:
            return {"label": cat["label"], "color": cat["color"]}
    return {"label": "Beyond index", "color": "#7e0023"}


@dataclass
class AirQualityResult:
    aqi: int | None = None
    category: str = "Unknown"
    dominant_pollutant: str | None = None
    pollutants: list[dict] = field(default_factory=list)
    health_recommendations: dict = field(default_factory=dict)
    forecast_hours: list[dict] = field(default_factory=list)
    lat: float = 0.0
    lon: float = 0.0

    def as_dict(self) -> dict:
        cat = _category(self.aqi)
        return {
            "aqi": self.aqi,
            "category": cat["label"],
            "category_color": cat["color"],
            "dominant_pollutant": self.dominant_pollutant,
            "pollutants": self.pollutants,
            "health_recommendations": self.health_recommendations,
            "forecast": self.forecast_hours[:48],
            "lat": self.lat,
            "lon": self.lon,
            "source": "Google Air Quality API",
            "caveats": [
                "AQI values are the provider's, shown as published.",
                "Air quality is a regional reading — it does not vary at building level.",
                "This product does not predict health effects for any individual.",
            ],
        }


def _api_key() -> str:
    key = os.environ.get("WEATHER_API_KEY") or os.environ.get("MAPS_API_KEY", "")
    if not key:
        raise AirQualityUnavailable("no WEATHER_API_KEY or MAPS_API_KEY configured")
    return key


def _post_json(url: str, body: dict, api_key: str) -> dict:
    data = json.dumps(body).encode()
    req = urllib.request.Request(
        f"{url}?key={api_key}",
        data=data,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            return json.loads(resp.read())
    except urllib.error.HTTPError as e:
        body_text = ""
        try:
            body_text = e.read().decode()[:500]
        except Exception:
            pass
        if e.code == 403:
            raise AirQualityUnavailable(
                f"Air Quality API returned 403 — the API may not be enabled in the GCP project. {body_text}"
            )
        raise AirQualityUnavailable(f"Air Quality API error {e.code}: {body_text}")
    except Exception as e:
        raise AirQualityUnavailable(f"Air Quality API request failed: {e}")


def fetch_current(lat: float, lon: float) -> AirQualityResult:
    """Fetch current air quality conditions for a point."""
    key = _api_key()
    body = {
        "location": {"latitude": lat, "longitude": lon},
        "extraComputations": [
            "HEALTH_RECOMMENDATIONS",
            "DOMINANT_POLLUTANT_CONCENTRATION",
            "POLLUTANT_CONCENTRATION",
        ],
        "languageCode": "en",
    }
    data = _post_json(
        "https://airquality.googleapis.com/v1/currentConditions:lookup", body, key
    )

    indexes = data.get("indexes", [])
    uaqi = next((i for i in indexes if i.get("code") == "uaqi"), None)
    local = next((i for i in indexes if i.get("code") != "uaqi"), uaqi)

    pollutants = []
    for p in data.get("pollutants", []):
        entry = {
            "code": p.get("code", ""),
            "display_name": p.get("displayName", p.get("code", "")),
            "full_name": p.get("fullName", ""),
        }
        conc = p.get("concentration", {})
        if conc:
            entry["concentration"] = conc.get("value")
            entry["unit"] = _unit(conc.get("units", ""))
        pollutants.append(entry)

    result = AirQualityResult(lat=lat, lon=lon)
    if local:
        result.aqi = local.get("aqi")
        result.category = local.get("category", "Unknown")
        result.dominant_pollutant = local.get("dominantPollutant")
    result.pollutants = pollutants
    result.health_recommendations = data.get("healthRecommendations", {})
    return result


def fetch_forecast(lat: float, lon: float, hours: int = 48) -> list[dict]:
    """Fetch hourly AQI forecast."""
    key = _api_key()
    body = {
        "location": {"latitude": lat, "longitude": lon},
        "period": {"hours": min(hours, 96)},
        "extraComputations": ["DOMINANT_POLLUTANT_CONCENTRATION"],
        "languageCode": "en",
        "pageSize": min(hours, 96),
    }
    try:
        data = _post_json(
            "https://airquality.googleapis.com/v1/forecast:lookup", body, key
        )
    except AirQualityUnavailable:
        return []

    result = []
    for entry in data.get("hourlyForecasts", []):
        time_str = entry.get("dateTime", "")
        indexes = entry.get("indexes", [])
        local = next((i for i in indexes if i.get("code") != "uaqi"), None)
        uaqi = next((i for i in indexes if i.get("code") == "uaqi"), local)
        idx = local or uaqi
        if idx:
            result.append({
                "time": time_str,
                "aqi": idx.get("aqi"),
                "category": idx.get("category", ""),
                "dominant_pollutant": idx.get("dominantPollutant"),
            })
    return result


def for_region(lat: float, lon: float, hours: int = 48) -> dict:
    """Full air quality data for a region centre point."""
    current = fetch_current(lat, lon)
    forecast = fetch_forecast(lat, lon, hours)
    current.forecast_hours = forecast
    return current.as_dict()
