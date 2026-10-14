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
        "timezone": "Asia/Kolkata",
        # Which Landsat scenes make the surface-heat composite. The monsoon blankets Kochi in cloud
        # from June to November, so the composite is the dry pre-monsoon months, where there are
        # enough clear views to take a median of.
        "surface_heat": {"months": [1, 4], "from": "2022-01-01", "to": "2026-04-30",
                         "season": "January to April, the dry months before the monsoon"},
        # Disambiguates from Kochi/Kochi, Japan in the global news index — "Kerala" as an AND term.
        "news_query": '(Kochi OR Ernakulam) Kerala (flood OR flooding OR waterlogging OR rain)',
        # The same place expression without the flood terms, so the other news topics (heat, traffic,
        # air) are searched for the same city the same way.
        "news_place": '(Kochi OR Ernakulam) Kerala',
        # Where people live: the GHS-POP 100 m grid (tools/fetch_population.py), scaled so the staged
        # box adds up to the published total below. The grid says where, the census says how many.
        "population_grid": {"year": 2020},
        # The published total, and the mapped building footprint it used to be spread across. That
        # footprint-only spreading is now the fallback for a city with no grid: measured against
        # Singapore's census it misplaced about a third of the city (tools/validate_population_sg.py).
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
        "timezone": "Asia/Singapore",
        # No dry season to pick: it rains and clouds over all year, so every month contributes.
        "surface_heat": {"months": [1, 12], "from": "2022-01-01", "to": "2026-04-30",
                         "season": "all months of the year, as Singapore has no dry season"},
        # Singapore is unambiguous in the global news index, so no disambiguating AND term is needed.
        "news_query": 'Singapore (flood OR flooding OR "flash flood" OR "heavy rain" OR ponding OR waterlogging)',
        "news_place": 'Singapore',
        # A live, keyless rain-gauge network — ~90 NEA gauges at 5-minute cadence (data.gov.sg).
        # No other staged city has an open feed like this; surfaced as observed rain, not forecast.
        "live_rainfall": {"source": "nea_sg", "label": "NEA rain gauges (data.gov.sg)",
                          "cadence": "5-minute totals"},
        # Where people live: the GHS-POP 100 m grid, scaled to the published total below. The staged
        # box includes a thin strip of Johor, which takes a little of the total: small, and said.
        "population_grid": {"year": 2020},
        # The published total. OSM footprints here are near-complete, and still footprint area is not
        # people: spread over footprints alone, the 2020 census by planning area was misplaced by a
        # third (industrial Tuas got 6.5% of the city and has no residents). `built_footprint_m2` is
        # the footprint-only fallback (158,230 footprints, summed in EPSG:32648 / UTM 48N).
        "population": {
            "people": 5917648,
            "extent": "Singapore, total population mid-2023 (SingStat)",
            "source": "https://www.singstat.gov.sg/ (Singapore total population, mid-2023)",
            "built_footprint_m2": 95911957,
            "note": "Total population placed by a modelled grid. The census check is of residents "
                    "only, so the dormitory workers in the published total are spread like residents. "
                    "An order of magnitude for who is exposed, by area.",
        },
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
