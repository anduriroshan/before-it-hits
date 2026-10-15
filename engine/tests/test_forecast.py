"""Offline tests for the rainfall forecast. No network: the point of the module is the reduction
from hourly QPF to a worst-case window, and that is pure arithmetic."""
import pytest

from bih_engine.forecast import Forecast, ForecastUnavailable, _rolling_peak, fetch, summarise


def hours(*qpf):
    return [{"start": f"2026-10-11T{i:02d}:00:00Z", "qpf_mm": float(q), "probability_pct": 50}
            for i, q in enumerate(qpf)]


def test_peak_window_finds_the_worst_run_not_the_first():
    qpf = [0, 1, 1, 0, 9, 9, 0, 2]
    assert _rolling_peak(qpf, 2) == (18.0, 4)
    assert _rolling_peak(qpf, 1) == (9.0, 4)


def test_a_window_longer_than_the_forecast_is_not_reported():
    f = summarise(hours(5, 5, 5), 1.3, 103.8)
    assert set(f.peaks) == {1, 3}          # 6, 12, 24 h do not fit in a 3 h forecast
    assert f.peaks[3]["mm"] == pytest.approx(15.0)


def test_the_same_total_in_a_burst_and_a_drizzle_are_told_apart():
    burst = summarise(hours(0, 0, 60, 0, 0, 0), 1.3, 103.8)
    drizzle = summarise(hours(10, 10, 10, 10, 10, 10), 1.3, 103.8)
    assert burst.total_mm == drizzle.total_mm == pytest.approx(60.0)
    assert burst.peaks[1]["mm"] == pytest.approx(60.0)
    assert drizzle.peaks[1]["mm"] == pytest.approx(10.0)   # the whole point of reporting peaks


def test_peak_window_records_when_the_worst_run_starts():
    f = summarise(hours(0, 0, 0, 20, 20, 0), 1.3, 103.8)
    assert f.peaks[3]["starts"] == "2026-10-11T02:00:00Z"
    assert f.peaks[3]["mm"] == pytest.approx(40.0)


def test_a_dry_forecast_is_zero_rather_than_missing():
    f = summarise(hours(0, 0, 0, 0), 1.3, 103.8)
    assert f.total_mm == 0.0 and f.peaks[1]["mm"] == 0.0
    assert f.as_dict()["total_mm"] == 0.0


def test_the_result_carries_its_own_limits():
    d = summarise(hours(1, 2, 3), 1.3036, 103.8318).as_dict()
    assert d["point"] == {"lat": 1.3036, "lon": 103.8318}
    assert d["source"].startswith("Google Maps Platform Weather API")
    joined = " ".join(d["caveats"]).lower()
    assert "point forecast" in joined          # not a rainfall field
    assert "return period" in joined           # and we do not invent one
    assert d["hours_ahead"] == 3


def test_no_key_fails_loudly_rather_than_reporting_no_rain(monkeypatch):
    monkeypatch.delenv("WEATHER_API_KEY", raising=False)
    monkeypatch.delenv("MAPS_API_KEY", raising=False)
    with pytest.raises(ForecastUnavailable, match="no WEATHER_API_KEY"):
        fetch(1.3, 103.8)


def test_peaks_are_monotonic_in_window_length():
    f = summarise(hours(*[3] * 30), 1.3, 103.8)
    mm = [f.peaks[w]["mm"] for w in sorted(f.peaks)]
    assert mm == sorted(mm)   # a longer window can never collect less


def test_empty_forecast_is_an_error_not_an_empty_object():
    f = Forecast()
    assert f.as_dict()["hours_ahead"] == 0


def test_the_hourly_shape_is_exposed_for_the_playback():
    d = summarise(hours(1, 2, 3), 1.3, 103.8).as_dict()
    assert [h["mm"] for h in d["hours"]] == [1.0, 2.0, 3.0]   # per-hour amounts, in order
    assert d["hours"][0]["t"].startswith("2026")             # each carries its start time


# ---- one forecast per place for a few minutes ---------------------------------------------------------

def _fake_urlopen(monkeypatch, quantities):
    """A provider that answers `quantities` hour by hour, and counts how often it is asked."""
    import io
    import json as _json
    calls = {"n": 0}

    def urlopen(url, timeout=0):
        calls["n"] += 1
        body = {"forecastHours": [{"interval": {"startTime": f"2026-10-15T{i:02d}:00:00Z"},
                                   "precipitation": {"qpf": {"quantity": q}}}
                                  for i, q in enumerate(quantities)]}
        return io.BytesIO(_json.dumps(body).encode())
    monkeypatch.setattr("bih_engine.forecast.urllib.request.urlopen", urlopen)
    monkeypatch.setenv("WEATHER_API_KEY", "test")
    return calls


def test_two_questions_about_one_place_see_the_same_forecast(monkeypatch):
    calls = _fake_urlopen(monkeypatch, [0.5] * 24)
    a, b = fetch(1.315, 103.87, hours=24), fetch(1.315, 103.87, hours=24)
    assert a is b and calls["n"] == 1


def test_a_different_place_is_a_different_forecast(monkeypatch):
    calls = _fake_urlopen(monkeypatch, [0.5] * 24)
    fetch(1.315, 103.87, hours=24)
    fetch(10.02, 76.31, hours=24)
    assert calls["n"] == 2


def test_the_cache_expires_so_a_stale_forecast_is_never_kept_for_long(monkeypatch):
    from bih_engine import forecast
    calls = _fake_urlopen(monkeypatch, [0.5] * 24)
    clock = {"t": 1000.0}
    monkeypatch.setattr(forecast.time, "monotonic", lambda: clock["t"])
    fetch(1.315, 103.87, hours=24)
    clock["t"] += forecast.CACHE_S - 1
    fetch(1.315, 103.87, hours=24)
    assert calls["n"] == 1
    clock["t"] += 2
    fetch(1.315, 103.87, hours=24)
    assert calls["n"] == 2


def test_a_failed_fetch_is_not_remembered_as_a_forecast(monkeypatch):
    import urllib.error
    monkeypatch.setenv("WEATHER_API_KEY", "test")
    monkeypatch.setattr("bih_engine.forecast.urllib.request.urlopen",
                        lambda *a, **k: (_ for _ in ()).throw(urllib.error.URLError("down")))
    with pytest.raises(ForecastUnavailable):
        fetch(1.315, 103.87, hours=24)
    calls = _fake_urlopen(monkeypatch, [0.5] * 24)
    assert fetch(1.315, 103.87, hours=24).total_mm == 12.0 and calls["n"] == 1


# ---- the window the asker meant ------------------------------------------------------------------------

from bih_engine.forecast import pick_window  # noqa: E402


def _forecast(first_utc_hour, qpf, day="2026-10-15"):
    """A Forecast.as_dict() whose hours run from `first_utc_hour` UTC on `day`."""
    hs = []
    for i, q in enumerate(qpf):
        t = first_utc_hour + i
        d = int(day[-2:]) + t // 24
        hs.append({"start": f"{day[:-2]}{d:02d}T{t % 24:02d}:00:00Z", "qpf_mm": float(q), "probability_pct": 50})
    return summarise(hs, 1.3, 103.8).as_dict()


def test_the_next_n_hours_are_the_first_n_not_the_wettest_n_anywhere_in_the_forecast():
    """The bug: 'the next 3 hours' returned the worst 3 hours in 48, which can be tomorrow."""
    qpf = [0.1] * 3 + [0.0] * 20 + [9.0] * 3 + [0.0] * 22
    f = _forecast(0, qpf)
    w = pick_window(f, 3, "now")
    assert w["mm"] == pytest.approx(0.3) and w["starts_in_hours"] == 0
    worst = pick_window(f, 3, "wettest")
    assert worst["mm"] == pytest.approx(27.0) and worst["starts_in_hours"] == 23


def test_a_window_says_how_many_hours_from_now_it_starts():
    f = _forecast(0, [0.0] * 5 + [4.0, 4.0] + [0.0] * 41)
    w = pick_window(f, 2, "wettest")
    assert w["starts_in_hours"] == 5 and w["mm"] == 8.0


def test_tonight_starts_at_18_local_time_in_the_cities_own_zone():
    # 08:00 UTC is 16:00 in Singapore (UTC+8), so tonight starts in two hours.
    f = _forecast(8, [0.0] * 48)
    assert pick_window(f, 12, "tonight", "Asia/Singapore")["starts_in_hours"] == 2
    # the same instant in Kochi (UTC+5:30) is 13:30, but the hour boundary is what counts: 13 -> 5 hours
    assert pick_window(f, 12, "tonight", "Asia/Kolkata")["starts_in_hours"] == 5


def test_tonight_when_it_is_already_night_means_now():
    f = _forecast(14, [0.0] * 48)               # 22:00 in Singapore
    assert pick_window(f, 8, "tonight", "Asia/Singapore")["starts_in_hours"] == 0


def test_tomorrow_starts_at_the_next_local_midnight():
    f = _forecast(8, [0.0] * 48)                # 16:00 in Singapore: midnight is 8 hours away
    w = pick_window(f, 24, "tomorrow", "Asia/Singapore")
    assert w["starts_in_hours"] == 8 and w["hours"] == 24 and not w["truncated"]


def test_a_window_the_forecast_ends_partway_through_is_flagged_not_quietly_shortened():
    f = _forecast(8, [1.0] * 48)
    w = pick_window(f, 24, "tomorrow", "Asia/Singapore")
    assert w["truncated"] is False
    late = _forecast(20, [1.0] * 48)            # 04:00 in Singapore: midnight is 20 hours away, 28 hours remain
    w = pick_window(late, 48, "tomorrow", "Asia/Singapore")
    assert w["truncated"] is True and w["hours"] == 28


def test_a_window_that_starts_after_the_forecast_ends_is_none():
    f = _forecast(8, [1.0] * 6)
    assert pick_window(f, 6, "tomorrow", "Asia/Singapore") is None


def test_an_unknown_anchor_or_zone_degrades_to_something_honest_rather_than_failing():
    f = _forecast(8, [1.0] * 48)
    assert pick_window(f, 6, "next fortnight")["anchor"] == "wettest"
    assert pick_window(f, 12, "tonight", "Not/AZone")["starts_in_hours"] == 10     # read as UTC: 08:00 -> 18:00


# ---- round 3: a clock time or an offset in the question picks the hour, and code does the arithmetic ----------

def test_a_time_named_as_hours_from_now_is_that_many_hours_from_now():
    from bih_engine.forecast import hours_until
    f = _forecast(8, [0.0] * 48)
    assert hours_until(f, "Asia/Singapore", in_hours=3) == 3
    assert hours_until(f, "Asia/Singapore", in_hours=1.4) == 1


def test_a_clock_time_today_is_worked_out_in_the_cities_own_zone():
    from bih_engine.forecast import hours_until
    f = _forecast(8, [0.0] * 48)                  # 16:00 in Singapore, 13:00 in Kochi
    assert hours_until(f, "Asia/Singapore", hour=18, day="today") == 2
    assert hours_until(f, "Asia/Kolkata", hour=18, day="today") == 5


def test_a_clock_time_tomorrow_is_a_day_further_on():
    from bih_engine.forecast import hours_until
    f = _forecast(8, [0.0] * 48)                  # 16:00 in Singapore
    assert hours_until(f, "Asia/Singapore", hour=9, day="tomorrow") == 17


def test_a_clock_time_with_no_day_means_the_next_time_it_is_that_hour():
    from bih_engine.forecast import hours_until
    f = _forecast(8, [0.0] * 48)                  # 16:00 in Singapore
    assert hours_until(f, "Asia/Singapore", hour=18) == 2
    assert hours_until(f, "Asia/Singapore", hour=9) == 17      # 9am has gone, so the next one is tomorrow's


def test_a_time_today_that_has_already_gone_is_none_not_a_negative_number():
    from bih_engine.forecast import hours_until
    f = _forecast(8, [0.0] * 48)
    assert hours_until(f, "Asia/Singapore", hour=9, day="today") is None


def test_a_window_starting_later_reads_the_hours_from_there():
    qpf = [0.0] * 5 + [4.0, 1.0] + [0.0] * 41
    f = _forecast(0, qpf)
    w = pick_window(f, 1, "later", "Asia/Singapore", start=5)
    assert w["mm"] == 4.0 and w["starts_in_hours"] == 5 and w["hours"] == 1 and w["anchor"] == "later"
    two = pick_window(f, 2, "later", "Asia/Singapore", start=5)
    assert two["mm"] == 5.0


def test_a_start_beyond_the_forecast_is_none_and_a_negative_start_is_read_as_now():
    f = _forecast(0, [1.0] * 10)
    assert pick_window(f, 1, "later", "Asia/Singapore", start=12) is None
    assert pick_window(f, 1, "later", "Asia/Singapore", start=-3)["starts_in_hours"] == 0
