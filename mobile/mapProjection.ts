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
 *
 * **Retained for the tests and for anyone who needs the case-study point**, but
 * **no longer used as the centre of the opening view** — see `OPENING_BOUNDS`.
 * It was, and that hid every exposed asset.
 */
export const SAGAR_CENTRE = { latitude: 21.6476, longitude: 88.0568 } as const;

/**
 * The opening view.
 *
 * **Chosen to be the basemap's own extent, and the reason is a measurement
 * rather than a preference.**
 *
 * This was centred on Sagar Island at 1.6 x 1.15 degrees, and the infrastructure
 * the app exists to show is not there. On the live `/exposure?category=6`
 * payload the 12 exposed hospitals sit at **lat 22.261-22.588** and the 22
 * substations at **22.265-22.592**. Sagar is at **21.65 N**, the southern end
 * of the study area, so a 1.15-degree frame centred on it reaches only 22.22 N
 * and **every one of the 34 markers fell outside it**.
 *
 * They were being drawn correctly — the DOM held exactly 12 `#a11d00` circles
 * and 22 `#fcb42a` ones, matching the API counts — and none could be seen. That
 * is a worse bug than not drawing them, because the tiles beside the map said
 * "Hospitals 12" while the map showed none.
 *
 * The frame is now the DEM extent, which is what `render_basemap.py` renders
 * and what `BASEMAP_META.bounds` already records. That single choice closes
 * three things at once:
 *
 *   - **all 34 exposed assets are inside** (verified above), as is Sagar, the
 *     default origin, and the flood extent at every category;
 *   - **the basemap image fills the frame exactly**, because it *is* that
 *     extent — no gutter, and nothing to keep in sync;
 *   - the reported flood area and the picture are the same thing, which is the
 *     point of the overlay discipline in MEMORY.md §32.
 *
 * `viewBoxFor` then shapes the frame to its ground aspect, so the drawing is
 * never letterboxed. See `groundAspect`.
 */
export const OPENING_BOUNDS: GeoBounds = {
  west: 87.8,
  south: 21.3,
  east: 89.2,
  north: 22.6016,
};

/** The opening view. Named as a constant so a test can assert it. */
export function openingBounds(): GeoBounds {
  return { ...OPENING_BOUNDS };
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
 * **The cos(lat) factor does appear exactly once, in `groundAspect`** — not to
 * bend the projection, but to choose a viewBox shape that matches the ground.
 * That is the only place the two conventions meet, which is why keeping it
 * there is what makes the rest of this file safe.
 *
 * The y-axis is flipped: latitude increases northward, SVG y increases
 * downward. Getting that backwards draws the Bay of Bengal where the delta
 * is, and the whole delta in the sea.
 */
/**
 * The cosine of latitude at the centre of the study area, used to convert a
 * geographic span into a ground-distance ratio.
 *
 * At 22.5°N, `cos(lat) = 0.924`. One degree of longitude is therefore ~92% of
 * one degree of latitude in ground distance, which is why a bbox quoted in
 * degrees cannot be compared to a pixel aspect ratio directly. This constant is
 * the whole of the correction, and it is named because two places need it and
 * they must agree.
 */
export const LATITUDE_COSINE = 0.924;

/**
 * Ground-distance aspect ratio (width / height) of a geographic box.
 *
 * This is what the viewBox aspect has to match for a plate-carrée projection to
 * fill its frame. `spanLon / (spanLat * cos(lat))` — **not** `spanLon /
 * spanLat`, which is wrong by 8% here: small enough to look fine in a test and
 * large enough to push real geometry off-screen.
 */
export function groundAspect(bounds: GeoBounds): number {
  const spanLon = bounds.east - bounds.west;
  const spanLat = bounds.north - bounds.south;
  if (spanLat === 0) return 1;
  return spanLon / (spanLat * LATITUDE_COSINE);
}

/**
 * A viewBox whose aspect ratio matches `bounds`, so the drawing fills the
 * frame instead of being letterboxed.
 *
 * **This is the fix for markers that were drawn but invisible.** The Web map
 * drew every hospital and substation — 12 and 22 circles, exactly matching the
 * API — and 84 of 91 circles fell *outside* the viewBox. The cause was an
 * aspect mismatch: a fixed `1000x760` viewBox against a bbox whose ground
 * aspect was ~1.62, so `preserveAspectRatio="xMidYMid meet"` fitted the data
 * into a letterboxed band while the projection — which assumes the full viewBox
 * *is* the full bbox — overshot vertically. The basemap image had the same
 * problem, which is the black gutters reported alongside it.
 *
 * Deriving the height from the width means the frame is always the right shape
 * for whatever is being drawn: the opening region, a fitted track, or anything
 * else. No hand-tuned numbers, and it cannot drift out of sync with the
 * projection the rest of this file applies.
 */
export function viewBoxFor(
  bounds: GeoBounds,
  width = 1000,
): { width: number; height: number } {
  const aspect = groundAspect(bounds);
  if (!Number.isFinite(aspect) || aspect <= 0) return { width, height: width };
  return { width, height: Math.round(width / aspect) };
}


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
