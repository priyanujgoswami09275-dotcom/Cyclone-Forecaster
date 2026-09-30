import { theme } from '../theme';

/**
 * Map layer styles — Design.md:
 *   - "Flood polygon on map: fill `waterFill` at 50% opacity, stroke `water`"
 *   - "Compromised road: dashed `Polyline`, color `danger`"
 *
 * These are plain style/prop objects rather than components, so this file
 * imports nothing from `react-native-maps`. The map screen can spread them
 * onto a `Polygon`/`Polyline` without this module taking a dependency on a
 * native module it does not itself render.
 */

/** Spread onto a react-native-maps `<Polygon>` for the flood extent. */
export const floodPolygonStyle = {
  fillColor: theme.colors.waterFill,
  fillOpacity: 0.5,
  strokeColor: theme.colors.water,
} as const;

/** Spread onto a react-native-maps `<Polyline>` for a compromised road. */
export const compromisedRoadStyle = {
  strokeColor: theme.colors.danger,
} as const;

/**
 * The `lineDashPattern` prop for a compromised-road `<Polyline>`.
 *
 * Design.md specifies "dashed" without a dash length or gap, so the
 * numbers below are React Native Maps' own documented default-style dash
 * unit, not a design decision from Design.md's token table. Worth a human
 * look if the dash should match the design intent.
 *
 * Platform caveat: `lineDashPattern` is not honoured by the Google Maps
 * renderer on Android, so on Android this renders as a solid danger line.
 * See MEMORY.md "Flagged for review".
 */
export const compromisedRoadDashPattern = [6, 4];

/**
 * The historical track's line and pin colour.
 *
 * **Design.md has no token assigned to the track layer** — it specifies the
 * flood polygon, compromised roads, priority chips, shelters and the primary
 * action, and never a cyclone's own path. Rather than invent a hex, this uses
 * `text` (Onyx, `#181d26`): a neutral dark that reads as a record of what
 * happened, and is already spoken for as the app's primary text, so it stays
 * distinct from the three colours that *mean* something here — `water` is
 * flood, `danger` is damage, `caution`/`safe` are severity levels.
 *
 * Flagged in MEMORY.md "Flagged for review": a design decision is owed on
 * whether a historical track should be neutral at all.
 */
export const trackLineStyle = {
  strokeColor: theme.colors.text,
} as const;

export const trackPinColour = theme.colors.text;

/**
 * Pin colours for the two point-asset classes.
 *
 * Design.md assigns `danger` to "compromised roads, CRITICAL priority chip"
 * and `safe` to "shelter markers". Hospitals and substations are neither, and
 * the tokens are deliberately not stretched to cover them: both are drawn
 * `danger` here because both are *submerged*, which is the same severity as a
 * cut-off road, and the class is distinguished by the callout text and by
 * `caution` on substations rather than by inventing a new colour. Worth a
 * human look — see MEMORY.md "Flagged for review".
 */
export const assetPinColours = {
  hospital: theme.colors.danger,
  substation: theme.colors.caution,
} as const;

/**
 * The one constant the flood `<Overlay>` needs and Design.md does not
 * specify.
 *
 * The raster is a single north-up image. `<Overlay>` takes a `bearing` and
 * does NOT track the map's rotation, so a rotated or pitched map would leave
 * the flood sitting at the wrong angle to the coastline — a subtle wrongness
 * that is much worse than losing the ability to spin the map. The map screen
 * therefore pins `rotateEnabled={false}` and `pitchEnabled={false}` and
 * leaves `bearing` at its default of 0.
 */
export const OVERLAY_BEARING = 0;

/**
 * The opening region, fixed to Sagar Island.
 *
 * Per the Module E brief: no location permission, so there is no "centre on
 * me" and no permission prompt in a live demo. Sagar Island is where Remal
 * made landfall and it is the anchor for the whole case study, so it is
 * hardcoded rather than derived from the first locality in `/localities`
 * (which happens to be Anantapur, alphabetically first, and is on the
 * northern edge of the study area).
 *
 * Centred on 21.68 N, 88.08 E with a ~0.36 deg span — roughly 40 km, wide
 * enough to hold the island, the north-south creek network and the Bay
 * coastline it floods.
 */
export const SAGAR_REGION = {
  latitude: 21.68,
  longitude: 88.08,
  latitudeDelta: 0.36,
  longitudeDelta: 0.36,
} as const;

/**
 * Animation duration for the "Zoom to Sagar" move, in ms.
 *
 * Long enough to read as a move rather than a cut, short enough not to hold up
 * a demo. The "Show storm path" fit passes `animated: true` to
 * `fitToCoordinates` and so uses the map's own default duration instead —
 * this one is explicit because `animateToRegion` takes a duration argument
 * that has to be a number.
 */
export const TRACK_FIT_DURATION_MS = 600;

/**
 * `edgePadding` for `fitToCoordinates`, in dp.
 *
 * Nearly symmetric, with a slightly larger bottom. The bottom is not padding
 * for the readout panel: that panel is a *sibling* of the map wrapper, not an
 * overlay on it, so the MapView's viewport already excludes it and a large
 * bottom padding would waste roughly a third of the visible map.
 *
 * What the bottom padding is for is the "Show storm path" control, which floats
 * at the map's bottom-right. 64dp lifts the southernmost fix — 18.75 N, well
 * south of Odisha — clear of that control instead of letting a pin sit on
 * top of it. The rest keeps the first and last fixes off the bezel.
 *
 * **The map legend now also floats at the bottom-left**, added 2026-09-30. The
 * padding is symmetric, so the left-hand fix is not deliberately cleared of
 * it — but the legend is far shorter than the control on the right, and a fix
 * that lands on a legend row is obscured rather than unreadable. Left alone
 * because increasing `left` would push the track further from the edge for
 * every device, and this is the kind of thing to settle by looking at a real
 * screen.
 */
export const TRACK_FIT_PADDING = {
  top: 24,
  right: 32,
  bottom: 64,
  left: 32,
} as const;
