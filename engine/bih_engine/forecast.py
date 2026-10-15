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
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from datetime import datetime
from zoneinfo import ZoneInfo

ENDPOINT = "https://weather.googleapis.com/v1/forecast/hours:lookup"
MAX_HOURS = 240                      # the API's documented ceiling
PEAK_WINDOWS_H = (1, 3, 6, 12, 24)   # durations we report the worst accumulation for
CACHE_S = 600                        # one set of numbers per place for ten minutes
_CACHE: dict = {}
ANCHORS = ("now", "tonight", "tomorrow", "wettest", "later")


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
            # The per-hour amounts, so a caller can play back the real shape of the storm rather
            # than spreading a total evenly. Just the rainfall, nothing derived.
            "hours": [{"t": h["start"], "mm": round(h["qpf_mm"], 2)} for h in self.hours],
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
    """The forecast at a point, kept for ten minutes.

    Without this, two questions about the same place a minute apart each fetched a fresh forecast, and
    when the provider's run moved between them the answers disagreed ("0.9 mm, 2 spots fill", then
    "2.5 mm, 4 spots fill") for what the asker took to be the same rain. One conversation now sees one
    forecast. A failure is never cached."""
    key = (round(lat, 3), round(lon, 3), int(hours))
    hit = _CACHE.get(key)
    if hit and time.monotonic() - hit[0] < CACHE_S:
        return hit[1]
    fc = _fetch(lat, lon, hours, api_key, timeout)
    if len(_CACHE) >= 64:
        _CACHE.pop(next(iter(_CACHE)))
    _CACHE[key] = (time.monotonic(), fc)
    return fc


def _fetch(lat: float, lon: float, hours: int = 48, api_key: str | None = None,
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


def _local_hour(first_iso: str, tz: str | None) -> int:
    """The local clock hour the forecast's first hour starts at, in the city's own timezone."""
    t = datetime.fromisoformat(str(first_iso).replace("Z", "+00:00"))
    try:
        t = t.astimezone(ZoneInfo(tz)) if tz else t
    except Exception:                       # unknown zone: UTC, which only shifts "tonight" and "tomorrow"
        pass
    return t.hour


def hours_until(f: dict, tz: str | None, *, hour: int | None = None, day: str = "none",
                in_hours: float | None = None) -> int | None:
    """How many hours from the forecast's first hour a time the asker named falls: "in 3 hours", or "6pm"
    (hour 18) today or tomorrow. The model only READS the time out of the question; this is the clock
    arithmetic, done in the city's own zone, and it is checked against the forecast: None means the time
    has already passed. "6pm" with no day means the next 6pm."""
    if in_hours is not None:
        return int(round(in_hours))
    h0 = _local_hour((f.get("hours") or [{}])[0].get("t"), tz)
    if day == "tomorrow":
        return hour - h0 + 24
    if day == "today":
        return hour - h0 if hour >= h0 else None
    return (hour - h0) % 24


def pick_window(f: dict, length: float, anchor: str, tz: str | None = None,
                start: int | None = None) -> dict | None:
    """The rainfall over `length` hours that the asker actually meant.

    The forecast reduced to its worst stretch answers "how bad could it get", and that was being served
    for every question, so "the next 3 hours" got the wettest 3 hours anywhere in the next two days -
    which can start tomorrow. This keeps the two apart:
      now       the first hours of the forecast
      tonight   from 18:00 local (or now, if it is already evening or night)
      tomorrow  from the next local midnight
      wettest   the worst stretch of that length anywhere in the forecast
      later     from `start` hours from now: "at 6pm", "in 3 hours"
    `f` is Forecast.as_dict(). Returns None when the window starts beyond the forecast, and flags a
    window the forecast ends partway through rather than quietly shortening it."""
    hours = f.get("hours") or []
    if not hours:
        return None
    length = max(1, min(int(round(float(length))), len(hours)))
    if anchor not in ANCHORS or anchor == "wettest":
        # The lengths the forecast already summarised are used as summarised; any other length the asker
        # names is worked out exactly, not snapped to the nearest, which answered a 2-hour question with 1.
        p = (f.get("peaks") or {}).get(str(length))
        if p:
            mm, starts = p["mm"], p["starts"]
            at = next((i for i, h in enumerate(hours) if h.get("t") == starts), 0)
        else:
            mm, at = _rolling_peak([h["mm"] for h in hours], length)
            starts = hours[at]["t"]
        return {"mm": round(mm, 1), "starts": starts, "starts_in_hours": at, "hours": length,
                "anchor": "wettest", "truncated": False}
    h0 = _local_hour(hours[0]["t"], tz)
    begin = (max(0, int(start)) if anchor == "later" and start is not None else
             {"now": 0, "tonight": 0 if (h0 >= 18 or h0 < 6) else 18 - h0, "tomorrow": 24 - h0,
              "later": 0}[anchor])
    seg = hours[begin:begin + length]
    if not seg:
        return None
    return {"mm": round(sum(h["mm"] for h in seg), 1), "starts": seg[0]["t"], "starts_in_hours": begin,
            "hours": len(seg), "anchor": anchor, "truncated": len(seg) < length}
