# MEMORY.md — Living State (read this every session, update it every session)

This file is the handoff baton between tools and sessions. AGENTS.md /
CLAUDE.md / GEMINI.md (same content, different filenames so different
tools auto-load it) describe the architecture and decisions — those don't
change often. This file describes *where things actually stand right now*
— it changes every session, updated by whichever tool worked last.

---

## 🔁 Bootstrap prompt — paste this first in any new tool or session

```
You're joining an in-progress hackathon project (Cyclone Impact &
Infrastructure Vulnerability Forecaster, Track 5, Google Code for
Communities Hackathon 2nd Edition). Before doing anything, read these
files in the repo root, in this order:

1. PRD.md — what we're building and why: problem, users, core loop,
   must-have vs. delighter features, and what's explicitly out of scope.
2. Architecture.md — how it's built: data flow, repo structure, and the
   API contract (endpoints, requests, responses).
3. Rules.md — guardrails: non-negotiable decisions, hard engineering
   rules (no live Overpass calls, gate Gemini behind a button, cite every
   historical number), and session discipline.
4. AGENTS.md (or CLAUDE.md / GEMINI.md — identical content) — the fuller
   reference doc, including the actual code for the surge model, flood
   propagation, routing, and shelter allocation.
5. Task.md — the full checklist backlog, module by module.
6. This file, MEMORY.md, in full — the current state: what's actually
   built right now, what's broken, what's next. Trust this over any
   assumption about progress you might otherwise make.

Treat decisions in PRD.md/Architecture.md/Rules.md/AGENTS.md as settled —
don't propose alternatives to things they document as already decided.
Work only on the item under "Next step" below, unless told otherwise.

Before ending this session — or as soon as you sense you're running low
on context window or usage quota — update this file yourself:
   - Update "Status by module"
   - Update "What actually exists in the repo right now"
   - Check off completed items in Task.md
   - Append one new entry to "Session log" (date, tool used, what you
     did, what broke, what's unresolved)
   - Rewrite "Next step" so the next session — possibly a completely
     different AI tool — knows exactly where to pick up, with no
     re-explaining needed

If you think a decision in Rules.md/AGENTS.md is wrong, don't silently
change direction — add it under "Flagged for review" below and keep
working within the existing decision until a human overrules it.

Confirm you've read all of the above and repeat back the current "Next
step" before starting any work.
```

---

## Status by module

| Module | Status | Notes |
|---|---|---|
| A. Data pipeline & surge ML model | Not started | |
| B. Simulation engine (flood propagation, routing, shelter allocation) | Not started | |
| C. Backend / API (FastAPI) | Not started | |
| D. AI advisory layer (Gemini) | Not started | |
| E. Mobile app (Expo / React Native) | Not started | |
| F. Deployment | Not started | |

*(Status values: Not started / In progress / Blocked / Done)*

## What actually exists in the repo right now

*(List real files/paths as they get created. Keep this in sync with reality
— this is what stops the next session from re-deriving something that
already exists, or trusting a file that was later deleted.)*

- Nothing yet.

## Known issues / blockers

*(None yet.)*

## Flagged for review

*(Nothing flagged. A tool session adds a line here — not a silent change —
if it disagrees with something in AGENTS.md/CLAUDE.md.)*

## Environment / credentials status

- [ ] Google Earth Engine authenticated (`ee.Authenticate()` run once)
- [ ] `GEMINI_API_KEY` set in environment
- [ ] Backend deployed (Render / Railway) — URL: _none yet_
- [ ] Expo project initialized

## Next step

Start Module A: pull the IBTrACS track for Cyclone Remal, the OSM
infrastructure layers (hospitals/substations/roads), and the SRTM DEM,
per the "Data sources — how to pull them" section in AGENTS.md/CLAUDE.md.
Commit the resulting GeoJSON/TIFF files to the repo rather than re-fetching
them live in later sessions.

---

## Session log (newest entry first)

### _(fill in date)_ — _(fill in which tool: Z.Code / Antigravity / Devin / Claude Code / OpenCode / Gemini)_
- No sessions logged yet — this is the first one. Update this entry, then
  move it below a fresh one next time.
