"""How hard the published design-level earthquake shaking is on the ground people stand on, and how
many people stand on each level of it.

This is the exposure half of what USGS PAGER does, built from the same published pieces and nothing
this product made up:

  rock shaking   GEM's Global Seismic Hazard Map: peak ground acceleration with a one in ten chance of
                 being exceeded in fifty years, on reference rock (Vs30 760-800 m/s).
  the ground     USGS's global Vs30 map: the average shear-wave speed of the top 30 m, estimated from the
                 shape of the land (Allen and Wald 2007; Heath et al. 2020).
  amplification  Borcherdt's (1994) short-period factor, (Vref / Vs30) ** ma, with the exponent ma
                 falling as the input shaking gets stronger - soft ground amplifies weak shaking most.
                 The exponents (0.35, 0.25, 0.10, -0.05) are checked against Table 1 of USGS Open-File
                 Report 2007-1357, whose printed factors they reproduce for every site class.
  intensity      Worden et al. (2012), as USGS ShakeMap implements it: the Modified Mercalli intensity
                 that goes with a peak ground acceleration. Accurate to about two-thirds of an
                 intensity unit either way, so the edge between two bands is soft.

What this deliberately is not. It is a HAZARD level, not a forecast and not an earthquake: a magnitude
alone does not fix shaking at a place, which depends on distance and depth. And it stops at shaking.
Turning shaking into damage, injury or death needs to know what buildings stand here and how they are
built, and this product holds no such thing. PAGER's loss half is not attempted, and nothing in here may
be read as if it were.
"""
from __future__ import annotations

import numpy as np
import rasterio
from rasterio.transform import from_bounds
from rasterio.warp import Resampling, reproject

from . import density, popgrid

G_CM_S2 = 981.0          # the factor USGS ShakeMap uses to turn g into cm/s2
# The speed the hazard map's own "reference rock" sits at: it is stated as 760-800 m/s. A site at this
# speed is the hazard map, unamplified. (OFR 2007-1357 normalises its printed table to 686 m/s, the mean
# of NEHRP class B; the factor is a ratio of speeds, so changing the reference only changes which ground
# counts as "no amplification", and this one is the one the input shaking is actually for.)
V_REF = 780.0
# Borcherdt (1994) short-period exponent by the shaking of the rock beneath, in cm/s2.
# shortcut: the first level (100) is inferred - the printed table leaves that column's label off, though
# the exponents themselves reproduce every printed factor. It only matters between 100 and 150 cm/s2 and
# moves a stiff-soil factor by about 8% at most; both staged cities sit below 100 cm/s2, where the
# first exponent applies whichever way that column is labelled. Check it before staging a stronger place.
MA_AT = (100.0, 150.0, 250.0, 350.0)
MA = (0.35, 0.25, 0.10, -0.05)
WATER = 600.0            # the Vs30 map stores water as exactly this: a placeholder, not a measurement
SCATTER = 0.66           # USGS's own scatter (SMMI) of intensity about the PGA relation, in intensity units
STEP_RATIO = 1.5         # a published value that changes by more than this inside one area is flagged
# What "design level" means in the hazard map's own terms: a 10% chance of being exceeded in 50 years,
# which GEM also files as the 475-year level (it is in the file's name). These are the dataset's
# definition, not something computed here, and they are kept as numbers so a briefing that writes the
# standard phrase "10% in 50 years" can be traced to them.
DESIGN_LEVEL = {"chance_of_being_exceeded_percent": 10, "within_years": 50, "return_period_years": 475}

# NEHRP site classes by Vs30 (m/s), as the Allen and Wald report draws them.
CLASSES = ((180.0, "E", "soft soil"), (360.0, "D", "stiff soil"),
           (760.0, "C", "very dense soil or soft rock"), (float("inf"), "B", "rock"))


def available(region) -> bool:
    meta = region.manifest.get("seismic")
    return (bool(meta) and (region.root / meta["hazard"]["file"]).exists()
            and (region.root / meta["ground"]["file"]).exists())


def amplification(vs30, pga_g):
    """How much the ground at `vs30` (m/s) multiplies the shaking of the rock beneath it (in g)."""
    ma = np.interp(np.asarray(pga_g, float) * G_CM_S2, MA_AT, MA)
    return (V_REF / np.asarray(vs30, float)) ** ma


def intensity(pga_g):
    """Modified Mercalli intensity for a peak ground acceleration in g (Worden et al. 2012).

    The relation runs on log10 of the acceleration in cm/s2, in three straight segments that meet. The
    coefficients are USGS ShakeMap's own (shakelib, gmice/wgrw12): 1.71 + 2.08x below x = 0.14,
    1.78 + 1.55x up to x = 1.57, and -1.60 + 3.70x above. Held to the scale's own range of 1 to 10."""
    x = np.log10(np.asarray(pga_g, float) * G_CM_S2)
    mmi = np.where(x < 0.14, 1.71 + 2.08 * x, np.where(x < 1.57, 1.78 + 1.55 * x, -1.60 + 3.70 * x))
    return np.clip(mmi, 1.0, 10.0)


def site_class(vs30: float) -> tuple[str, str]:
    for top, letter, words in CLASSES:
        if vs30 < top:
            return letter, words
    return CLASSES[-1][1], CLASSES[-1][2]


def _sample(path, bbox, rows, cols) -> np.ndarray:
    """A staged raster read onto the box's own grid, nearest cell, south row first (as the density
    raster is). Nearest, not smoothed: a coarse published cell is shown as the coarse cell it is."""
    w, s, e, n = bbox
    dst = np.full((rows, cols), np.nan, "float32")
    with rasterio.open(path) as ds:
        reproject(rasterio.band(ds, 1), dst, dst_transform=from_bounds(w, s, e, n, cols, rows),
                  dst_crs="EPSG:4326", dst_nodata=np.nan, resampling=Resampling.nearest)
    return dst[::-1]


def _weighted(values: np.ndarray, weights: np.ndarray, qs) -> list[float]:
    o = np.argsort(values)
    cum = np.cumsum(weights[o]) / weights.sum()
    return [float(values[o][min(np.searchsorted(cum, q), len(values) - 1)]) for q in qs]


def exposure(region, bbox) -> dict:
    """The design-level shaking over `bbox`, and how many people stand on each level of it."""
    if not available(region):
        return {"available": False, "reason": "no seismic data is staged for this city"}
    meta = region.manifest["seismic"]
    d = popgrid.density_for(region, tuple(bbox), density._cells_for(tuple(bbox)))
    if not d.get("available"):
        return {"available": False, "reason": d.get("reason", "no population estimate for this area")}
    rows, cols = d["rows"], d["cols"]
    people = popgrid.cell_people(d)
    pga = _sample(region.root / meta["hazard"]["file"], d["bbox"], rows, cols)
    vs = _sample(region.root / meta["ground"]["file"], d["bbox"], rows, cols)

    populated = people > 0
    # A published hazard of exactly zero is a gap in the models' coverage, not a claim of no shaking.
    has_hazard = np.isfinite(pga) & (pga > 0)
    # The Vs30 map writes water as a flat 600 m/s. Coastal cells that hold people can carry it, and
    # reading it as firm ground would quietly understate their shaking, so they are set aside and counted.
    has_ground = np.isfinite(vs) & (vs != WATER)
    use = populated & has_hazard & has_ground
    if not use.any():
        return {"available": False, "reason": "no populated ground here has both a published shaking "
                                              "level and a ground estimate"}
    p, h, v = people[use], pga[use].astype(float), vs[use].astype(float)
    fa = amplification(v, h)
    site_mmi, rock_mmi = intensity(h * fa), intensity(h)
    band, rock_band = np.floor(site_mmi + 0.5).astype(int), np.floor(rock_mmi + 0.5).astype(int)

    def by_band(b):
        return {int(k): float(p[b == k].sum()) for k in np.unique(b)}
    site_people, rock_people = by_band(band), by_band(rock_band)

    letters = np.array([site_class(x)[0] for x in v])
    classes = []
    for _, letter, words in CLASSES:
        share = float(p[letters == letter].sum()) / float(p.sum())
        if share > 0:
            classes.append({"class": letter, "described_as": words, "share_of_people": round(share, 3)})
    p10, med, p90 = _weighted(v, p, (0.1, 0.5, 0.9))

    return {
        "available": True,
        "design_level": DESIGN_LEVEL,
        "people_in_area": d["people_in_area"],
        "rock_pga_g": {"lowest": round(float(h.min()), 3), "highest": round(float(h.max()), 3)},
        "published_map_steps_here": bool(h.max() / h.min() > STEP_RATIO),
        "people_by_intensity": [{"intensity": k, "people": popgrid._two_figures(x)}
                                for k, x in sorted(site_people.items())],
        "intensity_most_people_feel": max(site_people, key=site_people.get),
        "intensity_on_firm_rock": max(rock_people, key=rock_people.get),
        "ground_amplifies_by": round(_weighted(fa, p, (0.5,))[0], 2),
        "ground": {"vs30_typical": round(med), "vs30_most_people_from": round(p10),
                   "vs30_most_people_to": round(p90), "classes": classes},
        "people_with_no_ground_estimate": popgrid._two_figures(float(people[populated & has_hazard & ~has_ground].sum())),
        "people_with_no_hazard_value": popgrid._two_figures(float(people[populated & ~has_hazard].sum())),
        "scatter_in_intensity": SCATTER,
    }
