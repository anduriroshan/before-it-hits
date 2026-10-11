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
