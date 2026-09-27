"""Shelter allocation as a capacitated transportation problem (LP).

For each demand node (a locality's exposed population) and each shelter, the
decision variable x[i][j] is the number of people sent from node i to shelter
j. The objective minimises total person-kilometres travelled; the constraints
are that every node's demand is fully met, and no shelter exceeds capacity.

This is a genuine linear program solved by `scipy.optimize.linprog`
(HI-GHS), not a greedy nearest-shelter heuristic — the whole point of the
formulation is that it trades off distance against capacity across the whole
district at once, which greedy assignment cannot do.

**Data limitation, enforced not hidden:** the shelter list is currently empty
(see shelters.py for the full provenance), so `allocate_shelters` is normally
run against the explicitly-labelled demo shelters. Every result carries the
`shelter_dataset_status()` block, which sets `is_demo_data: true` and spells
out that the assignment must not be used to direct a real evacuation. The
algorithm is real; the inputs are placeholders, and the output says so.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

import numpy as np
from scipy.optimize import linprog

from .shelters import Shelter, demo_shelters, load_shelters, shelter_dataset_status


@dataclass(frozen=True)
class DemandNode:
    """A place with people who need a shelter."""

    name: str
    lon: float
    lat: float
    population: int


def _haversine_km(a: tuple[float, float], b: tuple[float, float]) -> float:
    lon1, lat1, lon2, lat2 = map(math.radians, (*a, *b))
    dlon, dlat = lon2 - lon1, lat2 - lat1
    h = math.sin(dlat / 2) ** 2 + math.cos(lat1) * math.cos(lat2) * math.sin(dlon / 2) ** 2
    return 2 * 6371.0 * math.asin(math.sqrt(h))


def distance_matrix(nodes: list[DemandNode], shelters: list[Shelter]) -> np.ndarray:
    """(n_nodes, n_shelters) matrix of great-circle distances in km."""
    return np.array(
        [
            [_haversine_km((n.lon, n.lat), (s.lon, s.lat)) for s in shelters]
            for n in nodes
        ],
        dtype=float,
    )


def allocate_shelters(
    nodes: list[DemandNode],
    shelters: list[Shelter] | None = None,
) -> dict:
    """Assign every demand node's population to shelters, capacity-aware.

    Returns a dict with the assignment matrix (people per node-shelter pair),
    per-shelter loads, unmet demand, and the shelter-dataset status.
    """
    shelters = shelters if shelters is not None else (load_shelters() or demo_shelters())
    status = shelter_dataset_status()

    demand = np.array([n.population for n in nodes], dtype=float)
    nodes_with_demand = [i for i, d in enumerate(demand) if d > 0]
    if not nodes_with_demand or not shelters:
        return {
            "assignment": [],
            "shelter_loads": [
                {"shelter": s.name, "capacity_people": s.capacity_people, "assigned": 0}
                for s in shelters
            ],
            "unmet_demand": int(demand.sum()),
            "status": status,
            "message": "no demand or no shelters to allocate",
        }

    dist = distance_matrix(nodes, shelters)
    n_nodes, n_shelters = dist.shape

    # Decision vector: x[i * n_shelters + j] = people from i to j.
    c = dist.flatten()
    num_vars = n_nodes * n_shelters

    # Equality: each node's demand is fully met.
    A_eq = np.zeros((n_nodes, num_vars))
    for i in range(n_nodes):
        A_eq[i, i * n_shelters : (i + 1) * n_shelters] = 1
    b_eq = demand

    # Inequality: each shelter's total load <= its capacity.
    A_ub = np.zeros((n_shelters, num_vars))
    for j in range(n_shelters):
        A_ub[j, j::n_shelters] = 1
    b_ub = np.array([s.capacity_people for s in shelters], dtype=float)

    result = linprog(
        c,
        A_ub=A_ub,
        b_ub=b_ub,
        A_eq=A_eq,
        b_eq=b_eq,
        bounds=(0, None),
        method="highs",
    )

    if not result.success:
        # The classic infeasibility: total capacity < total demand. Report the
        # shortfall explicitly rather than returning a partial or zero answer,
        # because "we cannot shelter everyone" is the operationally critical
        # fact an LP that silently returns nothing would hide.
        total_demand = int(demand.sum())
        total_capacity = int(b_ub.sum())
        return {
            "assignment": [],
            "shelter_loads": [
                {"shelter": s.name, "capacity_people": s.capacity_people, "assigned": 0}
                for s in shelters
            ],
            "unmet_demand": total_demand,
            "total_capacity": total_capacity,
            "status": status,
            "message": (
                f"INFEASIBLE: total demand ({total_demand}) exceeds total shelter "
                f"capacity ({total_capacity}). Additional shelter capacity is "
                "required; this is an evacuation shortfall, not a model failure."
            ),
        }

    assignment = result.x.reshape(n_nodes, n_shelters)
    shelter_loads = [
        {
            "shelter": s.name,
            "capacity_people": s.capacity_people,
            "assigned": int(round(assignment[:, j].sum())),
        }
        for j, s in enumerate(shelters)
    ]
    return {
        "assignment": [
            {
                "node": nodes[i].name,
                "population": int(demand[i]),
                "assignments": [
                    {
                        "shelter": shelters[j].name,
                        "people": int(round(assignment[i, j])),
                        "distance_km": round(float(dist[i, j]), 2),
                    }
                    for j in range(n_shelters)
                    if assignment[i, j] > 0.5
                ],
            }
            for i in nodes_with_demand
        ],
        "shelter_loads": shelter_loads,
        "unmet_demand": 0,
        "total_person_km": round(float(result.fun), 1),
        "status": status,
        "message": "optimal assignment found (HI-GHS linear program)",
    }
