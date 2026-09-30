import type { MapStyleElement } from 'react-native-maps';

import { theme } from '../theme';

/**
 * Map layer styles and the basemap's `customMapStyle`.
 *
 * These are plain style/prop objects rather than components, so this file
 * imports **only a type** from `react-native-maps` — no runtime dependency on a
 * native module this file does not itself render. `import type` is erased at
 * compile time, so importing it here costs nothing at runtime and keeps
 * `MapStyleElement[]` as the authority on the style array's shape: adding a
 * field Google does not document is a compile error rather than something the
 * renderer quietly ignores.
 */

/**
 * The basemap, restyled.
 *
 * **The map is the only light thing in a dark app**, and that is the whole
 * point of the design: the chrome is near-black so the flood raster and the
 * asset pins are the brightest things on screen, which is where a viewer's
 * eye should go during an evacuation briefing. A dark basemap would put the
 * flood layer on a dark field and make depth classes much harder to read
 * against it.
 *
 * So: pale land (`land` #e6ece0), pale-blue water (`water` #c9e0ec), and
 * everything else pushed back. **POIs and transit are off.** A cyclone
 * exposure map with every restaurant and bus stop on it is unreadable, and
 * those markers are the ones most likely to be mistaken for the app's own
 * asset pins — the one confusion this map cannot afford.
 *
 * The style array is Google's documented format: an array of `{featureType,
 * stylers}` entries applied in order, later entries winning. Element types
 * (`geometry`, `labels.text`, `poi`, `transit`) are Google's own vocabulary —
 * this array is the one place in the app that speaks it, and a typo in a
 * `featureType` string is silently ignored by the renderer rather than
 * throwing, so the entries below are kept to the documented set.
 *
 * **Not `as const`.** The other exports in this file are, because they are
 * spread onto props and a readonly value is fine there. `customMapStyle` is
 * passed as the array itself, and react-native-maps types the prop as a mutable
 * `MapStyleElement[]`, so a readonly array is a compile error. The colour values
 * are still checked — they are `string` from `theme` either way.
 */
export const customMapStyle: MapStyleElement[] = [
  // --- everything, pulled back before anything specific is set -----------
  {
    elementType: 'geometry',
    stylers: [{ color: theme.colors.land }],
  },
  {
    elementType: 'labels.text',
    stylers: [{ color: theme.colors.textMuted }],
  },
  {
    elementType: 'labels.icon',
    stylers: [{ visibility: 'off' }],
  },

  // --- water, which `geometry` alone leaves as the land tint ------------
  {
    featureType: 'water',
    elementType: 'geometry',
    stylers: [{ color: theme.colors.water }],
  },
  {
    featureType: 'water',
    elementType: 'labels.text',
    stylers: [{ color: theme.colors.textMuted }],
  },

  // --- POIs and transit: off entirely -----------------------------------
  {
    featureType: 'poi',
    stylers: [{ visibility: 'off' }],
  },
  {
    featureType: 'transit',
    stylers: [{ visibility: 'off' }],
  },

  // --- roads kept, but quiet: the app draws its own, and the cut-off ----
  //     ones are the point. A basemap artery in a saturated colour competes
  //     with a `danger` polyline that means "this road is severed".
  {
    featureType: 'road',
    elementType: 'geometry',
    stylers: [{ color: theme.colors.card }],
  },
  {
    featureType: 'road',
    elementType: 'labels.text',
    stylers: [{ visibility: 'off' }],
  },
  {
    featureType: 'road.highway',
    elementType: 'geometry',
    stylers: [{ color: theme.colors.border }],
  },
  {
    featureType: 'administrative',
    elementType: 'geometry.stroke',
    stylers: [{ color: theme.colors.border }],
  },
  {
    featureType: 'administrative.land_parcel',
    stylers: [{ visibility: 'off' }],
  },
];

/** Spread onto a react-native-maps `<Polygon>` for the flood extent. */
export const floodPolygonStyle = {
  fillColor: theme.colors.flood,
  fillOpacity: 0.5,
  strokeColor: theme.colors.water,
} as const;

/** Spread onto a react-native-maps `<Polyline>` for a compromised road. */
export const compromisedRoadStyle = {
  strokeColor: theme.colors.danger,
} as const;

/**
 * The `lineDashPattern` for a compromised-road `<Polyline>`.
 *
 * Design.md specifies "dashed" without a dash length or gap, so the numbers
 * below are React Native Maps' own documented default-style dash unit, not a
 * design decision from Design.md's token table.
 *
 * Platform caveat, and it is a real one: `lineDashPattern` is **not honoured
 * by the Google Maps renderer on Android**, so on Android this renders as a
 * solid danger line. The legend draws its cut-off-road swatch dashed because
 * that is what the layer is *meant* to be, so on Android the legend and the
 * map disagree in shape while agreeing in colour. See MEMORY.md "Flagged for
 * review".
 */
export const compromisedRoadDashPattern = [6, 4];

/**
 * The storm path's line and pin colour: **near-black**, which is
 * `theme.colors.background` (#090909).
 *
 * The track is a *record of what happened*, drawn over a pale basemap. Near
 * black is the darkest available value and reads as ink on the pale land, and
 * it is distinct from the three colours that mean something on this map —
 * `flood` is water, `danger` is damage, `caution` is a substation.
 *
 * It is now **dashed** as well as near-black, which the light theme did not do.
 * The reason is that the track is the one layer that crosses the others: it
 * runs the length of the Bay, it crosses the flood raster near landfall, and
 * it passes over the delta where the cut-off roads cluster. A solid near-black
 * line over all three is a line that has to win every crossing. A dash lets
 * the flood and the severed roads stay legible underneath it, and it
 * distinguishes "what happened" from "what would happen" by texture as well as
 * by colour.
 */
export const trackLineStyle = {
  strokeColor: theme.colors.background,
} as const;

/**
 * The storm path's colour, as a plain string for Leaflet.
 *
 * `trackLineStyle` above is the `react-native-maps` shape (`{strokeColor}`),
 * which existed only for `<Polyline strokeColor={...}>`. Leaflet takes a bare
 * colour, so the map now reads this instead. Same value, one less indirection —
 * and `trackLineStyle` is kept because nothing else in the app may still
 * reference it, so removing it here would be a guess rather than a cleanup.
 */
export const trackLineColour = theme.colors.background;

/** The dash for the storm path. Wider than the road dash so the two differ. */
export const trackDashPattern = [10, 6];

export const trackPinColour = theme.colors.background;

/**
 * Pin colours for the two point-asset classes.
 *
 * Hospitals are `danger` and substations are `caution`. Both were `danger`
 * under the light theme, which made two different asset classes the same
 * colour on the map and forced the reader to open a callout to tell them
 * apart. The dark theme's `caution` is unchanged, so this costs no new token
 * and the two classes are now distinguishable at a glance — which is what
 * makes the substation's own legend row worth having.
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
 * leaves `bearing` at its default of 0. **Still true under the dark theme.**
 */
export const OVERLAY_BEARING = 0;

/**
 * The opening region, fixed to Sagar Island.
 *
 * Per the Module E brief: no location permission, so there is no "centre on
 * me" and no permission prompt in a live demo. Sagar Island is where Remal
 * made landfall and it is the anchor for the whole case study, so it is
 * hardcoded rather than derived from the first locality in `/localities`.
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
/**
 * The opening zoom, in Leaflet tile-zoom units.
 *
 * **Chosen from the previous `SAGAR_REGION` deltas rather than picked.** The
 * react-native-maps camera opened on a 0.36-degree latitude span; Leaflet
 * expresses zoom as a power-of-two tile scale, so the equivalent framing is
 * derived in `LeafletMap`'s caller comment rather than guessed. It is exposed
 * here because the WebView map needs a number and the delta was the only
 * statement of the intended framing in the codebase.
 */
export const SAGAR_INITIAL_ZOOM = 11;

export const TRACK_FIT_DURATION_MS = 600;

/**
 * `edgePadding` for `fitToCoordinates`, in dp.
 *
 * Nearly symmetric, with a slightly larger bottom. The bottom is not padding
 * for the readout panel: that panel is a *sibling* of the map wrapper, not an
 * overlay on it, so the MapView's viewport already excludes it and a large
 * bottom padding would waste roughly a third of the visible map.
 *
 * What the bottom padding is for is the storm-path control, which floats at the
 * map's right. 64dp lifts the southernmost fix — 18.75 N, well south of
 * Odisha — clear of that control instead of letting a pin sit on top of it.
 *
 * **The legend pill is bottom-LEFT and the three controls are on the RIGHT**
 * (both added/changed 2026-09-30), so the right-hand padding is the one doing
 * the work here and the left is only balancing it. The padding is left
 * symmetric because a fix landing on a legend row is obscured rather than
 * unreadable, and raising `left` would push the whole track further from the
 * edge on every device. This wants a real screen, which is still not
 * possible — see MEMORY.md.
 */
export const TRACK_FIT_PADDING = {
  top: 24,
  right: 32,
  bottom: 64,
  left: 32,
} as const;
