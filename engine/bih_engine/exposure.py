"""F7: roughly how many people are in the worst pooling spots, modelled - by area, never per home.

The model is one line: spread the city's published population across its mapped building
footprints by area, then add up the footprint that sits inside the top-ranked spots.

    people in the worst spots  =  city population x (footprint in those spots / all city footprint)

Why spread by footprint, and why this particular ratio: OpenStreetMap maps only a fraction of the
buildings in an Indian city, so a raw "people per building" would be badly wrong. But the *same*
partial footprints appear in both the numerator and the denominator, so if the mapping is missing
buildings roughly evenly, the under-counting cancels and the share is about right. That is the only
reason a partial dataset can be used here honestly.

What this deliberately does not do:
  - No per-building claim. A building only spreads the count; no number is attached to one home.
  - No floors. Footprint area is the proxy for floor area, because OSM has no storey count here. A
    tower and a shed with the same footprint are treated alike, which overstates low-rise and
    understates high-rise.
  - No real person's data. It is a published total pushed around by geometry.

Reported as "roughly", rounded hard, with the caveats attached every time. It is an order of
magnitude for who is exposed, not a roll call.
"""
from __future__ import annotations

import numpy as np
import shapely
from pyproj import Transformer
from shapely import transform as shp_transform
from shapely.geometry import shape

TOP_RANK = 4     # the same "top-ranked" class the hazard ranking and the KSDMA test use


def _round2(x: float) -> int:
    """Two significant figures, then to an int. A modelled count should not look precise."""
    if x <= 0:
        return 0
    mag = 10 ** (int(np.floor(np.log10(x))) - 1)
    return int(round(x / mag) * mag)


def _grid_estimate(features: list[dict], population: dict, treated: set, region, bbox) -> dict:
    """The same answer from the population grid (popgrid.py): who lives on the ground the worst spots
    cover, with a cell's people placed on its mapped buildings and spread evenly where it has none."""
    from . import popgrid
    spots = [shape(f["geometry"]) for f in features
             if f["properties"]["pooling_index"] >= TOP_RANK
             and f["properties"].get("fills_at_rainfall")
             and f["properties"].get("id") not in treated]
    got = popgrid.people_in(region, spots)
    box = popgrid.raster(region, tuple(bbox))
    meta = region.manifest["population_grid"]
    check = meta.get("validation")
    if check:
        # Said at the point of use, with the number, so the reader is not left to find the README.
        wrong = round(check["grid"]["share_of_people_in_the_wrong_planning_area"] * 100)
        was = round(check["footprint_only"]["share_of_people_in_the_wrong_planning_area"] * 100)
        checked = (f"Checked against the {check['against'].split(',')[0]} by planning area: about {wrong}% of "
                   f"the city's people land in the wrong planning area, against about {was}% for spreading "
                   "the total over building footprints. The census counts residents only.")
    else:
        checked = ("Not checked against a census by small area: none is staged for this city, so how "
                   "well the grid places people here is unverified.")
    return {
        "available": True,
        "people_in_worst_spots": _round2(got["people"]),
        "people_in_box": box["people_in_area"] if box.get("available") else 0,
        "buildings_in_worst_spots": got["buildings"],
        "affected_footprint_m2": round(got["footprint_m2"]),
        "method": "grid",
        "basis": {
            "population": population["people"],
            "population_extent": population.get("extent"),
            "source": population.get("source"),
            "grid": meta["source"],
        },
        "caveats": [
            "A modelled distribution, not a count of residents: a satellite-and-census population grid "
            "of about 100 m, scaled so the city adds up to its published population.",
            "By area only. No number is attached to any individual building.",
            "Inside a spot, a cell's people are placed on its mapped buildings by footprint, so open "
            "ground holds nobody; where a cell has people but no mapped building they are spread evenly "
            "over the cell. OpenStreetMap maps only part of the buildings in some cities.",
            checked,
        ],
    }


def estimate(features: list[dict], buildings, crs, population: dict,
             treated: set | None = None, region=None, bbox=None) -> dict:
    """`features` are the screening's pooling-spot geojson (lon/lat); `buildings` are shapely
    footprints in lon/lat; `population` is the region manifest block with `people` and
    `built_footprint_m2`. Returns a modelled exposure, or an honest 'not available'.

    `treated` is a set of spot ids to leave out of the exposed set - the spots a preparation plan
    would deal with, for the best-case "after" of F10. It changes which footprint is summed and
    nothing else: the population, the denominator and the method are the same as the "before".

    With `region` and `bbox`, and a staged population grid for that region, the people come from the
    grid (see popgrid.py). Without one, from the published total spread over footprints as before."""
    people = population.get("people")
    region_fp = population.get("built_footprint_m2")
    if region is not None and bbox is not None and people:
        from . import popgrid
        if popgrid.available(region):
            return _grid_estimate(features, population, treated or set(), region, bbox)
    if not people or not region_fp:
        return {"available": False,
                "reason": "no published population is configured for this region"}
    if not buildings:
        return {"available": False,
                "reason": "building footprints are not staged for this region, so exposure cannot "
                          "be modelled here"}

    tr = Transformer.from_crs(4326, crs, always_xy=True)
    to_m = lambda g: shp_transform(g, lambda c: np.column_stack(tr.transform(c[:, 0], c[:, 1])))  # noqa: E731

    b_m = [to_m(b) for b in buildings]
    box_footprint = float(sum(b.area for b in b_m))

    treated = treated or set()
    top = [to_m(shape(f["geometry"])) for f in features
           if f["properties"]["pooling_index"] >= TOP_RANK
           and f["properties"].get("fills_at_rainfall")
           and f["properties"].get("id") not in treated]
    affected_footprint = 0.0
    buildings_touched = 0
    if top:
        top_union = shapely.union_all(top)
        tree = shapely.STRtree(b_m)
        for i in tree.query(top_union, predicate="intersects"):
            inter = b_m[int(i)].intersection(top_union).area
            if inter > 0:
                affected_footprint += inter
                buildings_touched += 1

    scale = people / region_fp     # people per m2 of mapped footprint, city-wide
    return {
        "available": True,
        "people_in_worst_spots": _round2(affected_footprint * scale),
        "people_in_box": _round2(box_footprint * scale),
        "buildings_in_worst_spots": buildings_touched,
        "affected_footprint_m2": round(affected_footprint),
        "method": "footprint",
        "basis": {
            "population": people,
            "population_extent": population.get("extent"),
            "source": population.get("source"),
        },
        "caveats": [
            "A modelled distribution, not a count of residents: the city's published population "
            "spread across mapped building footprints by area, then summed inside the worst spots.",
            "By area only. No number is attached to any individual building.",
            "Footprint stands in for floor area - OSM has no storey count here - so high-rise is "
            "understated and low-rise overstated.",
            "OpenStreetMap maps only part of the buildings in the city; the estimate assumes that "
            "under-mapping is roughly even, which is why a share rather than a raw count is used.",
        ],
    }
