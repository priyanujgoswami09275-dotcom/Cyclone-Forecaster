"""Module B — simulation engine.

Submodules:
    dem       — DEM loading/caching (single source of truth for terrain + geo-math)
    surge     — wind -> storm surge height via the trained regression
    flood     — time-stepped BFS flood propagation over the DEM grid
    exposure  — infrastructure intersection with a flood frame
    routing   — road network graph + Dijkstra safe routes
    shelters  — curated shelter locations and capacities
    allocation— shelter allocation LP (transportation problem)
    population— at-risk population estimate per locality
"""
