# CLAUDE.md — Cyclone Impact & Infrastructure Vulnerability Forecaster

Project context and integration plan for the team. Google Code for Communities
Hackathon 2nd Edition — **Track 5: Cyclone Impact & Infrastructure
Vulnerability Forecaster**.

## Problem statement (as given)

Build an AI-powered predictive risk/vulnerability platform using Google
Earth Engine (GEE) satellite feeds, real-time meteorological data, and
Gemini 3.7 Flash's multimodal reasoning. Simulate cyclone storm surge,
predict rainfall damage pathways, map exposure for critical infrastructure
(power grids, arterial roads, medical shelters), and automate early-warning
advisory dispatches for local authorities.

## Case study anchor

We're building around one real, documented event rather than a generic
simulator — it's more credible and gives a concrete demo narrative.

- **Cyclone Remal**, May 2024. Landfall between Sagar Island (West Bengal,
  India) and Khepupara (Bangladesh).
- Real landfall wind: **110–120 kmph gusting to 135**.
- Real forecast/observed surge: **~1.0–1.5 m** above astronomical tide.
  Anchor value used throughout: **1.2 m**.
- Demo narrative: "here's what Remal actually did — here's what this
  system would have flagged 48 hours out."

## Product framing

- **Core function** (one sentence): given a cyclone intensity, show exactly
  which hospitals/substations/roads go underwater, and generate a
  ready-to-send evacuation advisory.
- **Core loop**: adjust intensity → flood zone + exposed infra update on
  the map → tap "Generate Advisory" → Gemini returns evacuation priorities
  + SMS draft → loop with a different intensity.
- **Retention hook (vision only, not built this round)**: push notification
  when a live IMD bulletin signals a new cyclone watch for a saved district
  — would reuse an NLP bulletin-parser on real IMD/RSMC bulletin text.

## Architecture — data flow

```
Data sources (track, OSM infra, DEM, historical cyclone dataset)
        |
Surge model — anchored quadratic scaling: 1.2 x (wind/115)^2,
              scaled from one observed event (NOT a trained regression;
              see "Corrections / gotchas" below)
        |
Simulation engine:
  - Flood propagation (BFS cellular automaton, time-stepped)
  - Evacuation routing (graph search over live road network)
  - Shelter allocation (linear programming / transportation problem)
        |
Gemini AI — synthesizes the district advisory from all computed outputs
        |
Mobile app (Expo / React Native) — displays everything
```

Deployment: **Render** hosts the FastAPI backend (free tier — pre-warm
before live demos, cold start is 30–50s after 15 min idle).

## Tech stack — what each piece is for

| Layer | Tool | Used for |
|---|---|---|
| Data | IBTrACS | Real historical track (lat/lon/wind/time) for Remal |
| Data | OpenStreetMap (Overpass API) | Hospitals, substations, roads for the affected districts |
| Data | Google Earth Engine | SRTM 30m elevation (DEM) — terrain for the flood model |
| Surge | plain arithmetic | `surge_m = 1.2 x (wind_kmph/115)^2`, anchored on Remal's documented 1.2 m at 115 km/h. No ML ships — see "Corrections / gotchas" |
| Simulation | NumPy / a BFS queue | Time-stepped flood propagation (cellular automaton) |
| Simulation | osmnx + networkx | Road network graph; Dijkstra shortest safe path avoiding flooded edges |
| Simulation | scipy.optimize.linprog | Shelter allocation as a capacitated transportation problem |
| Backend | FastAPI + Uvicorn | API server — every layer talks through this |
| Backend | rasterio, geopandas, shapely | DEM reading, polygon geometry, spatial intersections |
| AI | google-genai SDK + gemini-3.8-flash | Synthesizes the district advisory from all computed outputs |
| AI | pydantic (`response_schema`) | Forces Gemini's output into a fixed, parseable schema |
| Mobile | Expo (managed, TypeScript) | App shell, no native Xcode/Android Studio setup needed |
| Mobile | react-native-maps | MapView, Polygon (flood), Polyline (track/routes), Marker (infra) — confirmed to work directly in Expo Go |
| Mobile | @react-native-community/slider | Intensity control |
| Mobile | expo-clipboard | Copies the SMS advisory draft |
| Deploy | Render | Hosts the FastAPI backend |

## Data sources — how to pull them

**IBTrACS track (Cyclone Remal)**
```
https://www.ncei.noaa.gov/data/international-best-track-archive-for-climate-stewardship/v04r00/access/csv/ibtracs.NI.list.v04r00.csv
```
Filter `NAME == "REMAL"`, `SEASON == "2024"`. Skip row index 1 (units row).

**OSM infrastructure (Overpass API)** — bbox covers South/North 24 Parganas
and Sagar Island: `(21.30, 87.80, 22.60, 89.20)`. Pull `amenity~hospital|clinic`,
`power~substation|plant`, `highway~motorway|trunk|primary|secondary`.
Pre-fetch to local GeoJSON — don't call Overpass live from the backend
(public instance rate-limits at 2 concurrent requests/IP).

**SRTM DEM (Google Earth Engine)** — `ee.Image("USGS/SRTMGL1_003")` clipped
to the same bbox, 30m scale, exported via `getDownloadURL`.

## Modules — assign to teammates

### A. Data pipeline & surge model
- Pull IBTrACS, OSM, DEM per above.
- **Surge model: DONE, and it is anchored scaling, not a trained regression.**
  `surge_m = 1.2 x (wind_kmph/115)^2` in `backend/simulation/surge.py`.
- A regression WAS attempted: `backend/experiments/surge_regression/`. It was
  trained on 4 points — 3 of which have no traceable source — did not
  generalise under leave-one-out CV, and needed 2 of its 3 features
  (forward speed, approach angle) which the API never has at request time.
  **Abandoned 2026-09-28; kept only as a record of what was tried.** Do not
  revive it without a real training table.

### B. Simulation engine
- Flood propagation: BFS cellular automaton over the DEM grid, producing a
  sequence of flood frames (timeline), not a single static polygon.
- Evacuation routing: build the road graph with `osmnx`, remove/penalize
  edges intersecting the current flood frame, run Dijkstra to nearest
  shelter.
- Shelter allocation: `scipy.optimize.linprog` transportation-problem
  formulation — minimize total evacuation distance subject to shelter
  capacity and full population coverage.

### C. Backend / API (FastAPI)
- Wire modules A and B behind endpoints (`/surge-zone`, `/exposure`,
  `/routes`, `/allocation`, `/advisory`).
- `/advisory` calls Gemini with the combined structured output from B.
- Deploy to Render.

### D. AI advisory layer (Gemini)
- `google-genai` SDK, model `gemini-3.8-flash`.
- Pydantic schema `DistrictAdvisory`: `executive_summary`,
  `evacuation_plan` (block-level priorities), `sms_dispatch_draft`
  (<160 chars), `post_landfall_risks` (freshwater/salinization narrative —
  no separate data pipeline needed, this is generated from Gemini's own
  knowledge), `historical_context` (one-sentence comparison to a past
  cyclone).

### E. Mobile app (Expo / React Native)
- Two screens total: map screen + advisory modal. No auth, no location
  permission (hardcode initial region to Sagar Island), no persisted
  history.
- `react-native-maps` for the map layers; slider drives intensity; routes
  from module B drawn as `Polyline`; advisory shown in a `Modal`.

### F. Deployment & dev tooling
- Backend → Render (free tier).
- Dev workflow: Gemini CLI + MCP tools (DesktopCommanderMCP,
  codebase-memory-mcp, Understand-Anything, GAAI-framework) for
  vibe-coding, and Microsoft's official `@playwright/mcp` for screenshotting
  the local app to catch UI bugs.

## Corrections / gotchas found while planning — don't relitigate these

- **Railway is not free** as of 2026 (requires a $5/mo Hobby plan) — use
  **Render** or Hugging Face Spaces instead.
- **`invisible_playwright_mcp`** is a real repo but it's a stealth/anti-bot-
  detection browser tool (built to defeat CAPTCHAs and fingerprinting), not
  a generic screenshot tool — use the official **`@playwright/mcp`** for UI
  inspection instead.
- Cyclone Yaas evacuation figure: the correct widely-reported number is
  **~1.1 million** people (not 2 million) — use this if citing it.
- **The surge model is anchored scaling, NOT a trained regression.**
  It is `surge_m = 1.2 x (wind_kmph/115)^2` in
  `backend/simulation/surge.py`, scaled from Cyclone Remal's documented
  ~1.0-1.5 m at 110-120 km/h. A regression *was* trained and is preserved at
  `backend/experiments/surge_regression/`; it used 4 rows (3 with no traceable
  source), did not generalise under leave-one-out CV, and needed forward speed
  and approach angle — neither of which the API has at request time. It was
  replaced 2026-09-28 and is an **abandoned experiment**, not a component.
  `CLAUDE.md`, `README.md` and `Rules.md` all described it as a "trained
  regression" until 2026-10-01; all three are corrected. **Every surge figure
  ships with the disclosure string "Screening estimate scaled from one observed
  event; omits tide, pressure, bathymetry and storm size" — do not weaken it.**
  If a real training table ever appears, reviving the regression is legitimate;
  describing the current model as one is not.
- `gemini-3.8-flash` is a real, current model — confirmed, not hallucinated.
  It replaced `gemini-3.7-flash` on 2026-09-28, when a live test found 3.7
  returning `503 UNAVAILABLE` (capacity) on five consecutive attempts over
  ~4 minutes while 3.8 answered the same prompt in 3s. `models.list()`
  confirmed 3.7 was still valid and available to the key, so this was a
  capacity block rather than a wrong model name. See MEMORY.md "Flagged for
  review" #19.
- `react-native-maps` works directly inside **Expo Go** — no custom dev
  client or EAS build needed for development/demo.
- If reusing code drafted by another AI session, check for stray
  `[cite: N]` fragments left inside code blocks — these break Python/JS
  syntax and must be stripped before running.

## Open research items

- More historical surge data points (wind speed, forward speed, approach
  angle → observed surge) from RSMC New Delhi bulletin archive. Until there
  are enough with traceable sources to fit something, the surge model stays
  anchored scaling — this is the prerequisite for revisiting the regression
  in `backend/experiments/surge_regression/`, not a description of it.
- Verified shelter locations (OSM `amenity=shelter` coverage in this area
  may be sparse — may need a manually curated list of Multi-Purpose
  Cyclone Shelters for the target blocks).
- Approximate at-risk population per block (for the shelter allocation LP)
  — via building density from OSM or a population raster (e.g. WorldPop)
  clipped to the same bbox.
- Confirm `osmnx` graph extraction performance/quality for the target bbox
  before committing routing logic to it.

## Reference code

**DistrictAdvisory schema**
```python
class EvacuationPriority(BaseModel):
    block_name: str
    priority_level: str  # CRITICAL, HIGH, MEDIUM, LOW
    reasoning: str

class DistrictAdvisory(BaseModel):
    executive_summary: str
    evacuation_plan: list[EvacuationPriority]
    sms_dispatch_draft: str = Field(description="Under 160 chars")
    post_landfall_risks: str = Field(
        description="Freshwater/salinization/livelihood risk narrative"
    )
    historical_context: str = Field(
        description="One-sentence comparison to a past Bay of Bengal cyclone"
    )
```

**Surge model — what actually ships**
```python
# backend/simulation/surge.py
ANCHOR_SURGE_M = 1.2      # midpoint of the documented ~1.0-1.5 m range
ANCHOR_WIND_KMPH = 115    # midpoint of the documented 110-120 km/h range

def surge_for_wind(wind_kmph: float) -> float:
    return ANCHOR_SURGE_M * (wind_kmph / ANCHOR_WIND_KMPH) ** 2
```

**The abandoned regression, kept for the record — not a component.**
```python
# backend/experiments/surge_regression/ — DO NOT ship this.
from sklearn.linear_model import LinearRegression
from sklearn.model_selection import LeaveOneOut
import numpy as np

# 4 rows. 3 of the 4 surge values have no traceable source.
X = np.array([[115, 16, 1], [105, 13, 0], [95, 20, 1], [70, 14, 0]])
y = np.array([1.2, 1.6, 2.9, 0.6])

# LOOCV over 4 points did not generalise, and 2 of the 3 features
# (forward speed, approach angle) are not available at request time.
```

**Flood propagation (BFS cellular automaton)**
```python
from collections import deque
import numpy as np

def simulate_flood_propagation(elevation, ocean_mask, target_surge, n_steps=10):
    frontier = deque(zip(*np.where(ocean_mask)))
    visited = ocean_mask.copy()
    frames = []
    for step in range(1, n_steps + 1):
        water_level = target_surge * (step / n_steps)
        next_frontier = deque()
        while frontier:
            y, x = frontier.popleft()
            for dy, dx in [(-1,0),(1,0),(0,-1),(0,1)]:
                ny, nx = y+dy, x+dx
                if (0 <= ny < elevation.shape[0] and 0 <= nx < elevation.shape[1]
                        and not visited[ny, nx]
                        and elevation[ny, nx] <= water_level):
                    visited[ny, nx] = True
                    next_frontier.append((ny, nx))
        frontier = next_frontier
        frames.append(visited.copy())
    return frames
```

**Evacuation routing**
```python
import osmnx as ox
import networkx as nx

G = ox.graph_from_bbox(22.60, 21.30, 89.20, 87.80, network_type="drive")

def safe_route(G, flood_polygon, origin_node, shelter_nodes):
    G_safe = G.copy()
    for u, v, k, data in list(G.edges(keys=True, data=True)):
        geom = data.get("geometry")
        if geom is not None and geom.intersects(flood_polygon):
            G_safe.remove_edge(u, v, key=k)
    best = min(
        shelter_nodes,
        key=lambda s: nx.shortest_path_length(G_safe, origin_node, s, weight="length")
        if nx.has_path(G_safe, origin_node, s) else float("inf"),
    )
    return nx.shortest_path(G_safe, origin_node, best, weight="length"), best
```

**Shelter allocation (LP)**
```python
from scipy.optimize import linprog
import numpy as np

def allocate_shelters(pop_per_block, shelter_capacity, distance_matrix):
    n_blocks, n_shelters = distance_matrix.shape
    c = distance_matrix.flatten()
    A_eq, b_eq = [], []
    for i in range(n_blocks):
        row = np.zeros(n_blocks * n_shelters)
        row[i*n_shelters:(i+1)*n_shelters] = 1
        A_eq.append(row); b_eq.append(pop_per_block[i])
    A_ub, b_ub = [], []
    for j in range(n_shelters):
        row = np.zeros(n_blocks * n_shelters)
        row[j::n_shelters] = 1
        A_ub.append(row); b_ub.append(shelter_capacity[j])
    res = linprog(c, A_eq=A_eq, b_eq=b_eq, A_ub=A_ub, b_ub=b_ub, bounds=(0, None))
    return res.x.reshape(n_blocks, n_shelters)
```

## Agent skills

### Issue tracker

Local markdown: issues and specs live as files under `.scratch/`. See
`docs/agents/issue-tracker.md`.

### Domain docs

Single-context: `CONTEXT.md` and `docs/adr/` at the repo root. See
`docs/agents/domain.md`.
