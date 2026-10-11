import numpy as np
from scipy.ndimage import gaussian_filter

from scipy.ndimage import label

from bih_engine.hydrology import exit_accumulation, fill_and_route
from helpers import dig, tilted_plane


def border_outlet(shape):
    o = np.zeros(shape, bool)
    o[0, :] = o[-1, :] = o[:, 0] = o[:, -1] = True
    return o


def test_pit_in_a_flat_plane_fills_to_its_rim():
    dem = np.full((60, 60), 10.0)
    dem[25:35, 25:35] = 9.0  # 1 m deep
    filled, _, _ = fill_and_route(dem, border_outlet(dem.shape))
    depth = filled - dem
    assert depth[30, 30] == np.float64(depth[30, 30])
    assert abs(depth[30, 30] - 1.0) < 1e-3
    assert depth[5, 5] < 1e-3  # nothing else is raised


def test_monotone_slope_has_no_depressions():
    dem = tilted_plane()
    filled, _, _ = fill_and_route(dem, border_outlet(dem.shape))
    assert (filled - dem).max() < 1e-3


def traced_catchment(labels, fdir, region):
    """Independent oracle: follow every cell's flow path and count those that enter `region`."""
    from bih_engine.hydrology import D8_DC, D8_DR

    h, w = fdir.shape
    n = 0
    for r0 in range(h):
        for c0 in range(w):
            r, c = r0, c0
            while True:
                if labels[r, c] == region:
                    n += 1
                    break
                k = fdir[r, c]
                if k < 0:
                    break
                r, c = r + D8_DR[k], c + D8_DC[k]
    return n


def test_pit_catchment_matches_an_independent_trace_of_every_flow_path():
    dem = dig(tilted_plane(), 40, 46, 40, 46, below=1.5)
    outlet = border_outlet(dem.shape)
    filled, fdir, acc = fill_and_route(dem, outlet)
    labels, n = label((filled - dem) > 0.1)
    assert n == 1
    expected = traced_catchment(labels, fdir, 1)
    assert expected > 36 + 53 * 6  # more than the six rows through it: the flanking rows drain in too
    assert exit_accumulation(labels, fdir, acc, n)[0] == expected
    # The naive "max accumulation inside the pit" undercounts because the flat has many exits.
    assert acc[labels > 0].max() < 0.5 * expected


def test_random_terrain_conserves_water_and_drains_strictly_downhill():
    rng = np.random.default_rng(42)
    dem = gaussian_filter(rng.normal(size=(150, 150)), 4) * 5
    outlet = border_outlet(dem.shape)
    filled, fdir, acc = fill_and_route(dem, outlet)
    # Every cell's water ends up at an outlet.
    assert acc[outlet].sum() == dem.size
    # Filling only ever raises terrain, and never touches outlets.
    assert (filled >= dem - 1e-12).all()
    assert np.allclose(filled[outlet], dem[outlet])
    # Non-outlet cells all drain somewhere.
    assert (fdir[~outlet] >= 0).all()


def test_an_outlet_at_the_pit_bottom_drains_it():
    dem = dig(tilted_plane(60, 60), 20, 26, 20, 26, below=1.5)
    outlet = border_outlet(dem.shape)
    assert (fill_and_route(dem, outlet)[0] - dem)[20:26, 20:26].max() > 1.0  # holds water
    outlet[22, 22] = True  # open water / a drain inlet at the bottom
    filled, _, _ = fill_and_route(dem, outlet)
    assert (filled - dem)[20:26, 20:26].max() < 1e-3  # no longer holds water


def test_an_outlet_at_rim_height_does_not_lower_the_spill_level():
    # Water still has to rise to the outlet's own elevation before it can leave.
    dem = dig(tilted_plane(60, 60), 20, 26, 20, 26, below=1.5)
    base = fill_and_route(dem, border_outlet(dem.shape))[0]
    outlet = border_outlet(dem.shape)
    outlet[22:24, 14:20] = True
    assert np.allclose(fill_and_route(dem, outlet)[0][20:26, 20:26], base[20:26, 20:26], atol=1e-3)


def test_same_input_same_output():
    rng = np.random.default_rng(7)
    dem = gaussian_filter(rng.normal(size=(80, 80)), 3) * 4
    outlet = border_outlet(dem.shape)
    a = fill_and_route(dem, outlet)
    b = fill_and_route(dem, outlet)
    for x, y in zip(a, b):
        assert np.array_equal(x, y)


def test_water_that_leaves_a_region_and_comes_back_is_counted_once():
    # One row of cells flowing east: A(in) B(out) C(in) D(out). Water leaves at A, re-enters at C.
    labels = np.array([[1, 0, 1, 0]], dtype=np.int32)
    fdir = np.array([[4, 4, 4, -1]], dtype=np.int8)       # 4 = east
    acc = np.array([[1.0, 2.0, 3.0, 4.0]])
    # Cells A, B, C all drain through the region; D is downstream of it. Counting both exits gives 4.
    assert exit_accumulation(labels, fdir, acc, 1)[0] == 3.0


def test_catchment_never_exceeds_the_grid_and_matches_the_traced_oracle_on_irregular_terrain():
    rng = np.random.default_rng(42)
    dem = gaussian_filter(rng.normal(size=(110, 110)), 3) * 4
    outlet = border_outlet(dem.shape)
    filled, fdir, acc = fill_and_route(dem, outlet)
    labels, n = label((filled - dem) > 0.05, structure=np.ones((3, 3)))
    got = exit_accumulation(labels, fdir, acc, n)
    assert n > 5 and got.max() <= dem.size
    for lab in np.argsort(got)[::-1][:5] + 1:             # the largest catchments are where double counting bites
        assert got[lab - 1] == traced_catchment(labels, fdir, int(lab))
