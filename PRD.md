# PRD.md — Cyclone Impact & Infrastructure Vulnerability Forecaster

Google Code for Communities Hackathon 2nd Edition — **Track 5**.

## Problem statement (as given)

Build an AI-powered predictive risk/vulnerability platform using Google
Earth Engine (GEE) satellite feeds, real-time meteorological data, and
Gemini's multimodal reasoning. Simulate cyclone storm surge, predict
rainfall damage pathways, map exposure for critical infrastructure (power
grids, arterial roads, medical shelters), and automate early-warning
advisory dispatches for local authorities.

## The problem we're actually solving

Today's cyclone warnings are broad and regional — wind speed, expected
rainfall, a general "stay indoors." What they don't say is *which* hospital
loses power, *which* road becomes the one that strands a village, or
*where* the nearest shelter with room actually is. Existing early warning
systems (regional forecasts, mobile alerts, volunteer networks) enable mass
evacuation but lack hyper-local, asset-specific exposure modeling — so
emergency managers know a storm is coming without knowing exactly what to
do about it, block by block. This app closes that specific gap.

## Users

- **Primary**: district-level emergency management / disaster management
  authorities deciding where to send evacuation and relief resources.
- **Secondary**: hackathon judges evaluating technical depth and real-world
  applicability.

## Case study anchor

- **Cyclone Remal**, May 2024. Landfall between Sagar Island (West Bengal,
  India) and Khepupara (Bangladesh).
- Real landfall wind: 110–120 kmph gusting to 135.
- Real forecast/observed surge: ~1.0–1.5m above astronomical tide. Anchor
  value used throughout: **1.2m**.
- Demo narrative: "here's what Remal actually did — here's what this
  system would have flagged 48 hours out."

## Core function

Given a cyclone intensity, show exactly which hospitals, substations, and
roads go underwater, and generate a ready-to-send evacuation advisory.

## Core loop

1. Adjust the strength chips (Depression → Super Cyclonic Storm)
2. Flood zone + exposed infrastructure update on the map
3. Tap "Generate Advisory" → AI returns evacuation priorities + SMS draft
4. Repeat with a different intensity to stress-test another scenario

This loop should close in under ~15 seconds — that's the demo's heartbeat.

## Must-have features (MVP)

| Feature | What it does |
|---|---|
| Intensity simulator | Slider scales surge/flood extent across IMD wind categories |
| Infrastructure exposure intersector | Counts and names exactly which hospitals/substations/roads the flood reaches |
| AI advisory generator | Gemini synthesizes a district advisory + SMS draft from computed exposure |
| Cyclone track viewer | Real Remal track with timestamped waypoints on the map |

## Should-have features (delighters, build after must-haves)

| Feature | What it does | Cost |
|---|---|---|
| Compromised-corridor styling | Flooded roads shown as red dashed lines | Free — reuses exposure data already computed |
| SMS/WhatsApp copy export | One-tap copy of the generated SMS draft | Free — field already exists in the advisory schema |
| Post-landfall risk narrative | Advisory mentions freshwater/salinization/livelihood risk | Free — Gemini's own knowledge, no new data pipeline |
| Historical context sentence | One-line comparison to a past Bay of Bengal cyclone | Free — Gemini's own knowledge, no new data pipeline |
| Evacuation routing | Real computed safe route around flooded roads | Real engineering — graph search over OSM road network |
| Shelter allocation | Which population goes to which shelter, capacity-aware | Real engineering — linear programming |

## Explicitly out of scope

- User accounts / authentication
- Live GPS / location permission — hardcode initial map region
- Persisted history across app restarts
- Push notifications (this is the retention hook — vision only, see below)

### No longer out of scope, and now implemented

Two items previously listed as out of scope have since been built, so they are
**implemented, not out of scope**:

- **Multi-cyclone baseline comparison** — the "Dynamic Cyclone System" adds a
  610-storm historical IBTrACS catalogue, a `CyclonePicker`, and scenario
  comparison. The one-sentence AI comparison for a *different* past cyclone is
  still available inside the advisory's `historical_context`.
- **Real-time live meteorological data feeds** — there is now a real ATCF live
  provider (`/live-cyclone`) that probes configurable endpoints and reports an
  honest `live_unavailable` state rather than substituting a historical storm.
  Its availability for the North Indian Ocean basin is still unproven in
  production — see "Known limitations" in README.md.

## Retention hook (vision, not part of this build)

In a real production version, a live IMD-bulletin parser would push a
notification the moment a new cyclone watch is issued for a saved
district — turning this from a one-time simulator into a standing
early-warning companion. Worth stating explicitly in the pitch; not
something to build in this round.

## Success criteria for the demo

- The core loop (chips → exposure → advisory) completes in under 15
  seconds with no crashes.
- Every historical number cited (wind speed, surge height, past-cyclone
  comparisons) traces to a real, named source — nothing fabricated.
- The app runs live via Expo Go for the full demo window, with a backup
  screen-capture video ready in case of WiFi failure.
- Judges can be shown a clear "what really happened vs. what this would
  have flagged" comparison for Remal.

## Status today (2026-10-02)

**Implemented** — the case-study simulator (surge law, BFS flood, routing,
shelter allocation, Gemini advisory), the dynamic-cyclone catalogue and
scenario addressing, a real ATCF live provider, and a storm-peak-intensity ML
layer whose gate failed (so the baseline ships).

**Prototype / not on real data** — shelter capacities are demo placeholders;
the surge and ML figures are estimates, not forecasts; the live ATCF feed has
never returned a usable North Indian Ocean storm.

**Prototype limitations** — the native app has never run on a physical phone,
and the deployed backend predates the dynamic-cyclone backend, so it 404s on
`/cyclones` and `/live-cyclone` until redeployed.

**Unavailable external dependency** — a public North Indian Ocean ATCF source;
until one exists, live mode answers `live_unavailable`.
