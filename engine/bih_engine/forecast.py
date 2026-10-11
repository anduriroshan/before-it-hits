"""Rainfall forecast for a drawn box, from the Google Maps Platform Weather API.

Why a peak window rather than a total: the screening asks how much water ends up stuck in a
depression, and that is driven by intensity, not by a daily sum. 100 mm in an hour and 100 mm over
three days are different events and only one of them floods a street. So the forecast is reduced to
the worst rolling accumulation over several durations, and the caller picks the one it means.

Two honest limits, both reported with the result:
  - This is a point forecast at the centre of the box, not a rainfall field. A 10 km box can easily
    see very different totals at its corners, and a convective burst is smaller than the box.
  - QPF is a model's prediction, not an observation.

No mapping from rainfall to a return period is attempted. That needs local intensity-duration-
frequency curves, which we have not sourced for either staged city; inventing one would quietly
turn a guess into the thing that selects the official depth layer.
"""
from __future__ import annotations

import json
import os
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, field

ENDPOINT = "https://weather.googleapis.com/v1/forecast/hours:lookup"
MAX_HOURS = 240                      # the API's documented ceiling
PEAK_WINDOWS_H = (1, 3, 6, 12, 24)   # durations we report the worst accumulation for


class ForecastUnavailable(RuntimeError):
    """No usable forecast: no key configured, or the weather service refused the request."""


@dataclass
class Forecast:
    hours: list[dict] = field(default_factory=list)   # [{start, qpf_mm, probability_pct}]
    peaks: dict[int, dict] = field(default_factory=dict)
    total_mm: float = 0.0
    lat: float = 0.0
    lon: float = 0.0

    def as_dict(self) -> dict:
        return {
            "point": {"lat": round(self.lat, 5), "lon": round(self.lon, 5)},
            "hours_ahead": len(self.hours),
            "total_mm": round(self.total_mm, 1),
            "peaks": {
                str(w): {"mm": round(p["mm"], 1), "starts": p["starts"]}
                for w, p in sorted(self.peaks.items())
            },
            "source": "Google Maps Platform Weather API (hourly QPF)",
            "caveats": [
                "A point forecast at the centre of the box, not a rainfall field — a convective "
                "burst is smaller than a typical box and the corners can differ sharply.",
                "QPF is a model prediction, not an observation.",
                "No return period is inferred: that needs local intensity-duration-frequency "
                "curves, which are not sourced for this region.",
            ],
        }


def _rolling_peak(qpf: list[float], window: int) -> tuple[float, int]:
    """Largest sum over any `window` consecutive hours, and the index it starts at."""
    if window > len(qpf):
        return 0.0, 0
    running = sum(qpf[:window])
    best, best_at = running, 0
    for i in range(window, len(qpf)):
        running += qpf[i] - qpf[i - window]
        if running > best:
            best, best_at = running, i - window + 1
    return best, best_at


def summarise(hours: list[dict], lat: float, lon: float) -> Forecast:
    qpf = [h["qpf_mm"] for h in hours]
    peaks = {}
    for w in PEAK_WINDOWS_H:
        if w <= len(qpf):
            mm, at = _rolling_peak(qpf, w)
            peaks[w] = {"mm": mm, "starts": hours[at]["start"]}
    return Forecast(hours=hours, peaks=peaks, total_mm=sum(qpf), lat=lat, lon=lon)


def fetch(lat: float, lon: float, hours: int = 48, api_key: str | None = None,
          timeout: float = 20.0) -> Forecast:
    """Hourly rainfall forecast at a point. Raises ForecastUnavailable rather than returning zeros,
    because a silent zero reads as 'no rain forecast' and that is a different claim."""
    key = api_key or os.environ.get("WEATHER_API_KEY") or os.environ.get("MAPS_API_KEY")
    if not key:
        raise ForecastUnavailable("no WEATHER_API_KEY configured")
    hours = max(1, min(int(hours), MAX_HOURS))

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
            raise ForecastUnavailable(f"weather service returned {exc.code}: {detail}") from exc
        except (urllib.error.URLError, TimeoutError) as exc:
            raise ForecastUnavailable(f"weather service unreachable: {exc}") from exc

        page = payload.get("forecastHours") or []
        if not page:
            break
        for h in page:
            precip = h.get("precipitation") or {}
            collected.append({
                "start": (h.get("interval") or {}).get("startTime"),
                "qpf_mm": float((precip.get("qpf") or {}).get("quantity") or 0.0),
                "probability_pct": (precip.get("probability") or {}).get("percent"),
            })
        page_token = payload.get("nextPageToken")
        if not page_token:
            break

    if not collected:
        raise ForecastUnavailable("weather service returned no forecast hours")
    return summarise(collected[:hours], lat, lon)


def for_bbox(bbox, hours: int = 48, **kw) -> Forecast:
    w, s, e, n = bbox
    return fetch((s + n) / 2, (w + e) / 2, hours=hours, **kw)
