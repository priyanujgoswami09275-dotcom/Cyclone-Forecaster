# Plan — Integrate Design.md into the mobile app

**Date:** 2026-09-27
**Status:** Complete
**Scope:** Land `Design.md`'s visual system into `/mobile` (Module E).

## Situation found at start

`/mobile` did not exist. MEMORY.md lists Module E "Not started"; every
Module E item in Task.md is unchecked; "Expo project initialized" is
unchecked. So there were **no components to re-style** — the task's
"apply the theme to every component that already exists" had an empty
premise.

Per the task's own rule for missing components ("don't build its logic
here, just style whatever placeholder/stub exists"), this session does
NOT build Module E's screen logic, data wiring, or API calls. It lands
the design system itself: tokens, fonts, and the presentational
primitives that `Design.md`'s "Component specs for the actual screens"
enumerates.

## Steps

1. `/mobile/theme.ts` — the `theme` object copied verbatim from
   `Design .md`. No value modified, no value added.
2. Minimal Expo scaffold (`package.json`, `app.json`, `tsconfig.json`,
   `babel.config.js`) — required infrastructure for the font install.
3. Install `@expo-google-fonts/inter`, `@expo-google-fonts/roboto-slab`,
   `expo-font` per `Design.md`'s "Font setup (Expo)".
4. `useFonts` gate at the app root (`App.tsx`) with a loading state —
   nothing renders until fonts resolve.
5. Presentational, prop-driven primitives matching `Design.md`'s
   component specs: `PriorityChip`, `ExposureRow`, `PrimaryButton`,
   `GhostButton`, `AdvisoryModal`, plus map layer style constants.
   Zero data fetching, zero API, zero map logic.
6. Typecheck with `tsc --noEmit`.
7. Update MEMORY.md: what's themed, what's pending, flagged items.

## Deliberately not done (Module E / B / C scope)

- Map screen, `MapView`, `react-native-maps` wiring, static data render
- Slider → `/api/simulate` wiring, flood `Polygon` render, live exposure
- Remal track `Polyline`, routing, shelter allocation

## Flagged, not invented

- `theme.fonts` carries family names only. `Design.md`'s typography
  table gives **sizes** (20–24 / 15–16 / 12–13) that appear nowhere in
  the theme object, so no component could be given a font size without
  inventing one. Logged in MEMORY.md "Flagged for review".
- "White text" in the component specs is not a token. Mapped to
  `theme.colors.card` (`#ffffff`) — same hex, recorded, not invented.
- Source file is named `Design .md` — a stray space before the
  extension. Left in place; renaming needs a human.

## Outcome

See MEMORY.md "Session log" for 2026-09-27.
