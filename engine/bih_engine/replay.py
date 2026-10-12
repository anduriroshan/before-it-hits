"""F15 replay: run the screening over a real past storm and report, honestly, what it caught.

This is a second ground truth, independent of KSDMA: the neighbourhoods newspapers reported
underwater on 28 May 2024. It is scored with the *same* definition as the KSDMA agreement test, so
the two are directly comparable:

    a "flag" is a top-ranked pooling spot (index >= 4) that fills at the rainfall;
    detection is the share of reported localities within the tolerance of a flag;
    baseline is the share of the whole box a flag covers - the chance a random point is flagged;
    enrichment is detection over baseline, and like the KSDMA test it only means something while
    the baseline is small. Once most of the box is flagged, "near a flag" separates nothing.

That last line is not a hedge, it is the headline. 100 mm in one hour is an extreme that floods
most of low-lying central Kochi in the model, so the baseline is large and the test cannot
separate the reported places from anywhere else. Reporting that plainly - rather than lowering the
rainfall until a number looks good - is the point of running the replay at all. Where the ranking
*does* carry signal is a neighbourhood-sized box at a discriminating return period: that is the
KSDMA result, 1.25-3.26x.

No depth ever leaves here. Spots are ranked internally by the engine's own figures; the output
carries only ranks, distances, counts and shares.
"""
from __future__ import annotations

import numpy as np
import shapely
from pyproj import Transformer
from shapely import transform as shp_transform
from shapely.geometry import Point, box as shp_box, shape

# Kochi sits in UTM 43N. Metres, so buffers and distances mean what they say.
_METRIC_EPSG = 32643

DEFAULT_TOL_M = 400.0
TOP_RANK = 4          # the "top-ranked" class, exactly as the KSDMA agreement test defines it
DISCRIMINATING_BASELINE = 0.4


def _to_metric(tr):
    return lambda geom: shp_transform(geom, lambda c: np.column_stack(tr.transform(c[:, 0], c[:, 1])))


def _in_box(lat, lon, bbox) -> bool:
    w, s, e, n = bbox
    return w <= lon <= e and s <= lat <= n


def score_box(result: dict, localities: list[dict], bbox, *,
              tol_m: float = DEFAULT_TOL_M) -> dict:
    """Score one analysed box against the localities inside it, the KSDMA-parallel way."""
    tr = Transformer.from_crs(4326, _METRIC_EPSG, always_xy=True)
    to_m = _to_metric(tr)
    box_m = to_m(shp_box(*bbox))
    box_area = box_m.area

    top, filling = [], []
    for f in result["geojson"]["features"]:
        p = f["properties"]
        if not p.get("fills_at_rainfall"):
            continue
        g = to_m(shape(f["geometry"]))
        filling.append((p["id"], p["pooling_index"], g))
        if p["pooling_index"] >= TOP_RANK:
            top.append((p["id"], p["pooling_index"], g))

    # The flagged footprint: every top-ranked filling spot, buffered by the ground-truth tolerance.
    # Its share of the box is the chance a point dropped at random lands on a flag.
    flagged = shapely.union_all([g.buffer(tol_m) for _, _, g in top]) if top else None
    baseline = (flagged.intersection(box_m).area / box_area) if flagged is not None else 0.0

    fill_tree = shapely.STRtree([g for _, _, g in filling]) if filling else None
    per_locality = []
    for loc in localities:
        if not _in_box(loc["lat"], loc["lon"], bbox):
            continue
        pt = Point(*tr.transform(loc["lon"], loc["lat"]))
        nearest = None
        if fill_tree is not None:
            i = int(fill_tree.nearest(pt))
            sid, rank, g = filling[i]
            nearest = {"pooling_rank": rank, "distance_m": round(pt.distance(g))}
        per_locality.append({
            "name": loc["name"], "flooded": loc["flooded"],
            "detected": bool(flagged is not None and flagged.contains(pt)),
            "nearest_filling_spot": nearest,
            "report": loc.get("report"), "source": loc.get("source"),
        })

    positives = [r for r in per_locality if r["flooded"]]
    detected = [r for r in positives if r["detected"]]
    rate = (len(detected) / len(positives)) if positives else None
    enrich = (rate / baseline) if (rate is not None and baseline > 0) else None
    return {
        "bbox": list(bbox),
        "grid": result["grid"],
        "spots_total": len(result["screening"]["depressions"]),
        "spots_filling": len(filling),
        "spots_top_ranked": len(top),
        "baseline": round(baseline, 3),
        "discriminating": baseline < DISCRIMINATING_BASELINE,
        "detected": len(detected),
        "positives": len(positives),
        "detection_rate": None if rate is None else round(rate, 3),
        "enrichment": None if enrich is None else round(enrich, 2),
        "per_locality": per_locality,
    }


def pool(box_scores: list[dict]) -> dict:
    """One figure for the event: total detections over a locality-weighted baseline."""
    det = pos = 0
    wbase = wtot = 0.0
    for b in box_scores:
        det += b["detected"]
        pos += b["positives"]
        wbase += b["baseline"] * b["positives"]
        wtot += b["positives"]
    base = (wbase / wtot) if wtot else 0.0
    rate = (det / pos) if pos else None
    enrich = (rate / base) if (rate is not None and base > 0) else None
    discriminating = base < DISCRIMINATING_BASELINE
    return {
        "baseline": round(base, 3),
        "discriminating": discriminating,
        "detected": det,
        "positives": pos,
        "detection_rate": None if rate is None else round(rate, 3),
        "enrichment": None if enrich is None else round(enrich, 2),
        "verdict": (
            "discriminating" if discriminating else
            "saturated: at this rainfall a flagged spot is within the tolerance of most of the "
            "box, so being near one no longer separates the reported places from anywhere else"),
    }


def run(region, event: dict) -> dict:
    """Analyse each event box at the storm's rainfall and score it. Imports the engine lazily so
    this module can be unit-tested without a staged region."""
    from .analyse import analyse_area

    rain = event["rainfall"]
    tol = event.get("scoring", {}).get("tolerance_m", DEFAULT_TOL_M)
    locs = event["localities"]

    box_scores = []
    for name, bbox in event["boxes"].items():
        res = analyse_area(tuple(bbox), rainfall_mm=rain["mm"], res_m=10.0, region=region)
        s = score_box(res, locs, tuple(bbox), tol_m=tol)
        s["box"] = name
        box_scores.append(s)

    placed = {r["name"] for b in box_scores for r in b["per_locality"]}
    return {
        "event": {k: event[k] for k in ("id", "region", "title", "what_happened")},
        "rainfall": rain,
        "scoring": {"tolerance_m": tol,
                    "top_rank": TOP_RANK,
                    "note": event.get("scoring", {}).get("note", "")},
        "boxes": box_scores,
        "pooled": pool(box_scores),
        "localities_not_placed": [loc["name"] for loc in locs if loc["name"] not in placed],
        "method": (
            "Scored the same way as the KSDMA agreement test: a flag is a top-ranked (index >= 4) "
            "pooling spot that fills at the rainfall; a locality is detected if it lies within the "
            "tolerance of a flag; the baseline is the share of the box a flag covers; enrichment is "
            "detection over baseline, and is only meaningful while the baseline is small."),
        "honest": [
            "100 mm in one hour is an extreme that floods most of low-lying central Kochi in the "
            "model, so the baseline is large and the test cannot separate the reported places from "
            "anywhere else. That is the honest result at the event rainfall.",
            "Where the ranking does carry signal is a neighbourhood-sized box at a discriminating "
            "return period: the KSDMA agreement, 1.25-3.26x.",
            "Vyttila is a backwater junction whose flooding is tidal and drainage-driven - exactly "
            "what this rain-ponding model does not claim to capture (no tides, no drains).",
            "The ground truth is neighbourhood-level news, so each locality is an approximate "
            "centroid; the tolerance is set to a neighbourhood, not a street. Absence of a report "
            "is not evidence a place stayed dry.",
        ],
    }
