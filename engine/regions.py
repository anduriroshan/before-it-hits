"""Region definitions: everything staging needs to onboard a city.

Adding a city is a config entry plus running the staging tools. No engine code changes — the
screening is terrain physics, so it is not city-specific. What *is* city-specific is which terrain
covers it, which official hazard layer exists (usually none), and which places are worth offering
as a starting point.

Boxes are grid-aligned to DeltaDTM's native 1-arcsecond grid by the terrain fetcher, so staging
never resamples the published elevations.
"""
from __future__ import annotations

REGIONS: dict[str, dict] = {
    "kochi": {
        "title": "Kochi, Kerala",
        "country": "India",
        # 0.25 deg square; Kochi sits 0-7 m above sea level, fully inside DeltaDTM's coastal coverage.
        "bbox_wsen": [76.20, 9.85, 76.45, 10.10],
        "note": (
            "Monsoon city on the backwaters. Chosen because KSDMA publishes depth-valued flood "
            "hazard rasters by return period, which is what the validated tier is checked against."
        ),
        "official": "ksdma",
        # For F7 exposure: a published population and the total mapped building footprint it is
        # spread across. `built_footprint_m2` is measured from the staged buildings.fgb (sum of
        # footprint areas in metres), so the ratio is self-consistent - the same partial OSM
        # footprints sit in both the city total and any one spot, so even under-mapping cancels.
        "population": {
            "people": 2117990,
            "extent": "Kochi urban agglomeration, 2011 Census of India",
            "source": "https://censusindia.gov.in/ (Kochi UA, 2011)",
            "built_footprint_m2": 24499608,
            "note": "The staged box is greater Kochi and is not an exact match for the UA boundary, "
                    "so the count is an order of magnitude, not a precise total.",
        },
        # Localities named in the 28 May 2024 cloudburst reporting.
        "presets": [
            {"label": "Edappally", "bbox": [76.295, 10.010, 76.325, 10.040]},
            {"label": "Palarivattom", "bbox": [76.2968, 9.9967, 76.3168, 10.0167]},
            {"label": "Vyttila", "bbox": [76.305, 9.955, 76.330, 9.980]},
            {"label": "Kakkanad", "bbox": [76.330, 10.000, 76.360, 10.028]},
        ],
    },
    "singapore": {
        "title": "Singapore",
        "country": "Singapore",
        # Main island plus Jurong. DeltaDTM covers ~97.5% of it (probed 11 Oct 2026).
        "bbox_wsen": [103.60, 1.21, 104.05, 1.48],
        "note": (
            "About 2,300 mm of rain a year in short, intense convective bursts. The interesting "
            "case for this product: Singapore has one of the best drainage networks in the world, "
            "and we have no drainage data for it at all. Our screening should therefore overstate "
            "pooling here more than anywhere else — which is exactly the limit we disclose on "
            "every run, now visible rather than theoretical."
        ),
        "official": None,  # no public depth-valued hazard raster found; the UI must say so
        # Places with a documented flash-flood history.
        "presets": [
            {"label": "Orchard Road", "bbox": [103.825, 1.295, 103.845, 1.312]},
            # The hill box is 23% above DeltaDTM's 30 m ceiling; this is the canal reach,
            # which is both usable terrain and the actual flood location.
            {"label": "Bukit Timah Rd", "bbox": [103.800, 1.320, 103.825, 1.340]},
            {"label": "Geylang / Kallang", "bbox": [103.860, 1.305, 103.885, 1.325]},
            {"label": "Jurong East", "bbox": [103.730, 1.325, 103.755, 1.345]},
        ],
    },
}


def get(name: str) -> dict:
    if name not in REGIONS:
        raise SystemExit(f"unknown region {name!r}; known: {', '.join(sorted(REGIONS))}")
    return dict(REGIONS[name], name=name)
