"""The replay scorer on synthetic boxes where the answer is known. No staged data, no network:
`run()` needs a real region, but the scoring that `run()` wraps is pure geometry and is what can
be wrong, so that is what is tested here."""
import pytest

from bih_engine import replay


def sq(lon, lat, half=0.0003):
    return {"type": "Polygon", "coordinates": [[
        [lon - half, lat - half], [lon + half, lat - half],
        [lon + half, lat + half], [lon - half, lat + half], [lon - half, lat - half]]]}


def feat(fid, lon, lat, rank, fills):
    return {"type": "Feature", "geometry": sq(lon, lat),
            "properties": {"id": fid, "pooling_index": rank, "fills_at_rainfall": fills,
                           "fill_order": 1, "low_confidence": False, "contributing_ha": 1.0}}


def result(features):
    return {"screening": {"depressions": [{"id": f["properties"]["id"]} for f in features]},
            "geojson": {"type": "FeatureCollection", "features": features},
            "grid": {"shape": [330, 330], "res_m": 10.0, "area_km2": 10.9}}


# A ~3 km box. Centre is far (>400 m) from the corners, so tolerance does not reach across it.
BOX = (76.300, 10.000, 76.330, 10.030)
CENTRE = (76.315, 10.015)
CORNER = (76.3015, 10.0015)


def loc(name, lon, lat, flooded=True):
    return {"name": name, "lat": lat, "lon": lon, "flooded": flooded}


def test_a_locality_on_a_top_ranked_filling_spot_is_detected():
    res = result([feat("d_1", *CENTRE, rank=5, fills=True)])
    s = replay.score_box(res, [loc("Hit", *CENTRE)], BOX)
    assert s["detected"] == 1 and s["positives"] == 1
    assert s["per_locality"][0]["detected"] is True


def test_a_locality_far_from_every_flag_is_not_detected():
    res = result([feat("d_1", *CENTRE, rank=5, fills=True)])
    s = replay.score_box(res, [loc("Miss", *CORNER)], BOX)
    assert s["detected"] == 0 and s["per_locality"][0]["detected"] is False


def test_a_low_ranked_spot_is_not_a_flag():
    # rank 3 is below the top class, so nothing is flagged and the baseline is zero.
    res = result([feat("d_1", *CENTRE, rank=3, fills=True)])
    s = replay.score_box(res, [loc("Hit", *CENTRE)], BOX)
    assert s["spots_top_ranked"] == 0 and s["baseline"] == 0.0
    assert s["per_locality"][0]["detected"] is False


def test_a_top_ranked_spot_that_stays_dry_is_not_a_flag():
    res = result([feat("d_1", *CENTRE, rank=5, fills=False)])
    s = replay.score_box(res, [loc("Hit", *CENTRE)], BOX)
    assert s["spots_filling"] == 0 and s["baseline"] == 0.0


def test_the_baseline_is_the_flagged_share_of_the_box():
    # One small spot buffered 400 m in an ~11 km2 box covers a small, discriminating share.
    res = result([feat("d_1", *CENTRE, rank=5, fills=True)])
    s = replay.score_box(res, [loc("Hit", *CENTRE)], BOX, tol_m=400)
    assert 0.0 < s["baseline"] < 0.2 and s["discriminating"] is True


def test_enrichment_falls_as_flags_blanket_the_box():
    # Tile the box with top-ranked spots: a flag ends up near everywhere, baseline is large, and
    # a high detection rate stops meaning anything - the saturated case the event actually hits.
    feats = [feat(f"d_{i}_{j}", 76.301 + 0.004 * i, 10.001 + 0.004 * j, 5, True)
             for i in range(7) for j in range(7)]
    here = loc("Hit", *CENTRE)
    s = replay.score_box(result(feats), [here], BOX, tol_m=400)
    assert s["baseline"] > 0.4 and s["discriminating"] is False


def test_a_non_flooded_locality_is_not_counted_as_a_positive():
    res = result([feat("d_1", *CENTRE, rank=5, fills=True)])
    s = replay.score_box(res, [loc("Palarivattom", *CENTRE, flooded=False)], BOX)
    assert s["positives"] == 0 and s["detection_rate"] is None


def test_localities_outside_the_box_are_ignored():
    res = result([feat("d_1", *CENTRE, rank=5, fills=True)])
    s = replay.score_box(res, [loc("Elsewhere", 80.0, 13.0)], BOX)
    assert s["per_locality"] == [] and s["positives"] == 0


def test_pool_weights_the_baseline_by_how_many_localities_each_box_has():
    a = {"baseline": 0.1, "detected": 2, "positives": 2}
    b = {"baseline": 0.9, "detected": 1, "positives": 6}
    pooled = replay.pool([a, b])
    assert pooled["detected"] == 3 and pooled["positives"] == 8
    assert pooled["baseline"] == pytest.approx((0.1 * 2 + 0.9 * 6) / 8, abs=1e-3)
    assert pooled["discriminating"] is False and "saturated" in pooled["verdict"]
