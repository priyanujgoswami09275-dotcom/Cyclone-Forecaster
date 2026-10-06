# Flood reach of the reference BFS vs an unrestricted fill

**NOT ON THE RUNTIME PATH.** This is an experiment that measures what the
reference algorithm's step-limited flood spread costs, and it is kept out of
the Vercel bundle (`backend/experiments/**` is in `excludeFiles`).

`simulate_flood_propagation` expands the flood by one DEM cell per step for
`n_steps=10` steps. At the committed DEM's roughly 50 m resolution that is a
reach of at most about 600 m inland from any ocean-connected cell, whatever the
surge. The command below prints the side-by-side with an unrestricted fill
(every cell with `elevation <= surge` in a connected component that touches
ocean), with ocean and nodata cells excluded from both areas — so only the
reach differs.

## Command

```bash
venv/bin/python backend/experiments/flood_reach/compare.py
```

## Result

| scenario        | wind_kmph | surge_m | run_flood_model_km2 | bathtub_km2 | bath/model |
|-----------------|-----------|---------|---------------------|-------------|------------|
| Remal observed  | 111.1     | 1.12    | 327.6               | 440.7       | 1.35       |
| cat4            | 142.0     | 1.83    | 359.2               | 440.7       | 1.23       |
| cat5            | 194.0     | 3.41    | 1709.6              | 2574.8      | 1.51       |
| cat6            | 222.0     | 4.47    | 2680.2              | 4947.7      | 1.85       |

Notes:

- Each side excludes permanent ocean and nodata cells; the two columns are the
  same quantity (inundated land). The only difference is how far the model is
  allowed to advance inland.
- Ratios grow with surge because higher water reaches terrain that a short
  reach never had a chance to enter — the cost of the limit is largest exactly
  when the scenario is largest.
- `cat4` bathtub equals `Remal observed` bathtub: the extra cells in the 1.12–
  1.83 m window are not ocean-connected (or are nodata), not because they cost
  the same by design.
- These are precisely the model's own building blocks; no figures were hand
  re-counted. If a number drifts, the computation that produced it is the fix,
  not this table.
