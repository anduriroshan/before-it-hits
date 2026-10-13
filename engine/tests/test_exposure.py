"""Exposure on synthetic footprints where the share, and so the count, is known in advance."""
import pytest
from shapely.geometry import Polygon

from bih_engine import exposure

CRS = "EPSG:32643"          # metric, so footprint areas come out in m2


def sq(lon, lat, half=0.0003):
    return Polygon([(lon - half, lat - half), (lon + half, lat - half),
                    (lon + half, lat + half), (lon - half, lat + half)])


def feat(lon, lat, rank=5, fills=True, half=0.0006):
    return {"type": "Feature", "geometry": sq(lon, lat, half).__geo_interface__,
            "properties": {"id": "d_1", "pooling_index": rank, "fills_at_rainfall": fills}}


CENTRE = (76.315, 10.015)
FAR = (76.360, 10.055)


def test_no_population_config_is_an_honest_not_available():
    out = exposure.estimate([feat(*CENTRE)], [sq(*CENTRE)], CRS, {})
    assert out["available"] is False and "population" in out["reason"]


def test_no_staged_buildings_is_an_honest_not_available():
    pop = {"people": 1_000_000, "built_footprint_m2": 1e6}
    out = exposure.estimate([feat(*CENTRE)], [], CRS, pop)
    assert out["available"] is False and "building" in out["reason"]


def test_people_in_the_worst_spots_track_the_footprint_share():
    # Two identical buildings: one inside the spot, one far outside. Half the footprint is exposed.
    inside, outside = sq(*CENTRE), sq(*FAR)
    import numpy as np
    from pyproj import Transformer
    from shapely import transform as t
    tr = Transformer.from_crs(4326, 32643, always_xy=True)
    tm = lambda g: t(g, lambda c: np.column_stack(tr.transform(c[:, 0], c[:, 1])))
    total_fp = tm(inside).area + tm(outside).area
    pop = {"people": 1_000_000, "built_footprint_m2": total_fp}
    out = exposure.estimate([feat(*CENTRE)], [inside, outside], CRS, pop)
    # inside is one of two equal footprints -> about half the city's people, rounded to 2 sig figs
    assert out["available"] is True
    assert out["people_in_worst_spots"] == pytest.approx(500_000, rel=0.02)
    assert out["buildings_in_worst_spots"] == 1


def test_only_top_ranked_filling_spots_expose_anyone():
    pop = {"people": 1_000_000, "built_footprint_m2": 1e6}
    low = exposure.estimate([feat(*CENTRE, rank=3)], [sq(*CENTRE)], CRS, pop)
    dry = exposure.estimate([feat(*CENTRE, fills=False)], [sq(*CENTRE)], CRS, pop)
    assert low["people_in_worst_spots"] == 0 and dry["people_in_worst_spots"] == 0


def test_a_treated_spot_leaves_the_exposed_set_and_the_denominator_stays_put():
    """F10: same population, same footprints; only the spots the plan acts on stop counting."""
    inside, outside = sq(*CENTRE), sq(*FAR)
    pop = {"people": 1_000_000, "built_footprint_m2": 2e5}
    here, there = feat(*CENTRE), feat(*FAR)
    here["properties"]["id"], there["properties"]["id"] = "d_1", "d_2"
    both = exposure.estimate([here, there], [inside, outside], CRS, pop)
    after = exposure.estimate([here, there], [inside, outside], CRS, pop, treated={"d_1"})
    assert after["people_in_worst_spots"] < both["people_in_worst_spots"]
    assert after["people_in_box"] == both["people_in_box"]
    none_left = exposure.estimate([here, there], [inside, outside], CRS, pop, treated={"d_1", "d_2"})
    assert none_left["people_in_worst_spots"] == 0


def test_the_count_is_rounded_so_it_never_looks_precise():
    assert exposure._round2(0) == 0
    assert exposure._round2(273) == 270
    assert exposure._round2(38641) == 39000
    assert exposure._round2(7) == 7


def test_the_estimate_always_carries_its_caveats():
    pop = {"people": 1_000_000, "built_footprint_m2": 2e5}
    out = exposure.estimate([feat(*CENTRE)], [sq(*CENTRE)], CRS, pop)
    joined = " ".join(out["caveats"]).lower()
    assert "modelled" in joined and "building" in joined and "floor" in joined
