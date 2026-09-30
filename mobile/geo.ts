/**
 * The one place this app converts between coordinate conventions.
 *
 * Three conventions meet here and they are transposed against each other:
 *
 * | Convention | Order | Source |
 * |---|---|---|
 * | GeoJSON / the backend | `[lon, lat]` | every `geometry.coordinates` in `/exposure`, `/track` |
 * | `react-native-maps` | `{latitude, longitude}` | was `MapScreen.tsx`'s `<Marker coordinate>` |
 * | Leaflet | `[lat, lng]` | `L.marker`, `L.polyline`, `map.fitBounds` |
 *
 * **A swap does not throw.** It places the marker in the Bay of Bengal or off
 * the coast of Bangladesh at roughly the right distance from the right place.
 * The study area is about 1.4° wide, so the error is on the order of 150 km —
 * large enough to be obvious if anyone looks, small enough to survive a demo
 * if nobody does. That is why the conversion is written once, named, and
 * tested rather than inlined at each call site.
 *
 * `toLatLng` (in `api.ts`) already handles GeoJSON → the `react-native-maps`
 * object shape and is still used by the native-only helpers. **This module
 * handles GeoJSON → Leaflet**, which is what the WebView map speaks.
 */

/** `[lat, lng]` — Leaflet's `LatLngTuple`. */
export type LeafletLatLng = [number, number];

/**
 * Rejects anything that is not a usable geographic pair.
 *
 * `/track` coerces positions with Python's `float()` and does not range-check,
 * and `json.loads` accepts a bare `NaN` — so a corrupt committed file can put
 * `NaN` or `Infinity` into `path`. Handing one to Leaflet puts the marker at
 * an undefined place and, worse, makes `fitBounds` throw on an uncomputable
 * `LatLngBounds`. Filtering here means a corrupt row is dropped instead of
 * taking the map down.
 */
function isUsablePair(value: unknown): value is number[] {
  return (
    Array.isArray(value) &&
    value.length >= 2 &&
    Number.isFinite(value[0]) &&
    Number.isFinite(value[1])
  );
}

/**
 * A GeoJSON `[lon, lat]` pair as Leaflet's `[lat, lng]`.
 *
 * Returns null for a short or non-finite pair so the caller can skip the
 * feature rather than draw it in the sea — which on a map is indistinguishable
 * from a real position and would quietly disagree with the count in the tile.
 */
export function toLeaflet(
  pair: number[] | null | undefined,
): LeafletLatLng | null {
  if (!isUsablePair(pair)) return null;
  const [longitude, latitude] = pair;
  return [latitude, longitude];
}

/** A whole coordinate list converted, dropping any unusable vertex. */
export function toLeafletList(
  coordinates: ReadonlyArray<number[]>,
): LeafletLatLng[] {
  return coordinates
    .map(toLeaflet)
    .filter((p): p is LeafletLatLng => p !== null);
}

/**
 * The other direction: this app's own `{latitude, longitude}` object as Leaflet's
 * `[lat, lng]`.
 *
 * `/track`'s `path` and `roadPaths()` already speak the named-object form, so
 * they arrive here rather than through `toLeaflet`. Having both directions in
 * this one file is the point: there is now no second place in the app that
 * knows Leaflet orders its pairs the other way round.
 *
 * Returns null rather than a NaN-containing pair, for the same reason
 * `toLeaflet` does — Leaflet's `fitBounds` throws on an uncomputable
 * `LatLngBounds`, and a `NaN` marker draws in the sea.
 */
export function fromLatLng(
  point: { latitude: number; longitude: number } | null | undefined,
): LeafletLatLng | null {
  if (!point) return null;
  const { latitude, longitude } = point;
  if (!Number.isFinite(latitude) || !Number.isFinite(longitude)) return null;
  return [latitude, longitude];
}

/** A whole `{latitude, longitude}` list converted, dropping any unusable vertex. */
export function fromLatLngList(
  points: ReadonlyArray<{ latitude: number; longitude: number }>,
): LeafletLatLng[] {
  return points
    .map(fromLatLng)
    .filter((p): p is LeafletLatLng => p !== null);
}

/**
 * `fitBounds` needs at least one point to compute a centre, and one point
 * gives it no extent to fit. Returns null so the caller can leave the camera
 * alone rather than ask Leaflet for a degenerate view.
 */
export function fitBoundsInput(
  points: ReadonlyArray<LeafletLatLng>,
): LeafletLatLng[] | null {
  return points.length >= 2 ? [...points] : null;
}