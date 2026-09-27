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
