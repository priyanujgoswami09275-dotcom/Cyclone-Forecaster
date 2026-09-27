"""Road network graph and flood-aware evacuation routing.

**Why not osmnx.** CLAUDE.md names osmnx + networkx for this, and networkx IS
used. osmnx is not, because it builds its graph by querying the Overpass API
live — which Rules.md forbids outright ("never call Overpass ... live from a
request handler") and which would additionally re-download the whole network
on Render's free tier. Instead the graph is assembled from the committed
`data/roads.geojson`, which is pre-fetched exactly as Rules.md requires.

That file stores bare LineStrings with no node ids, so connectivity is
recovered by snapping: coordinates are rounded to ~1 m and endpoints sharing a
rounded coordinate become the same graph node. 1 m is well below the DEM's
~50 m cell, so it cannot merge distinct junctions, and it absorbs the
coordinate noise that would otherwise split a single road into fragments.

Routing then runs Dijkstra (via networkx) over the graph with every edge that
intersects the flood extent removed, so a route never crosses water. If no
path survives, the caller is told the origin is cut off — silence there would
read as "safe", which is the dangerous direction to be wrong in.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from functools import lru_cache

import networkx as nx
from shapely.geometry import LineString, Point
from shapely.ops import unary_union
from shapely.prepared import prep

from .exposure import _line_features, _flood_shape, DATA_DIR

# Road layers merged into the routable graph, in priority order.
ROAD_FILES = ("roads.geojson", "delta_roads.geojson")

# Endpoint coordinates are snapped to this many decimal places (~1 m) to
# recover node identity from bare LineStrings.
SNAP_PRECISION = 5

# An origin/shelter further than this from any road is treated as off-network.
# Sagar Island's mapped roads are sparse, so this is generous enough to snap a
# village centroid onto the nearest way without inventing a connection across
# open water.
MAX_SNAP_M = 2_000.0

# Approximate metres per degree of longitude at ~22 deg N (the bbox centre).
# Exact enough for ranking routes, and far cheaper than a full projection.
M_PER_DEG_LON = 102_000.0


def _snap(coords: list[float]) -> tuple[float, float]:
    return (round(coords[0], SNAP_PRECISION), round(coords[1], SNAP_PRECISION))


def _haversine_m(a: tuple[float, float], b: tuple[float, float]) -> float:
    """Great-circle distance in metres between two (lon, lat) points."""
    lon1, lat1, lon2, lat2 = map(math.radians, (*a, *b))
    dlon, dlat = lon2 - lon1, lat2 - lat1
    h = math.sin(dlat / 2) ** 2 + math.cos(lat1) * math.cos(lat2) * math.sin(dlon / 2) ** 2
    return 2 * 6_371_000 * math.asin(math.sqrt(h))


@lru_cache(maxsize=1)
def build_road_graph() -> nx.Graph:
    """Assemble a routable graph from the committed roads GeoJSON.

    Nodes are snapped endpoints; edges carry `length_m` (haversine) and the
    source road's name/highway class. Cached per process — building it walks
    several thousand ways and is far too slow to repeat per request.

    Two files are merged: `roads.geojson` (mainland arterials) and
    `delta_roads.geojson` (Sagar Island / southern delta, where OSM uses
    tertiary/unclassified classes that the arterial fetch filter excludes).
    Without the second file Sagar Island — the case study's landfall point —
    has no network at all, and routing there always fails.
    """
    graph = nx.Graph()
    for filename in ROAD_FILES:
        if not (DATA_DIR / filename).exists():
            continue
        for geometry, props in _line_features(filename):
            coordinates = list(geometry.coords)
            if len(coordinates) < 2:
                continue
            snapped = [_snap(c) for c in coordinates]
            # Collapse consecutive duplicates created by snapping.
            deduped = [snapped[0]]
            for point in snapped[1:]:
                if point != deduped[-1]:
                    deduped.append(point)
            if len(deduped) < 2:
                continue

            for u, v in zip(deduped, deduped[1:]):
                length = _haversine_m(u, v)
                if length <= 0:
                    continue
                # Keep the shortest parallel edge if two roads share endpoints
                # (service loops, dual carriageways) — the shorter is the better
                # evacuation option, and it keeps the graph simple for Dijkstra.
                if graph.has_edge(u, v) and graph[u][v]["length_m"] <= length:
                    continue
                tags = props.get("tags", props) or {}
                graph.add_edge(
                    u,
                    v,
                    length_m=length,
                    highway=tags.get("highway"),
                    name=tags.get("name") or tags.get("name:en") or "",
                    osm_id=props.get("osm_id"),
                )
    return graph


def flooded_edges(graph: nx.Graph, flood) -> set[tuple]:
    """Edges whose geometry passes through the flood extent.

    Edges are reconstructed as LineStrings from their snapped endpoints, which
    is a slight simplification of the original way geometry — at this snapping
    tolerance the difference is metres, well inside the ~50 m DEM cell.
    """
    geom = _flood_shape(flood)
    if geom is None:
        return set()
    prepared = prep(geom)
    blocked = set()
    for u, v, data in graph.edges(data=True):
        if prepared.intersects(LineString([u, v])):
            blocked.add((u, v))
            blocked.add((v, u))
    return blocked


def largest_component(graph: nx.Graph) -> nx.Graph:
    """The subgraph holding the most nodes.

    A 3.6 m surge severs the delta into disconnected islands. Routes are only
    meaningful within a component, so routing runs on this one and anything
    unreachable is genuinely unreachable rather than merely far.
    """
    if graph.number_of_nodes() == 0:
        return graph
    return graph.subgraph(max(nx.connected_components(graph), key=len)).copy()


@dataclass(frozen=True)
class Route:
    origin: tuple[float, float]
    destination: tuple[float, float]
    coordinates: list[tuple[float, float]]
    length_m: float
    reachable: bool
    reason: str = ""

    def to_dict(self) -> dict:
        return {
            "origin_lonlat": list(self.origin),
            "destination_lonlat": list(self.destination),
            "coordinates": [list(c) for c in self.coordinates],
            "length_m": round(self.length_m, 1),
            "length_km": round(self.length_m / 1000, 2),
            "reachable": self.reachable,
            "reason": self.reason,
        }


def safe_route(
    graph: nx.Graph,
    flood,
    origin: tuple[float, float],
    destination: tuple[float, float],
) -> Route:
    """Shortest path from origin to destination avoiding flooded edges.

    Returns an unreachable Route (rather than raising) when the flood severs
    the two: for an evacuation tool, "no safe route exists" is a result the
    caller must act on, not an error to swallow.
    """
    o, d = _snap(origin), _snap(destination)
    if not graph.has_node(o):
        # Snap to the nearest graph node so a coordinate that missed the
        # network by a few metres — or a village centroid on a sparse island —
        # still routes. Bounded by MAX_SNAP_M so nothing is connected across
        # open water.
        nearest = min(graph.nodes, key=lambda n: _haversine_m(o, n), default=None)
        if nearest is None or _haversine_m(o, nearest) > MAX_SNAP_M:
            return Route(
                origin, destination, [], 0.0, False, "origin not on road network"
            )
        o = nearest
    if not graph.has_node(d):
        nearest = min(graph.nodes, key=lambda n: _haversine_m(d, n), default=None)
        if nearest is None or _haversine_m(d, nearest) > MAX_SNAP_M:
            return Route(
                origin, destination, [], 0.0, False, "shelter not on road network"
            )
        d = nearest

    blocked = flooded_edges(graph, flood)
    safe = graph.edge_subgraph(
        [e for e in graph.edges() if (e[0], e[1]) not in blocked]
    ).copy()

    try:
        path = nx.shortest_path(safe, o, d, weight="length_m")
    except nx.NetworkXNoPath:
        return Route(
            origin, destination, [], 0.0, False, "no flood-free route: origin cut off"
        )
    except nx.NodeNotFound:
        return Route(origin, destination, [], 0.0, False, "endpoint not reachable")

    length = nx.path_weight(safe, path, weight="length_m")
    return Route(origin, destination, path, float(length), True)
