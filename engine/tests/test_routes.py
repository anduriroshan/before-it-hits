"""Naming where a pooling spot actually is. Synthetic roads, so the right answer is known."""
from pyproj import CRS
from shapely.geometry import LineString, Point

from bih_engine.osm import Road
from bih_engine.routes import NEAREST_ROAD_MAX_M, build_graph, nearest_named_roads

CRS_M = CRS.from_epsg(32643)


def roads_at(*specs):
    """Roads as (name, lon0, lat0, lon1, lat1). Near the equator 1e-5 deg is about 1.1 m."""
    return [Road(geom=LineString([(a, b), (c, d)]), highway="residential", name=name)
            for name, a, b, c, d in specs]


def test_a_spot_is_named_by_the_road_beside_it():
    g = build_graph(roads_at(("Pipeline Road", 76.30, 10.00, 76.31, 10.00)), CRS_M)
    edge = next(iter(g.edges(data=True)))[2]["geom"]
    on_the_road = Point(edge.coords[0])
    [found] = nearest_named_roads(g, [on_the_road])
    assert found["name"] == "Pipeline Road" and found["distance_m"] == 0


def test_a_road_too_far_away_is_not_a_landmark():
    g = build_graph(roads_at(("Pipeline Road", 76.30, 10.00, 76.31, 10.00)), CRS_M)
    edge = next(iter(g.edges(data=True)))[2]["geom"]
    x, y = edge.coords[0]
    assert nearest_named_roads(g, [Point(x, y - NEAREST_ROAD_MAX_M - 50)]) == [None]
    assert nearest_named_roads(g, [Point(x, y - NEAREST_ROAD_MAX_M + 20)])[0] is not None


def test_an_unnamed_road_never_wins_over_a_named_one():
    # The unnamed way is nearer, but naming a spot after it would name nothing.
    named = Road(geom=LineString([(76.300, 10.000), (76.310, 10.000)]),
                 highway="primary", name="Seaport Airport Road")
    unnamed = Road(geom=LineString([(76.300, 10.0004), (76.310, 10.0004)]),
                   highway="residential", name=None)
    g = build_graph([named, unnamed], CRS_M)
    x, y = next(d["geom"].coords[0] for _, _, d in g.edges(data=True) if d["name"])
    [found] = nearest_named_roads(g, [Point(x, y + 30)])
    assert found["name"] == "Seaport Airport Road"


def test_no_named_road_anywhere_is_not_an_error():
    unnamed = Road(geom=LineString([(76.30, 10.00), (76.31, 10.00)]),
                   highway="residential", name=None)
    g = build_graph([unnamed], CRS_M)
    assert nearest_named_roads(g, [Point(500_000, 1_100_000)]) == [None]


def test_a_road_floods_at_the_least_rain_of_the_spots_it_crosses():
    import numpy as np
    from pyproj import Transformer
    from shapely.geometry import box
    from bih_engine.routes import road_flood_thresholds
    tr = Transformer.from_crs(4326, 32643, always_xy=True)
    g = build_graph(roads_at(("Pipeline Road", 76.300, 10.000, 76.310, 10.000)), CRS_M)
    x0, y0 = tr.transform(76.305, 10.000)             # a point on the road, in metres
    near = box(x0 - 60, y0 - 60, x0 + 60, y0 + 60)     # a spot straddling the road
    xf, yf = tr.transform(76.330, 10.000)
    far = box(xf - 60, yf - 60, xf + 60, yf + 60)       # a spot nowhere near it
    rows = road_flood_thresholds(g, [(near, 12.0, 5), (far, 3.0, 2)])
    assert len(rows) == 1
    assert rows[0]["rain_to_flood_mm"] == 12.0 and rows[0]["severity"] == 5


def test_two_spots_on_one_road_give_the_earliest_threshold_and_worst_severity():
    import numpy as np
    from pyproj import Transformer
    from shapely.geometry import box
    from bih_engine.routes import road_flood_thresholds
    tr = Transformer.from_crs(4326, 32643, always_xy=True)
    g = build_graph(roads_at(("Long Rd", 76.300, 10.000, 76.320, 10.000)), CRS_M)
    a = box(*(lambda x, y: (x - 50, y - 50, x + 50, y + 50))(*tr.transform(76.305, 10.000)))
    b = box(*(lambda x, y: (x - 50, y - 50, x + 50, y + 50))(*tr.transform(76.315, 10.000)))
    rows = road_flood_thresholds(g, [(a, 40.0, 2), (b, 9.0, 5)])
    assert rows[0]["rain_to_flood_mm"] == 9.0    # the earlier of the two to fill
    assert rows[0]["severity"] == 5              # the worse of the two spots


def test_a_bridge_over_a_spot_is_not_counted_as_flooded():
    import numpy as np
    from pyproj import Transformer
    from shapely.geometry import box, LineString
    from bih_engine.osm import Road
    from bih_engine.routes import road_flood_thresholds
    tr = Transformer.from_crs(4326, 32643, always_xy=True)
    bridge = Road(geom=LineString([(76.300, 10.000), (76.310, 10.000)]),
                  highway="primary", name="Flyover", elevated=True)
    g = build_graph([bridge], CRS_M)
    spot = box(*(lambda x, y: (x - 60, y - 60, x + 60, y + 60))(*tr.transform(76.305, 10.000)))
    assert road_flood_thresholds(g, [(spot, 10.0, 5)]) == []
