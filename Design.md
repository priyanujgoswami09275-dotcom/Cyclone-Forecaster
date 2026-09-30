# Design.md — Visual System

Adapted from the Airtable-style reference (chosen for implementation ease)
into an actual Expo/React Native system for this app's real screens: map +
slider + exposure list + advisory modal. Not a marketing page — no hero,
no logo bar, no announcement banner.

## Why these choices

- **Superseded 2026-09-30.** The rationale below describes the original light
  palette and is kept because several of the decisions still hold; the *colors*
  it justifies were replaced by the dark theme sampled from the human's design.
  See "Dark theme (2026-09-30)" for the current palette.
- Terracotta/Burnt Sienna already read as danger — used directly for
  compromised roads and CRITICAL priority, no color invented.
- Sapphire/Pale Sky already read as water — used directly for the flood
  polygon fill and stroke, a natural map convention.
- Forest Ink reads as safe — used for shelters.
- Marigold — MEDIUM priority / caution.
- The source had no defined primary action color (Airtable's own CTA is
  plain black) — **Cobalt Blue is chosen here** as the one deliberate
  accent for the app's single main action ("Generate Advisory"), keeping
  it distinct from the danger/water/safe system.
- Fonts: **Inter only.** The original spec paired Inter (body/UI) with
  Roboto Slab (headings). The dark design calls for one family, and this
  app's headings are a weight distinction rather than a typeface one, so
  the slab face was removed from `theme.fonts` and from the font gate in
  `App.tsx` on 2026-09-30. Roboto Slab is no longer downloaded at boot.
- Shadows simplified to React Native's single-shadow model — the source's
  multi-layer box-shadow doesn't exist in RN; one soft shadow value is
  used everywhere a card needs lift. It is now a **black** shadow at 0.5
  opacity, because the light theme's blue-tinted shadow is invisible on a
  near-black surface.

## Dark theme (2026-09-30)

Sampled from the human's supplied design; these values **replace** the table
below rather than sitting alongside it. `mobile/theme.ts` is the implementation.

| Token | Hex | Use in this app |
|---|---|---|
| `background` | `#090909` | Page background — the app is dark, edge to edge |
| `card` | `#0c0c0b` | Panels, cards, one step above the page |
| `text` | `#eeedea` | Primary text — a warm off-white, **not** pure `#fff` |
| `textMuted` | `#bcbbaf` | Secondary text, captions |
| `border` | `#3d3d3c` | Hairlines and card outlines; visible on `#090909` |
| `primary` | `#0d52c3` | The single accent — Generate advisory, step badges |
| `selectedFill` | `#011132` | Selected chip fill — darker than `background`, not a tint of it |
| `selectedText` | `#5f9dea` | Text of a selected chip |
| `land` | `#e6ece0` | Basemap land tint in `customMapStyle` |
| `water` | `#c9e0ec` | Basemap water tint in `customMapStyle` |
| `flood` | `#84a7d3` | The flood raster's own tint |
| `danger` | `#a11d00` | Submerged hospitals, cut-off roads, CRITICAL |
| `caution` | `#fcb42a` | Substations, MEDIUM. **Unchanged** from the light theme |
| `white` | `#ffffff` | The map's floating buttons and the legend pill, **only** |
| `onWhite` | `#181d26` | Text/icon colour on that white chrome |

**The one light surface in a dark app, and why.** The map's floating buttons
and the legend pill are opaque white, with `onWhite` text. They sit *on top of*
a pale basemap (`land` is `#e6ece0`), and a dark chip on a pale landmass is a
control nobody can see. The map's chrome is light while the app around it is
dark; that inversion is deliberate and is why `text` cannot be used on those two
surfaces.

`land` and `water` are **map tints, not UI surfaces.** They colour the basemap
and nothing else should reference them.

### Replaced (light theme, 2026-09-29)

Kept for history; do not restore without a reason. `background #faf5e8`,
`card #ffffff`, `text #181d26` (now `onWhite`), `textMuted #525965`,
`border #e0e2e6`, `primary #1b61c9`, `water #254fad`, `waterFill #c7e5f2`,
`danger #aa2d00`, `dangerDark #912e1f`, `safe #0a2e0e`, and the
`RobotoSlab_700Bold` heading face.

`dangerDark` and `safe` were **not** carried into the dark theme. Neither was
used by anything the rebuild touches, and leaving unused tokens in the palette
is how a token file becomes a list of things that used to be true. Both are in
git history.

## Color tokens (SUPERSEDED — see Dark theme above)

## Color tokens

| Token | Hex | Use in this app |
|---|---|---|
| `background` | `#faf5e8` | App background (Parchment Cream) |
| `card` | `#ffffff` | Advisory modal, exposure list cards |
| `text` | `#181d26` | Primary text (Onyx) |
| `textMuted` | `#525965` | Secondary text, captions |
| `border` | `#e0e2e6` | Card borders, dividers |
| `primary` | `#1b61c9` | Cobalt Blue — "Generate Advisory" button, active slider thumb |
| `water` | `#254fad` | Sapphire — flood polygon stroke |
| `waterFill` | `#c7e5f2` | Pale Sky — flood polygon fill, use at ~50% opacity |
| `danger` | `#aa2d00` | Terracotta — compromised roads, CRITICAL priority chip |
| `dangerDark` | `#912e1f` | Burnt Sienna — pressed/active danger state |
| `safe` | `#0a2e0e` | Forest Ink — shelter markers, LOW priority |
| `caution` | `#fcb42a` | Marigold — MEDIUM priority chip |

## Typography

**Inter only, as of 2026-09-30.** One family, four roles, separated by weight.

| Role | Font | Weight | Size |
|---|---|---|---|
| Headings | Inter | 600 | 20–24px — **22px** used |
| Body | Inter | 400 | 15–16px — **16px** used |
| Emphasis / button labels | Inter | 600 | 15–16px — **16px** used |
| Caption / labels | Inter | 500 | 12–13px — **13px** used |

`theme.fonts.heading` now points at `Inter_600SemiBold` rather than
`RobotoSlab_700Bold`. Nothing on the map screen needs a slab: the screen's
headings are the 16px screen title and the 13px uppercase step labels, and both
read as headings because of weight and case, not typeface.

The ranges above are the design intent; the bold values are the concrete sizes
implemented in `mobile/theme.ts` (`theme.typography`), chosen as the midpoint
of each range. Change one and change both.

## Spacing & radius

8px base unit, kept from the source.

| Element | Radius |
|---|---|
| Buttons | 12px |
| Cards | 16px |
| Priority chips | 32px (pill) |

## Component specs for the actual screens

### Rebuilt to the dark design, 2026-09-30 (Stage A)

The map screen was rebuilt in two stages. Stage A is below; Stage B (display
smoothing, advisory modal restyle) is not done. What follows replaces the
superseded light-theme specs, which are kept in git history.

- **Screen structure, top to bottom**: title + subtitle, the map (flexing, with
  a 40% minimum height), then the panel. The panel sizes to its content and the
  map takes the rest, so the map *grows* when the panel shrinks — on a short
  device or a large system font — rather than the panel eating a fixed
  percentage. The 40% floor is what stops the map being squeezed to a strip.
- **Title** "Cyclone Remal impact simulator" at `typography.body` in
  `fonts.heading`; **subtitle** "What would a storm like this hit today?" at
  `typography.caption` in `textMuted`.
- **Step badge** (`PanelStep`): a `radius.chip` circle filled `selectedFill`
  with the number in `primary`. The badge is *darker* than the page, so it
  reads as a recessed well and the number is the only `primary` on the panel.
  Three badges give the panel a spine and make the three steps countable.
  Deliberately **not** the first-run card's `primary` fill: that card is a
  transient overlay on a pale map, while these sit on near-black where a bright
  blue disc would out-shout the Generate button they lead to.
- **Step titles** "Pick a storm strength", "See what gets hit", "Get the
  evacuation plan" at `typography.body` in `fonts.bodySemibold`.
- **Strength chips** (`StrengthChips`): a horizontal scrolling row, because
  "Super cyclonic" makes four chips wider than a phone and a wrap would change
  the row's height between selections, shoving the figures line below it. Idle
  chip is `card` fill with a `border` outline; selected is `selectedFill` fill
  with a `primary` border and `selectedText` label in `bodySemibold`, idle
  label `textMuted` in `bodyMedium`.
- **Figures line** under the chips: `typography.caption`, `bodySemibold`,
  `text`. Prefixed `≥` only when the backend's `wind_is_band_midpoint` is
  false, and suffixed "(model)" because the km² is the computation's figure and
  not measured off the picture above it.
- **Exposure tiles** (`ExposureTiles`): three equal `card` blocks, outlined in
  `border`, value at `typography.heading` in `text`, label at `caption` in
  `text`, unit line at `caption` in `textMuted`. An unresolved count renders
  `—`, never `0`; a real zero renders `0`. The unit line is suppressed at zero.
  Road's caption is "cut off by water", not "submerged" — a road is severed by
  water it need not be standing in.
- **Generate advisory**: full-width `primary` fill, 12px radius, at
  `typography.emphasis` in `fonts.bodySemibold`, label in `background` (the
  near-black that is the only legible colour on `primary` at this weight).
  Inside the panel as step 3, **not** in a pinned footer — with three steps the
  panel no longer scrolls past the button, and a button detached from the thing
  it summarises reads worse than one directly under it.
- **Origin line**: "From: <locality> · change", `caption`, name in
  `bodySemibold`/`text`, and "change" in `selectedText` underlined — the same
  blue a selected chip uses, because it is an action in the chip vocabulary.
  Opens the existing `LocalityPicker`; no new component and no new dependency.
- **Map floating controls** (`MapControl`): three 48dp `radius.button` squares
  stacked at the map's right, `white` fill with `onWhite` icons, the card
  shadow. The storm-path button gains a 2dp `selectedText` ring while the map is
  fitted to the track, so the state is on the control and not only in the map.
  **These are drawn marks, not platform-native icons** — see the vector-icons
  note in MEMORY.md.
- **Legend**: `white` fill (it sits on the pale basemap, so a dark pill would
  be the one control nobody can see), `onWhite` text, 12px radius, bottom-left.
  The track's swatch is a *solid* bar although the layer is now dashed — see
  the next section.
- **About caption** "Screening estimate, not a forecast · About this estimate":
  `caption` in `textMuted`, with "About this estimate" in `bodySemibold`/
  `selectedText` and an `accessibilityRole` of `link`. Opens `AboutSheet`.
- **About sheet** (`AboutSheet`): a bottom-anchored `card` sheet over a 60%
  black scrim, `radius.card` top corners, capped at 80% height so it scrolls.
  Three blocks with uppercase `caption`/`textMuted` headings. Backdrop is
  tappable and `onRequestClose` is wired, or the sheet traps the user on
  Android.

### The basemap (`customMapStyle`)

New 2026-09-30. Google Maps' documented style array, applied in order with
later entries winning. **POIs and transit are off entirely** — a cyclone
exposure map with every restaurant and bus stop on it is unreadable, and those
markers are the ones most likely to be mistaken for the app's own asset pins.
Road geometry is `card` and road *labels* off, so the app's own cut-off roads
are the only road lines a reader has to interpret.

| Feature | Element | Value |
|---|---|---|
| (all) | `geometry` | `land` |
| (all) | `labels.text` | `textMuted` |
| (all) | `labels.icon` | off |
| `water` | `geometry` | `water` |
| `water` | `labels.text` | `textMuted` |
| `poi` | — | off |
| `transit` | — | off |
| `road` | `geometry` | `card` |
| `road` | `labels.text` | off |
| `road.highway` | `geometry` | `border` |
| `administrative` | `geometry.stroke` | `border` |
| `administrative.land_parcel` | — | off |

**Rotation and pitch stay disabled**, and that is a correctness decision rather
than a simplification: the flood is a single north-up raster and `<Overlay>`
takes a static `bearing` instead of tracking the camera, so a rotated map would
leave the water at the wrong angle to the coastline — a subtle wrongness much
worse than losing the ability to spin the map.

### Superseded light-theme component specs

Kept for history; the dark rebuild above replaces them.

- **Map screen background**: `background` token behind the map container
- **Intensity slider**: track in `border`, filled portion + thumb in `primary`
  — **replaced** by the four strength chips, which is the whole of Stage A's
  structural change. Five of the seven bands expose no infrastructure, so the
  slider spent four fifths of its travel on empty results.
- **Exposure list row** (hospital/substation/road): `card` background,
  small colored dot — `danger` if affected, `textMuted` if not
- **Priority chip** (CRITICAL/HIGH/MEDIUM/LOW): pill shape, background
  `danger`/`dangerDark`/`caution`/`safe` respectively, white text — **the two
  dark-theme tokens it needed did not survive the rebuild.** It now maps
  CRITICAL/HIGH→`danger`, MEDIUM→`caution`, LOW→`border`, and carries the
  rank difference in the *label's weight* (`bodySemibold` vs `bodyMedium`)
  rather than in a second red. Two reds differing only by opacity are
  indistinguishable on a phone in daylight. Label colour is per-level too:
  near-black on `danger` and `caution`, but `text` on `border`, where
  near-black would be a contrast failure. Four distinct fills would be two new
  tokens plus a Design.md row — the human's call, logged in MEMORY.md.
- **Flood polygon on map**: fill `waterFill` at 50% opacity, stroke `water` —
  **now `flood` fill, `water` stroke.** Note the committed PNGs are painted
  `#2563eb` (`render_overlays.RGB`), which is *not* the `flood` token; the
  raster is a pre-rendered artefact the theme does not control. Restyling it to
  `flood` is a Stage B question and would change every committed PNG.
- **Compromised road**: dashed `Polyline`, color `danger` — **unchanged.** The
  dash is `[6, 4]` and is not honoured by the Android renderer, so it draws
  solid there.
- **Cyclone track**: was `text` (Onyx), now **near-black `background` (#090909)
  and dashed `[10, 6]`**. The dash is the new part and it is not cosmetic: the
  track is the one layer that crosses the others — the length of the Bay, across
  the flood raster near landfall, over the delta where the cut-off roads
  cluster. Solid, it has to win every crossing. Dashed, the flood and the
  severed roads stay legible underneath, and "what happened" is distinguished
  from "what would happen" by texture as well as by colour.
- **Advisory modal**: `card` background, 16px radius, single soft shadow —
  **not yet restyled.** That is Stage B.
- **"Generate Advisory" button**: `primary` fill, white text, 12px radius —
  label is now "Generate advisory" (sentence case, matching the design) and
  sits inside the panel.
- **SMS copy button**: ghost style — white fill, 1px `border`, `text` color

### Added 2026-09-29 without a prior spec

These use existing tokens only — no colour, font or size was invented — but
none of them had a line here before they were built, so they are recorded now
rather than left as undocumented implementation. Unlike the track token above,
these are **implementation choices, not decisions**; MEMORY.md §37 lists them
as owed a human look.

**Still on screen after the Stage A rebuild:**

- **Map legend**: bottom-left of the map, `white` fill (it sits on the pale
  basemap), `onWhite` labels, `radius.button`. Five rows — flooded area,
  hospital, substation, cut-off road, storm path. **Swatch colours are named as
  strings in `legend.ts` and resolved in `MapLegend.tsx` against the same
  `mapStyles.ts` constants the map draws from**, so the legend cannot drift from
  the map. The name/value split exists because `node --test` cannot follow the
  extensionless imports Metro requires — see that file's header.
  **One acknowledged mismatch**: the flood row resolves to `theme.colors.flood`
  (`#84a7d3`) while the committed raster is painted `#2563eb`. The swatch is
  app chrome and the raster is a pre-rendered artefact the theme does not
  control; they are close in hue, so the key reads correctly against the layer.
  Logged in MEMORY.md.
- **First-open card**: top of the map, `card` fill, `radius.card`, 1px
  `border`. Three numbered steps in `primary` badges naming the three gestures
  that make up the app's loop. Dismissible. **Session-scoped, not persisted** —
  it returns on every cold start. See MEMORY.md.
- **Shelter disclosure notice**: `caution` fill, `text` body, `radius.button`.
  Placed above the advisory body rather than in a footer, so it is read before
  the numbers it qualifies. Non-dismissable by design.
- **"No flood-free route" notice**: `danger` fill, `card` text. Sits directly
  under the provenance line, above the summary, because it changes how every
  line below it should be read.
- **Stale-advisory notice**: `caution` fill, `text` body, shown when the
  advisory on screen was generated for a different intensity or origin than
  the current settings. Now inside the panel, directly above the button it
  warns about.
- **Failure states**: `danger` heading, `text`/`textMuted` body, violations in
  a `background` block. Only the `validation` state lists violations; every
  other failure shows prose alone.

**Removed by the Stage A rebuild:**

- **"Show storm path" ghost chip** → became one of three white floating buttons
  (`MapControl`).
- **"Intensity slider" + "What-if" caption** → replaced by the four strength
  chips and the figures line. The caption's "this is a what-if" point is now
  carried by the "Screening estimate, not a forecast" line.
- **Section headings** "Storm", "Impact", "Where are you" → the three numbered
  `PanelStep` titles.
- **Pinned "Generate Advisory" footer** → the button is back inside the panel
  as step 3, since three steps no longer overflow the panel.
- **`ReadoutPanel`** → its numbers are the three tiles, its raster note and
  limitation are in the About sheet, and its three exposure rows are the tiles.
  The component is no longer rendered; it remains in the tree, unused, pending
  the human's decision on whether to delete it.
- **The "Where are you" locality card** → the one-line `OriginLine`.

## The Web build, 2026-10-01

Added because the Web app is now a real screen rather than a compatibility
fallback, and because **it is not a variation on the spec above — it is a
different medium**. The native screen is a map with a panel under it on a
phone; the Web screen is a two-column briefing at desktop width that collapses
to one. Tokens are identical (`mobile/theme.ts` is the single source for both),
so nothing here re-specifies a colour, a font or a size.

### What is shared, and what is genuinely different

**Shared verbatim:** `theme.ts`, `strengthChips.ts`, `exposureTiles.ts`,
`trackFacts.ts`, `legend.ts`, `advisoryFlow.ts`, `api.ts`. The Web screen is
held to the same four chips, the same enabled-when-anything-is-exposed rule, the
same `—`-vs-`0` distinction and the same failure wording as the native one,
because it imports the modules rather than restating them.

**Different by necessity:**

- **The basemap.** There is no Google Maps key for the browser and this project
  ships no tile server, so the Web map draws its own. `land` and `water` — the
  two tokens this file already reserves for "map tints, not UI surfaces" — are
  read out of `theme.ts` at build time and painted onto
  `data/basemap/basemap.png`, rendered by `backend/tools/render_basemap.py` from
  the **0 m contour of the committed DEM**. That is the same threshold
  `dem.py`'s `ocean_mask()` floods from, so the coastline and the flood extent
  agree by construction. Display-only and deterministic; no figure is measured
  off it, and the screen says so.
- **The flood layer is an `<image>`, not a `<Polygon>`.** Same
  `/overlays` raster the native `<Overlay>` samples, placed through the same
  projection as every vector layer so they register exactly.
- **The track is dashed and always renders dashed.** `lineDashPattern` is
  *not* honoured by react-native-maps on Android (§ "Superseded light-theme
  component specs"); SVG honours it everywhere, so on Web the texture matches
  the legend instead of contradicting it.
- **The map chrome is the app's chrome, not white floating pills.** On a
  desktop the map is one panel in a grid rather than a full-bleed surface
  behind a control stack, so `white`/`onWhite` have nothing to sit against and
  the controls use `card` + `border` + `selectedText`. The inversion described
  in "Dark theme" above exists to keep controls legible *on a pale basemap*;
  on Web the controls sit on the app's own dark panel, so it would be a
  contradiction, not a deliberate echo.
- **The advisory is inline, not a modal.** A judge's viewport has the height for
  a document; a modal over the map would hide the map the advisory describes.
  Same content, same order, same disclosures.

### Still the same design system

`theme.typography` sizes are unchanged (22/16/16/13), the eight-px spacing base
holds, and the radius table (12 button / 16 card / 32 chip) is used as-is. The
four chips keep the documented idle/selected treatment. The map's flooded-area
figure keeps the "(model)" qualifier wherever it appears.

### Not specified anywhere, and still owed a human

The Web build's **information hierarchy** — which of the four steps is visually
primary at desktop width, and whether the map or the numbers should hold the
left column — is an editorial decision made here and not recorded above, in the
same sense as §37 and §43 for the native screen.

## React Native theme object

**SUPERSEDED — the light theme's object.** The current one is `mobile/theme.ts`
and its table is "Dark theme" above. Kept because the *shape* is still the
contract: a component reaches for `theme.colors.*` and never for a literal.

```ts
export const theme = {
  colors: {
    background: '#faf5e8',
    card: '#ffffff',
    text: '#181d26',
    textMuted: '#525965',
    border: '#e0e2e6',
    primary: '#1b61c9',
    water: '#254fad',
    waterFill: '#c7e5f2',
    danger: '#aa2d00',
    dangerDark: '#912e1f',
    safe: '#0a2e0e',
    caution: '#fcb42a',
  },
  radius: { button: 12, card: 16, chip: 32 },
  spacing: { xs: 8, sm: 16, md: 24, lg: 32, xl: 40, xxl: 64 },
  shadow: {
    card: {
      shadowColor: '#0f306a',
      shadowOffset: { width: 0, height: 4 },
      shadowOpacity: 0.08,
      shadowRadius: 12,
      elevation: 3, // Android
    },
  },
  fonts: {
    heading: 'RobotoSlab_700Bold',
    body: 'Inter_400Regular',
    bodyMedium: 'Inter_500Medium',
    bodySemibold: 'Inter_600SemiBold',
  },
  typography: {
    heading: 22,
    body: 16,
    emphasis: 16, // button labels — same size as body, bodySemibold weight
    caption: 13,
  },
};
```

Two changes from the above, both in the dark theme: `shadow.card` is **black at
0.5 opacity** (a blue-tinted shadow on a near-black surface is invisible, and a
shadow's job here is to separate a raised card from the page), and
`fonts.heading` is `Inter_600SemiBold` rather than a slab face.

## Font setup (Expo)

```bash
npx expo install @expo-google-fonts/inter expo-font
```

Load with the `useFonts` hook before rendering the app, per the standard
Expo Google Fonts pattern:

```ts
import { useFonts, Inter_400Regular, Inter_500Medium, Inter_600SemiBold } from '@expo-google-fonts/inter';

const [fontsLoaded] = useFonts({
  Inter_400Regular, Inter_500Medium, Inter_600SemiBold,
});
```

**The Roboto Slab dependency is gone** as of 2026-09-30 — the dark design calls
for one family, and dropping it means the slab face is no longer downloaded at
boot.
