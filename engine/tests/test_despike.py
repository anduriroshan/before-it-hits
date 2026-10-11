import numpy as np

from bih_engine.terrain import despike_low


def flat(n=40, z=2.5):
    rng = np.random.default_rng(0)
    return (z + rng.normal(0, 0.15, (n, n))).astype(np.float32)  # gentle noise, well under the threshold


def test_single_and_double_pixel_holes_are_removed():
    a = flat()
    a[10, 10] = -6.0           # 1 px, ~8 m under its neighbours
    a[20, 20] = a[20, 21] = -7.0  # 2 px blob
    out, n = despike_low(a, 3.0)
    assert n == 3
    assert out[10, 10] > 2 and out[20, 20] > 2 and out[20, 21] > 2


def test_a_real_broad_depression_survives():
    a = flat()
    a[10:17, 10:17] -= 4.0     # 7x7 px (about 200 m) basin, 4 m deep: terrain, not a spike
    out, n = despike_low(a, 3.0)
    assert (out[12:15, 12:15] < -1).all()  # the centre keeps its depth
    assert n < 49  # at most the rim pixels can be touched


def test_ordinary_noise_is_left_alone():
    a = flat()
    out, n = despike_low(a, 3.0)
    assert n == 0 and np.array_equal(out, a)


def test_high_outliers_are_not_touched():
    a = flat()
    a[10, 10] = 12.0
    out, n = despike_low(a, 3.0)
    assert n == 0 and out[10, 10] == 12.0


def test_pixels_next_to_no_data_are_left_alone():
    a = flat()
    a[:, :10] = -9999.0         # sea / outside coverage
    a[15, 10] = -6.0           # a "spike" on the coastline: cannot tell it from a shore, so keep it
    out, n = despike_low(a, 3.0)
    assert n == 0 and out[15, 10] == -6.0 and (out[:, :10] == -9999.0).all()


def test_the_image_border_is_left_alone():
    a = flat()
    a[0, 5] = -8.0
    out, n = despike_low(a, 3.0)
    assert n == 0
