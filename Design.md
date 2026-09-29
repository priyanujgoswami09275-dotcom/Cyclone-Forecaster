# Design.md — Visual System

Adapted from the Airtable-style reference (chosen for implementation ease)
into an actual Expo/React Native system for this app's real screens: map +
slider + exposure list + advisory modal. Not a marketing page — no hero,
no logo bar, no announcement banner.

## Why these choices

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
- Fonts: Inter (body/UI) and Roboto Slab (headings) — both real Google
  Fonts, installable directly via `@expo-google-fonts`, no custom font
  files needed.
- Shadows simplified to React Native's single-shadow model — the source's
  multi-layer box-shadow doesn't exist in RN; one soft shadow value is
  used everywhere a card needs lift.

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

| Role | Font | Weight | Size |
|---|---|---|---|
| Headings | Roboto Slab | 700 | 20–24px — **22px** used |
| Body | Inter | 400 | 15–16px — **16px** used |
| Emphasis / button labels | Inter | 600 | 15–16px — **16px** used |
| Caption / labels | Inter | 500 | 12–13px — **13px** used |

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

- **Map screen background**: `background` token behind the map container
- **Intensity slider**: track in `border`, filled portion + thumb in `primary`
- **Exposure list row** (hospital/substation/road): `card` background,
  small colored dot — `danger` if affected, `textMuted` if not
- **Priority chip** (CRITICAL/HIGH/MEDIUM/LOW): pill shape, background
  `danger`/`dangerDark`/`caution`/`safe` respectively, white text
- **Flood polygon on map**: fill `waterFill` at 50% opacity, stroke `water`
- **Compromised road**: dashed `Polyline`, color `danger`
- **Cyclone track (best-track polyline and its waypoint pins)**: `text`.
  Added 2026-09-29 to close MEMORY.md §36, which recorded that the token table
  assigned a colour to the flood, the roads, the priority chips, the shelters
  and the one main action, and said nothing about a storm's own path — so the
  app was drawing one from an unrecorded choice. `text` (Onyx) is deliberate:
  the track is a *record of what happened*, so it is drawn in a neutral that
  reads as neither hazard nor forecast, and the four colours that carry
  meaning on this map stay distinct from it. A third option was available and
  not taken — `primary`, as the single deliberate accent — because that token
  is reserved for "Generate Advisory" above.
- **Advisory modal**: `card` background, 16px radius, single soft shadow
- **"Generate Advisory" button**: `primary` fill, white text, 12px radius
- **SMS copy button**: ghost style — white fill, 1px `border`, `text` color

### Added 2026-09-29 without a prior spec

These use existing tokens only — no colour, font or size was invented — but
none of them had a line here before they were built, so they are recorded now
rather than left as undocumented implementation. Unlike the track token above,
these are **implementation choices, not decisions**; MEMORY.md §37 lists them
as owed a human look.

- **"Full track" map control**: a ghost chip at the map's bottom-right —
  `card` fill, 1px `border`, `text` label, `radius.chip`. Placed bottom-right
  because the flood-layer banner at the top is full-width and its height
  varies with its text.
- **Shelter disclosure notice**: `caution` fill, `text` body, `radius.button`.
  Placed above the advisory body rather than in a footer, so it is read before
  the numbers it qualifies. Non-dismissable by design.
- **"No flood-free route" notice**: `danger` fill, `card` text. Sits directly
  under the provenance line, above the summary, because it changes how every
  line below it should be read.
- **Stale-advisory notice**: `caution` fill, `text` body, shown when the
  advisory on screen was generated for a different intensity or origin than
  the current settings.
- **Failure states**: `danger` heading, `text`/`textMuted` body, violations in
  a `background` block. Only the `validation` state lists violations; every
  other failure shows prose alone.

## React Native theme object

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

## Font setup (Expo)

```bash
npx expo install @expo-google-fonts/inter @expo-google-fonts/roboto-slab expo-font
```

Load with the `useFonts` hook before rendering the app, per the standard
Expo Google Fonts pattern:

```ts
import { useFonts, Inter_400Regular, Inter_500Medium, Inter_600SemiBold } from '@expo-google-fonts/inter';
import { RobotoSlab_700Bold } from '@expo-google-fonts/roboto-slab';

const [fontsLoaded] = useFonts({
  Inter_400Regular, Inter_500Medium, Inter_600SemiBold, RobotoSlab_700Bold,
});
```
