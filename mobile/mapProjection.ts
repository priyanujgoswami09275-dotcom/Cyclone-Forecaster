/**
 * Web map geometry — projection, bounds and path building, with no renderer.
 *
 * **Imports nothing but types**, for the same reason `legend.ts` and
 * `exposureTiles.ts` do: `node --test` loads this file directly, and a value
 * import of `theme`/`mapStyles` would be an extensionless specifier Node's ESM
 * resolver refuses to follow. That is what makes the projection testable at
 * all, and the projection is precisely the thing that must be tested — a
 * transposed axis here puts Sagar Island in the Bay of Bengal and still
 * renders.
 *
 * The native app does not use this file. It projects through
 * `react-native-maps`' own camera, and the Web app cannot use that module at
 * all (see `MapScreen.web.tsx`).
 */

/**
 * A geographic bounding box, matching the shape both `OverlayEntry.bounds` and
 * the backend's DEM bbox use, so the two can be compared directly.
 */
export interface GeoBounds {
  west: number;
  south: number;
  east: number;
  north: number;
}

/**
 * The Sagar Island study region — the app's opening view, and the same place
 * `SAGAR_REGION` in `mapStyles.ts` centres the native map on.
 *
 * Named here rather than imported because `mapStyles.ts` is native-adjacent.
 * The coordinates are the committed `data/places.geojson` coordinate for the
 * `sagar` locality, which is what `/localities` serves.
 */
export const SAGAR_CENTRE = { latitude: 21.6476, longitude: 88.0568 } as const;

/**
 * The opening view's span, chosen to frame the delta and the Sagar Island
 * blocks the flood reaches at the two intensities that expose anything.
 *
 * Slightly wider than the native `SAGAR_REGION` deltas, because a desktop
 * viewport is wider than a phone: at 16:9 the same longitude span shows more
 * latitude, and the extra room costs nothing on a screen with width to spare.
 */
export const OPENING_SPAN_DEG = {
  latitude: 1.15,
  longitude: 1.6,
} as const;

/** Bounds centred on `SAGAR_CENTRE` at `OPENING_SPAN_DEG`. */
export function openingBounds(): GeoBounds {
  return {
    west: SAGAR_CENTRE.longitude - OPENING_SPAN_DEG.longitude / 2,
    east: SAGAR_CENTRE.longitude + OPENING_SPAN_DEG.longitude / 2,
    south: SAGAR_CENTRE.latitude - OPENING_SPAN_DEG.latitude / 2,
    north: SAGAR_CENTRE.latitude + OPENING_SPAN_DEG.latitude / 2,
  };
}

/**
 * Bounds that contain every finite point in `points`.
 *
 * Returns null when there is nothing finite to fit — an empty list, or a list
 * of NaNs. The caller decides what an unfittable set means; it cannot
 * silently centre on (0, 0), which is in the Atlantic off West Africa and
 * would render as a blank map with no error.
 *
 * The NaN filter is load-bearing and is not paranoia. `/track` coerces
 * positions with `float()` without range-checking, and `json.loads` accepts a
 * bare `NaN`, so a corrupt or hand-edited `data/remal_track.geojson` really can
 * put a non-finite latitude on the wire. `mapStyles.ts` already filters on
 * `Number.isFinite` before calling `fitToCoordinates` for the same reason.
 *
 * `padding` is a fraction of the computed span on each side, so a track whose
 * span is near zero still gets a usable box rather than collapsing to a point.
 */
export function boundsOf(
  points: ReadonlyArray<{ latitude: number; longitude: number }>,
  padding = 0.12,
): GeoBounds | null {
  let west = Number.POSITIVE_INFINITY;
  let east = Number.NEGATIVE_INFINITY;
  let south = Number.POSITIVE_INFINITY;
  let north = Number.NEGATIVE_INFINITY;
  let seen = 0;

  for (const point of points) {
    if (!Number.isFinite(point.latitude) || !Number.isFinite(point.longitude)) continue;
    seen += 1;
    if (point.longitude < west) west = point.longitude;
    if (point.longitude > east) east = point.longitude;
    if (point.latitude < south) south = point.latitude;
    if (point.latitude > north) north = point.latitude;
  }
  if (seen === 0) return null;

  const padLon = Math.max((east - west) * padding, 0.02);
  const padLat = Math.max((north - south) * padding, 0.02);
  return {
    west: west - padLon,
    east: east + padLon,
    south: south - padLat,
    north: north + padLat,
  };
}

/**
 * A lon/lat point placed in a fixed viewBox, plus the box it was placed in.
 *
 * The projection is **plate carrée** — linear in both axes, no cos(lat)
 * correction. That is a deliberate choice for a 1.4°-wide view at 22°N: over
 * that span a Web Mercator correction would shift points by at most ~2% of
 * the frame, and it would make the y-axis non-linear, which buys nothing and
 * makes the flood raster's placement harder to reason about. The basemap and
 * the flood raster use the *same* projection, so they register exactly.
 *
 * The y-axis is flipped: latitude increases northward, SVG y increases
 * downward. Getting that backwards draws the Bay of Bengal where the delta
 * is, and the whole delta in the sea.
 */
export interface Projection {
  bounds: GeoBounds;
  /** The SVG viewBox the projection maps onto. */
  width: number;
  height: number;
  project(point: { latitude: number; longitude: number }): { x: number; y: number };
}

export function makeProjection(bounds: GeoBounds, width: number, height: number): Projection {
  const spanLon = bounds.east - bounds.west;
  const spanLat = bounds.north - bounds.south;
  // A zero span would divide by zero. Every caller passes a padded box, but a
  // degenerate one should still produce a finite projection rather than NaN
  // coordinates that silently draw nothing.
  const safeLon = spanLon === 0 ? 1 : spanLon;
  const safeLat = spanLat === 0 ? 1 : spanLat;

  return {
    bounds,
    width,
    height,
    project(point) {
      const x = ((point.longitude - bounds.west) / safeLon) * width;
      const y = height - ((point.latitude - bounds.south) / safeLat) * height;
      return { x, y };
    },
  };
}

/**
 * An SVG `points` attribute from a lon/lat path, or null when there is nothing
 * drawable.
 *
 * Returns null rather than an empty string so the caller can omit the element
 * instead of rendering a `<polyline points="">`, which renders as a stray
 * dot in some browsers. Non-finite vertices are dropped mid-path rather than
 * poisoning the whole line with one `NaN`, which is what SVG does with them.
 *
 * A path of one point is not returned: an SVG polyline needs two vertices to
 * be a line, and a single vertex would be an invisible dot pretending to be
 * a road.
 */
export function pathPoints(
  coordinates: ReadonlyArray<number[]>,
  projection: Projection,
  minVertices = 2,
): string | null {
  const out: string[] = [];
  for (const pair of coordinates) {
    if (!Array.isArray(pair) || pair.length < 2) continue;
    const [longitude, latitude] = pair;
    if (!Number.isFinite(longitude) || !Number.isFinite(latitude)) continue;
    const { x, y } = projection.project({ latitude, longitude });
    out.push(`${x.toFixed(1)},${y.toFixed(1)}`);
  }
  return out.length >= minVertices ? out.join(' ') : null;
}

/** As `pathPoints`, for an already-{latitude,longitude} list (e.g. `/track`). */
export function latLngPoints(
  points: ReadonlyArray<{ latitude: number; longitude: number }>,
  projection: Projection,
  minVertices = 2,
): string | null {
  const out: string[] = [];
  for (const point of points) {
    if (!Number.isFinite(point.latitude) || !Number.isFinite(point.longitude)) continue;
    const { x, y } = projection.project(point);
    out.push(`${x.toFixed(1)},${y.toFixed(1)}`);
  }
  return out.length >= minVertices ? out.join(' ') : null;
}

/**
 * Where an image with geographic `bounds` sits inside a projected viewBox.
 *
 * The flood rasters and the basemap are images carrying their own bounds, and
 * projecting them through the same `makeProjection` as every vector layer is
 * what makes them register with the track and the roads to the pixel.
 *
 * Returns null for a degenerate box so a caller can omit the element rather
 * than emit a `<image>` with `NaN` width.
 */
export function imageRect(
  bounds: GeoBounds,
  projection: Projection,
): { x: number; y: number; width: number; height: number } | null {
  const spanLon = bounds.east - bounds.west;
  const spanLat = bounds.north - bounds.south;
  if (spanLon === 0 || spanLat === 0) return null;
  const nw = projection.project({ latitude: bounds.north, longitude: bounds.west });
  const se = projection.project({ latitude: bounds.south, longitude: bounds.east });
  return {
    x: nw.x,
    y: nw.y,
    width: se.x - nw.x,
    height: se.y - nw.y,
  };
}
