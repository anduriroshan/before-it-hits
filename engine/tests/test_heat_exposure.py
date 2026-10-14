"""Heat exposure: whose threshold is used, what happens when it cannot be checked, and the line
between counting people and claiming harm. Offline - the forecast and the population grid are both
passed in or stubbed, because what is worth testing is the judgement, not the fetching."""
import pytest

from bih_engine import heat_exposure, popgrid

IMD = {
    "authority": "the India Meteorological Department",
    "measure": "temperature_c",
    "measure_label": "the highest air temperature forecast",
    "bands": [{"label": "IMD's severe heat-wave temperature", "min_c": 47},
              {"label": "IMD's heat-wave temperature", "min_c": 45},
              {"label": "the temperature IMD requires at a coastal station", "min_c": 37}],
    "source": "https://mausam.imd.gov.in/ (heat-wave criteria)",
    "incomplete": "IMD also need the day to run well above the station's own normal, and no normal "
                  "is staged, so this never declares a heat wave.",
}
NEA = {
    "authority": "the National Environment Agency",
    "measure": "wbgt_c",                      # a reading no forecast carries
    "measure_label": "the wet-bulb globe temperature",
    "bands": [{"label": "high heat stress", "min_c": 33},
              {"label": "moderate heat stress", "min_c": 31}],
    "source": "https://www.mse.gov.sg/latest-news/press-release-heat-stress-advisory",
    "incomplete": "WBGT needs solar radiation, which the hourly forecast does not carry.",
}
BOX = (76.3, 9.99, 76.32, 10.0)


class Region:
    def __init__(self, standard=IMD):
        self.manifest = {"title": "Kochi, Kerala"}
        if standard:
            self.manifest["heat_standard"] = standard


def hours(temps=(30, 34, 38), heat_index=None):
    hi = heat_index if heat_index is not None else [t + 4 for t in temps]
    return [{"temperature_c": t, "heat_index_c": h} for t, h in zip(temps, hi)]


@pytest.fixture(autouse=True)
def people(monkeypatch):
    monkeypatch.setattr(popgrid, "density_for", lambda region, bbox, cells=None: {
        "available": True, "people_in_area": 73000, "source": "a population grid"})


# ---- whose threshold -------------------------------------------------------------------------

def test_the_citys_own_authority_is_used_when_the_forecast_carries_its_reading():
    out = heat_exposure.assess(Region(), BOX, hours())
    assert out["judged_against"] == "the India Meteorological Department"
    assert "why_not_the_local_authority" not in out


def test_an_authority_whose_reading_no_forecast_carries_falls_back_and_says_why():
    """Singapore's case. WBGT is never computed from a forecast, and never silently swapped for a
    different index under the authority's name."""
    out = heat_exposure.assess(Region(NEA), BOX, hours())
    assert out["judged_against"] == heat_exposure.FALLBACK["authority"]
    assert "solar radiation" in out["why_not_the_local_authority"]
    assert "National Environment Agency" not in out["judged_against"]


def test_a_city_with_no_authority_staged_falls_back_and_says_so():
    out = heat_exposure.assess(Region(None), BOX, hours())
    assert out["judged_against"] == heat_exposure.FALLBACK["authority"]
    assert "No local heat authority" in out["why_not_the_local_authority"]


def test_a_threshold_that_can_only_be_half_checked_says_so_in_the_findings():
    out = heat_exposure.assess(Region(), BOX, hours())
    assert "never declares a heat wave" in out["only_half_checked"]


# ---- the bands -------------------------------------------------------------------------------

def test_a_reading_matches_the_most_serious_band_it_reaches():
    bands = IMD["bands"]
    assert heat_exposure.band_for(48, bands) == "IMD's severe heat-wave temperature"
    assert heat_exposure.band_for(45, bands) == "IMD's heat-wave temperature"
    assert heat_exposure.band_for(37, bands) == "the temperature IMD requires at a coastal station"


def test_a_reading_below_every_band_matches_none_rather_than_the_lowest():
    assert heat_exposure.band_for(31, IMD["bands"]) is None
    assert heat_exposure.band_for(None, IMD["bands"]) is None


def test_the_peak_and_the_hours_in_each_band_are_counted_from_the_forecast():
    out = heat_exposure.assess(Region(), BOX, hours((30, 38, 46, 39)))
    assert out["highest_forecast_c"] == 46
    assert out["highest_is_hours_from_now"] == 2
    assert out["band_the_forecast_reaches"] == "IMD's heat-wave temperature"
    counts = {b["label"]: b["hours"] for b in out["hours_in_each_band"]}
    assert counts["the temperature IMD requires at a coastal station"] == 3   # 38, 46, 39
    assert counts["IMD's heat-wave temperature"] == 1
    assert counts["IMD's severe heat-wave temperature"] == 0


def test_a_forecast_that_reaches_nothing_says_so_rather_than_naming_the_lowest_band():
    out = heat_exposure.assess(Region(), BOX, hours((28, 30, 31)))
    assert out["band_the_forecast_reaches"] == "none of the bands below"


def test_hours_with_no_reading_are_skipped_not_counted_as_cool():
    out = heat_exposure.assess(Region(), BOX,
                               [{"temperature_c": None}, {"temperature_c": 38}, {}])
    assert out["hours_of_forecast_read"] == 1 and out["highest_forecast_c"] == 38


# ---- a figure the asker supplied --------------------------------------------------------------

def test_a_stated_figure_is_banded_and_labelled_as_the_askers_not_as_a_prediction():
    out = heat_exposure.assess(Region(), BOX, hours((30, 31)), stated_c=46)
    assert out["asked_about_c"] == 46
    assert out["band_the_asked_figure_reaches"] == "IMD's heat-wave temperature"
    assert "stated in the question" in out["where_the_asked_figure_came_from"]
    # the real forecast is still reported beside it, so the two are never confused
    assert out["highest_forecast_c"] == 31


def test_a_what_if_works_even_when_the_forecast_is_missing_entirely():
    out = heat_exposure.assess(Region(), BOX, [], stated_c=46)
    assert out["available"] and out["asked_about_c"] == 46
    assert "highest_forecast_c" not in out


# ---- people ----------------------------------------------------------------------------------

def test_people_come_from_the_grid_and_the_count_says_what_it_is_not():
    out = heat_exposure.assess(Region(), BOX, hours())
    assert out["people_living_here"] == 73000
    assert "never a count of anyone who will fall ill" in out["what_this_count_is"]


def test_no_population_estimate_gives_a_reason_and_never_a_zero(monkeypatch):
    monkeypatch.setattr(popgrid, "density_for",
                        lambda region, bbox, cells=None: {"available": False, "reason": "no grid here"})
    out = heat_exposure.assess(Region(), BOX, hours())
    assert "people_living_here" not in out
    assert out["people_unavailable"] == "no grid here"
