"""Seismic exposure: the published factors reproduce, the relations behave, and the ways it could quietly
go wrong - water read as firm ground, a seam in the hazard map, a city with nothing staged - are pinned.
Offline: the staged rasters are small synthetic ones, and the population grid is stubbed, because what is
worth testing is the arithmetic and the judgement, not the download."""
import math
from pathlib import Path

import numpy as np
import pytest
import rasterio
from rasterio.transform import from_bounds

from bih_engine import popgrid, seismic

# ---- the amplification reproduces the USGS table it is checked against ----------------------------

# Open-File Report 2007-1357, Table 1, short-period (PGA) factors, normalised to class B (686 m/s).
# Columns are the four input levels of rock shaking. Class: (mean Vs30, factors).
OFR_TABLE_1 = {"C": (464, (1.15, 1.10, 1.04, 0.98)),
               "D": (301, (1.33, 1.23, 1.09, 0.96)),
               "E": (163, (1.65, 1.43, 1.15, 0.93))}


@pytest.mark.parametrize("cls", sorted(OFR_TABLE_1))
def test_the_amplification_reproduces_every_factor_the_usgs_table_prints(cls):
    vs, printed = OFR_TABLE_1[cls]
    for level, want in zip(seismic.MA_AT, printed):
        pga_g = level / seismic.G_CM_S2
        # The table is relative to 686 m/s and ours to the hazard map's rock, so compare the ratio:
        # the factor is a ratio of speeds, which is why changing the reference cannot change this.
        got = seismic.amplification(vs, pga_g) / seismic.amplification(686.0, pga_g)
        assert got == pytest.approx(want, abs=0.006), (cls, level)


def test_the_hazard_maps_own_reference_rock_is_not_amplified():
    for pga in (0.01, 0.09, 0.3):
        assert seismic.amplification(seismic.V_REF, pga) == pytest.approx(1.0)


def test_soft_ground_amplifies_weak_shaking_more_than_strong_shaking():
    weak = seismic.amplification(200.0, 40 / seismic.G_CM_S2)
    strong = seismic.amplification(200.0, 400 / seismic.G_CM_S2)
    assert weak > strong
    assert strong < 1.0          # the table's last exponent is negative: very strong shaking is not amplified


def test_softer_ground_always_amplifies_at_least_as_much_as_stiffer_ground():
    vs = np.array([150.0, 200.0, 300.0, 500.0, 780.0, 1200.0])
    assert np.all(np.diff(seismic.amplification(vs, 0.05)) < 0)


# ---- intensity from acceleration ------------------------------------------------------------------

def test_intensity_matches_the_published_relation_at_known_points():
    # PGA 0.1 g = 98.1 cm/s2, log10 = 1.99167, above the 1.57 break: -1.60 + 3.70x
    assert float(seismic.intensity(0.1)) == pytest.approx(-1.60 + 3.70 * math.log10(98.1), abs=1e-6)
    # 0.02 g = 19.62 cm/s2, log10 = 1.29270, between the breaks: 1.78 + 1.55x
    assert float(seismic.intensity(0.02)) == pytest.approx(1.78 + 1.55 * math.log10(19.62), abs=1e-6)


def test_the_three_segments_of_the_relation_meet_without_a_jump():
    for x in (0.14, 1.57):
        g = 10 ** x / seismic.G_CM_S2
        below, above = float(seismic.intensity(g * 0.9999)), float(seismic.intensity(g * 1.0001))
        assert abs(above - below) < 0.01


def test_stronger_shaking_never_gives_a_lower_intensity():
    sweep = seismic.intensity(np.geomspace(1e-4, 2.0, 300))
    assert np.all(np.diff(sweep) >= 0)


def test_intensity_stays_inside_the_scales_own_range():
    assert float(seismic.intensity(50.0)) == 10.0
    assert float(seismic.intensity(1e-9)) == 1.0


def test_site_classes_change_at_the_boundaries_the_report_draws():
    pairs = [(179.9, "E"), (180.0, "D"), (359.9, "D"), (360.0, "C"), (759.9, "C"), (760.0, "B"), (2000.0, "B")]
    assert [seismic.site_class(v)[0] for v, _ in pairs] == [c for _, c in pairs]


# ---- exposure over a box --------------------------------------------------------------------------

BOX = (76.30, 10.00, 76.32, 10.02)
ROWS = COLS = 4


def write_tif(path: Path, arr: np.ndarray, pad: float = 0.05) -> None:
    w, s, e, n = BOX
    with rasterio.open(path, "w", driver="GTiff", height=arr.shape[0], width=arr.shape[1], count=1,
                       dtype="float32", crs="EPSG:4326", nodata=float("nan"),
                       transform=from_bounds(w - pad, s - pad, e + pad, n + pad, arr.shape[1], arr.shape[0])) as d:
        d.write(arr.astype("float32"), 1)


class Region:
    def __init__(self, root: Path, staged=True):
        self.root = root
        self.manifest = ({"seismic": {"hazard": {"file": "pga.tif"}, "ground": {"file": "vs30.tif"}}}
                         if staged else {})


def density_raster(people_per_cell=2000.0):
    """A 4 x 4 density grid, every cell populated. `values` are people per km2, south row first."""
    area = (((BOX[3] - BOX[1]) / ROWS * 110574.0) * ((BOX[2] - BOX[0]) / COLS * 111320.0
                                                     * math.cos(math.radians(10.01)))) / 1e6
    return {"available": True, "rows": ROWS, "cols": COLS, "bbox": list(BOX),
            "values": [people_per_cell / area] * (ROWS * COLS), "people_in_area": 32000}


@pytest.fixture
def stage(tmp_path, monkeypatch):
    """Stage a hazard and a ground raster that each cover the box with a margin, then expose it."""
    def go(hazard, ground, **kw):
        write_tif(tmp_path / "pga.tif", np.full((6, 6), hazard) if np.isscalar(hazard) else hazard)
        write_tif(tmp_path / "vs30.tif", np.full((6, 6), ground) if np.isscalar(ground) else ground)
        monkeypatch.setattr(popgrid, "density_for", lambda region, bbox, cells=None: density_raster())
        return seismic.exposure(Region(tmp_path, **kw), BOX)
    return go


def test_uniform_ground_and_hazard_put_everyone_in_one_band_and_say_which(stage):
    out = stage(0.09, 250.0)
    assert out["available"]
    assert len(out["people_by_intensity"]) == 1
    # rock 0.09 g = 88 cm/s2 -> 5.6; on 250 m/s ground the factor is about 1.5, so 6.2 -> band VI
    assert out["intensity_most_people_feel"] == 6
    assert out["intensity_on_firm_rock"] == 6
    assert out["ground_amplifies_by"] == pytest.approx((780 / 250) ** 0.35, abs=0.01)
    assert out["ground"]["classes"][0]["class"] == "D"
    assert out["ground"]["classes"][0]["share_of_people"] == 1.0


def test_soft_ground_can_lift_a_band_that_firm_rock_would_not(stage):
    """The reason ground matters: at 0.04 g the rock is intensity IV, and 250 m/s ground makes it V."""
    out = stage(0.041, 250.0)
    assert out["intensity_on_firm_rock"] == 4
    assert out["intensity_most_people_feel"] == 5


def test_water_placeholder_ground_is_set_aside_not_read_as_firm_ground(stage):
    ground = np.full((6, 6), 250.0)
    ground[:, :3] = 600.0                                  # the Vs30 map's value for water
    out = stage(0.09, ground)
    total = sum(b["people"] for b in out["people_by_intensity"]) + out["people_with_no_ground_estimate"]
    assert 0 < out["people_with_no_ground_estimate"] < 32000
    assert total == pytest.approx(32000, rel=0.12)         # nobody is lost, and nobody is guessed at
    assert all(c["class"] != "C" for c in out["ground"]["classes"])     # 600 m/s was never used as ground


def test_a_published_hazard_of_exactly_zero_is_a_gap_not_no_shaking(stage):
    hazard = np.full((6, 6), 0.09)
    hazard[:, :3] = 0.0
    out = stage(hazard, 250.0)
    assert out["people_with_no_hazard_value"] > 0
    assert out["rock_pga_g"]["lowest"] == pytest.approx(0.09, abs=0.001)   # the zeros did not drag it down


def test_an_abrupt_step_in_the_published_map_is_flagged_where_it_falls_inside_the_area(stage):
    hazard = np.full((6, 6), 0.04)
    hazard[:, :3] = 0.02                                    # the Singapore case: a doubling in one cell
    out = stage(hazard, 250.0)
    assert out["published_map_steps_here"] is True
    assert out["rock_pga_g"] == {"lowest": 0.02, "highest": 0.04}


def test_a_smooth_map_is_not_flagged(stage):
    assert stage(0.09, 250.0)["published_map_steps_here"] is False


def test_the_people_shown_in_the_bands_are_never_more_than_live_there(stage):
    out = stage(0.09, 250.0)
    assert sum(b["people"] for b in out["people_by_intensity"]) <= 32000 * 1.06


def test_a_city_with_nothing_staged_says_so_and_claims_nothing(tmp_path):
    out = seismic.exposure(Region(tmp_path, staged=False), BOX)
    assert out == {"available": False, "reason": "no seismic data is staged for this city"}


def test_no_population_estimate_means_no_exposure_claim(tmp_path, monkeypatch):
    write_tif(tmp_path / "pga.tif", np.full((6, 6), 0.09))
    write_tif(tmp_path / "vs30.tif", np.full((6, 6), 250.0))
    monkeypatch.setattr(popgrid, "density_for",
                        lambda region, bbox, cells=None: {"available": False, "reason": "no grid here"})
    assert seismic.exposure(Region(tmp_path), BOX) == {"available": False, "reason": "no grid here"}


def test_the_scatter_in_intensity_travels_with_the_answer(stage):
    assert stage(0.09, 250.0)["scatter_in_intensity"] == 0.66


# ---- the helper that splits a box's people over another map ---------------------------------------

def test_a_cells_people_are_its_density_times_its_own_area():
    d = {"bbox": [0.0, 0.0, 0.02, 0.02], "rows": 2, "cols": 2, "values": [1000, None, 1000, 1000]}
    got = popgrid.cell_people(d)
    one = 1000 * (0.01 * 110574.0) * (0.01 * 111320.0 * math.cos(math.radians(0.005))) / 1e6
    assert got[0, 0] == pytest.approx(one, rel=1e-3)
    assert got[0, 1] == 0.0                                  # an empty cell holds nobody
    assert got.shape == (2, 2)


def test_the_design_level_is_the_datasets_own_definition_and_travels_with_the_answer(stage):
    assert stage(0.09, 250.0)["design_level"] == {
        "chance_of_being_exceeded_percent": 10, "within_years": 50, "return_period_years": 475}
    # 475 years is what 10% in 50 years means: -50 / ln(0.9), rounded to the conventional figure
    assert -50 / math.log(0.9) == pytest.approx(475, abs=1)
