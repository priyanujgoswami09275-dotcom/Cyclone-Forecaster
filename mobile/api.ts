/**
 * Backend API client.
 *
 * One module owns three things that are easy to get subtly wrong and painful
 * to debug from a UI bug report:
 *
 *   1. The response TYPES, mirrored field-for-field from what
 *      `backend/main.py` actually returns. Not from what the docs say it
 *      returns — the shapes below were read off a running instance, because
 *      a type that drifts from the wire is worse than no type at all: it
 *      compiles and then renders `undefined`.
 *   2. The TIMEOUTS, which are a function of how long the backend can
 *      legitimately take, not of taste.
 *   3. The ERROR taxonomy, so a screen can say something true about *why*
 *      it failed instead of "something went wrong".
 *
 * It owns no UI. Nothing here imports react-native or react-native-maps, so
 * it is testable in plain Node and the map screen can stay presentational.
 */

// ---------------------------------------------------------------------------
// Configuration
// ---------------------------------------------------------------------------

/**
 * Base URL comes from the environment, never from a literal in this file.
 *
 * There is no safe default. `localhost` is wrong on a physical phone (the
 * device's "localhost" is the phone), and a LAN IP baked into source is
 * wrong on the next network. Expo inlines `EXPO_PUBLIC_*` at build time, so
 * the value is visible in the bundle and must be a plain http(s) origin with
 * no trailing slash and no `/` path.
 *
 * Per render.yaml, GEMINI_API_KEY is a dashboard-only secret and is never
 * in the repo. This file sends no key and no auth of any kind: the backend
 * is public and unauthenticated by design for the demo.
 */
export const API_BASE_URL: string = (process.env.EXPO_PUBLIC_API_URL ?? '').trim();

/**
 * Timeout for POST /advisory.
 *
 * 120 s, and that number is derived, not chosen. The worst legitimate case
 * is:
 *   - 3 Gemini calls for the first draft (the capacity ladder retries the
 *     SAME model on 503 UNAVAILABLE, up to 3 attempts, 2 s + 4 s of backoff)
 *   - + 3 more for the correction pass, which repeats the same ladder
 *   - + 12 s of backoff across both ladders
 *   - + 6 x model latency
 * That lands at roughly 60-90 s. 120 s leaves headroom without letting a
 * genuinely hung request sit on the screen indefinitely.
 *
 * Consequence worth stating plainly: if this timeout were shorter than the
 * backend's worst case, the app would show a failure while the backend was
 * still working, and the user would retry a request that had already spent
 * scarce Gemini quota. Do not lower it to "feels snappier" without redoing
 * this arithmetic.
 */
export const ADVISORY_TIMEOUT_MS = 120_000;

/**
 * Timeout for the GET endpoints. Shorter than the advisory because none of
 * them touch Gemini, but `/surge-zone` is not cheap on a cold cache: it runs
 * the flood propagation and polygonises the result. 45 s covers that with
 * room, and still fails fast enough for a usable error.
 */
export const READ_TIMEOUT_MS = 150_000;

/**
 * **Raised from 45 s to 150 s on 2026-10-01, and the measurement is why.**
 *
 * This was found by running the deployed Web app against the deployed backend
 * and timing it, not by reasoning about it. `/exposure?category=6` on a cold
 * Vercel function measured **113.9 s** — the flood propagation over the
 * 2898x3117 DEM plus the polygonisation — while the same request warm is
 * **1.4-1.9 s**.
 *
 * At the old 45 s the app therefore *aborted its own request* on the very
 * category the demo leads with, and reported "did not complete" for a request
 * the backend was about to answer. That is the worst failure shape available:
 * a correct backend made to look broken by the client's impatience.
 *
 * The figure is a bound, not a guess: Vercel's Hobby ceiling is 300 s per
 * request (MEMORY.md "Next step"), so 150 s leaves headroom under the platform
 * limit while comfortably exceeding the measured cold start. It is far above
 * anything warm — warm requests finish in under 2 s and never wait on it.
 *
 * `ADVISORY_TIMEOUT_MS` (120 s) is deliberately **not** raised: it is derived
 * from the Gemini call budget, not from the simulation, and the backend's own
 * worst case for that path is 60-90 s.
 */

// ---------------------------------------------------------------------------
// Errors
// ---------------------------------------------------------------------------

/**
 * Why a request failed, from the caller's point of view. The UI branches on
 * this; it should never branch on a bare status code, because the same code
 * means different things per endpoint (a 502 from /advisory carries honesty
 * violations worth showing the user, a 502 from elsewhere does not).
 */
export type ApiErrorKind =
  | 'config' // EXPO_PUBLIC_API_URL is unset — a build problem, not a network one
  | 'timeout' // we gave up waiting; the backend may still be working
  | 'network' // never reached the server: airplane mode, wrong host, CORS
  | 'capacity' // 503 + Retry-After — Gemini is at capacity, retrying later can work
  | 'quota' // 429, no Retry-After — the daily request limit is spent; resets at midnight Pacific
  | 'validation' // 502 carrying `violations` — the advisory was withheld on purpose
  | 'upstream' // 502/501 with no violations — something failed upstream
  | 'http'; // any other non-2xx

export class ApiError extends Error {
  readonly kind: ApiErrorKind;
  /** HTTP status, or 0 when the request never got a response. */
  readonly status: number;
  /** The backend's `detail`, stringified if it was an object. */
  readonly detail: string;
  /** Parsed `Retry-After` in seconds, when the response carried one. */
  readonly retryAfterSeconds: number | null;
  /** Honesty-check violations, present only for kind === 'validation'. */
  readonly violations: string[];

  constructor(init: {
    kind: ApiErrorKind;
    status: number;
    message: string;
    detail?: string;
    retryAfterSeconds?: number | null;
    violations?: string[];
  }) {
    super(init.message);
    this.name = 'ApiError';
    this.kind = init.kind;
    this.status = init.status;
    this.detail = init.detail ?? init.message;
    this.retryAfterSeconds = init.retryAfterSeconds ?? null;
    this.violations = init.violations ?? [];
  }
}

// ---------------------------------------------------------------------------
// Shared response fragments
// ---------------------------------------------------------------------------

/**
 * Provenance of the surge number — the model is an estimate and says so.
 *
 * This mirrors the anchored scaling law `1.2 * (wind_kmph / 115) ** 2`. It
 * used to describe a fitted regression and carried that model's diagnostics:
 * `loo_mae_m`, `raw_prediction_m`, `clamped`, and the two `*_assumed` flags
 * for inputs the app never observed. There is no fit now and nothing is
 * assumed, so those are gone rather than left as decoration — reading them
 * would mean reading a number that no longer means anything. The replacement
 * provenance is `method` plus the anchor the number is scaled from.
 */
export interface SurgeResult {
  wind_kmph: number;
  imd_category: string;
  surge_m: number;
  /** Identifies the method. "anchored_quadratic_scaling" today. */
  method: string;
  /** The observed event every number is scaled from: Cyclone Remal, May 2024. */
  anchor_wind_kmph: number;
  anchor_surge_m: number;
  is_estimate: boolean;
}

/** The single event the surge model is anchored to, with its citation. */
export interface SurgeAnchor {
  wind_kmph: number;
  surge_m: number;
  event: string;
  source: string;
}

/**
 * The context block every simulation endpoint opens its response with. Kept
 * as a named interface (rather than inlined five times) so a change to
 * `backend/main.py::_category_header` has exactly one place to be mirrored.
 */
export interface CategoryHeader {
  category: number;
  imd_category: string;
  /**
   * IMD's **km/h** column. `upper` is null for category 6: IMD documents
   * Super Cyclonic Storm as >=222 kmph with no ceiling, so the band is
   * open-ended and has no midpoint. Anything that assumed a number here would
   * be inventing one.
   *
   * This field briefly held the **knots** column (top band starting at 120)
   * while being named and displayed as km/h, which made every wind about
   * 1.85x too small. `band_knots` ships beside it so the two are checkable
   * against each other rather than against a source file.
   */
  band_kmph: { lower: number; upper: number | null };
  /** IMD's **knots** column for the same band. Transparency, not input. */
  band_knots: { lower: number; upper: number | null };
  wind_kmph: number;
  wind_is_band_midpoint: boolean;
  surge_m: number;
  method: string;
  anchor: SurgeAnchor;
  limitation: string;
  surge: SurgeResult;
}

export interface Locality {
  /** Stable slug, e.g. "sagar". This is what /routes and /allocation take. */
  id: string;
  name: string;
  lon: number;
  lat: number;
  radius_km: number;
  place: string;
  source: string;
}

/** How the study area was drawn, and what was excluded to draw it. */
export interface Scoping {
  study_area_districts: string[];
  out_of_district_excluded: Record<string, string>;
  out_of_district_excluded_count: number;
  deny_list_not_in_extract: string[];
  unresolved_border_localities: string[];
  unresolved_border_note: string;
  study_area_max_lat: number;
  localities_in_study_area: number;
  localities_excluded: number;
  excluded_names: string[];
  excluded_reasons: Record<string, string>;
  disclosure: string;
}

// ---------------------------------------------------------------------------
// Endpoint response types
// ---------------------------------------------------------------------------

/**
 * A named scenario the app can select directly, by `id` — not by stepping to
 * a category. The seven IMD bands are a classification scheme, not a set of
 * events, and none of their representative winds is the storm this project is
 * about: Remal made landfall at 110-120 kmph, which is Severe Cyclonic Storm
 * (89-117 kmph) and equal to no band's midpoint — the nearest is category 3's
 * 103 kmph. A UI that can only step through categories 0-6 therefore cannot
 * show the actual case study, which is why this exists.
 */
export interface SurgePreset {
  id: string;
  label: string;
  wind_kmph: number;
  surge_m: number;
  source: string;
}

export interface CategoriesResponse {
  source: string;
  representative_wind: string;
  method: string;
  /** `imd_band` is where the anchor falls on IMD's km/h column. */
  anchor: { wind_kmph: number; surge_m: number; imd_band: string };
  limitation: string;
  categories: (CategoryHeader & {
    /**
     * Present and non-empty when the band produces less surge than the DEM's
     * 1 m vertical resolution can represent, so the flood model returns no
     * inundation for it. A UI should say so rather than show an empty map
     * with no explanation.
     */
    note?: string;
  })[];
  presets: SurgePreset[];
}

export interface LocalitiesResponse {
  count: number;
  source: string;
  note: string;
  scoping: Scoping;
  localities: Locality[];
}

/**
 * Flood geometry as the backend emits it. `MultiPolygon` puts its polygons
 * under `coordinates`; `GeometryCollection` puts them under `geometries`; a
 * `Polygon` has a single `coordinates`. All three occur, so all three are in
 * the type and `floodPolygons()` below normalises them.
 */
export type FloodGeometry =
  | { type: 'Polygon'; coordinates: number[][][] }
  | { type: 'MultiPolygon'; coordinates: number[][][][] }
  | { type: 'GeometryCollection'; geometries: FloodGeometry[] };

export interface FloodFrameProperties {
  step: number;
  water_level_m: number;
  area_km2: number;
  land_area_km2: number;
  new_land_area_km2: number;
  /** Area omitted from the geometry for renderability. Not zero at peak. */
  dropped_detail_km2: number;
}

export interface FloodFeature {
  type: 'Feature';
  properties: FloodFrameProperties;
  geometry: FloodGeometry;
}

export interface SurgeZoneResponse extends CategoryHeader {
  /** Modelled extent — the number to report. */
  final_land_area_km2: number;
  /** What the polygon actually shows. Lower; the difference is dropped detail. */
  drawn_area_km2: number;
  is_estimate: boolean;
  area_disclosure: string;
  frame_count: number;
  definitions: Record<string, string>;
  geojson: {
    type: 'FeatureCollection';
    final_land_area_km2: number;
    drawn_area_km2: number;
    features: FloodFeature[];
  };
}

/**
 * One exposed asset.
 *
 * The geometry type is **mixed**, and this type was wrong about that until
 * 2026-09-28. It previously claimed every asset was a `LineString` on the
 * reasoning that all three groups come from the same OSM extract, and told
 * callers to take `coordinates[0]`. That silently renders a hospital marker
 * at `[lon, lat]`'s *lon* value as if it were a coordinate pair.
 *
 * The backend passes each source geometry through untouched
 * (`_record()` in `backend/simulation/exposure.py` emits
 * `geometry.__geo_interface__` verbatim), and OSM tags a hospital as a node
 * (`Point`) far more often than a footprint. Measured on the live payload at
 * category 6: 6 `Point` and 28 `LineString` across hospitals + substations.
 * At category 5 all 15 are `LineString`, so the mix is category-dependent
 * and cannot be special-cased.
 *
 * Use `markerCoordinate()` below rather than indexing into this by hand.
 */
export interface InfraFeature {
  type: 'Feature';
  properties: {
    name: string;
    status: string;
    amenity?: string;
    power?: string;
    highway?: string;
  };
  geometry:
    | { type: 'Point'; coordinates: number[] }
    | { type: 'LineString'; coordinates: number[][] }
    | { type: 'Polygon'; coordinates: number[][][] }
    | { type: 'MultiPolygon'; coordinates: number[][][][] };
}

/**
 * One [lon, lat] to hang a `<Marker>` on, whatever shape the asset arrived
 * in. Returns null for an empty or unrecognised geometry, because a marker
 * with a NaN coordinate renders off-screen and looks like a missing asset.
 */
export function markerCoordinate(feature: InfraFeature): number[] | null {
  const g = feature.geometry;
  switch (g.type) {
    case 'Point':
      return g.coordinates.length >= 2 ? g.coordinates : null;
    case 'LineString':
      return g.coordinates[0] ?? null;
    case 'Polygon':
      return g.coordinates[0]?.[0] ?? null;
    case 'MultiPolygon':
      return g.coordinates[0]?.[0]?.[0] ?? null;
    default:
      return null;
  }
}

/**
 * A GeoJSON `[lon, lat]` pair as the `{latitude, longitude}` object
 * `react-native-maps` wants.
 *
 * The two conventions are transposed, and getting it wrong does not throw —
 * it puts a marker in the Bay of Bengal or off the coast of Bangladesh, at
 * roughly the right distance from the right place. At 22°N the whole study
 * area is about 1.4° wide, so a swap is a ~150 km error, not a subtle one,
 * but it is silent either way. Every conversion from the wire to the map goes
 * through here.
 *
 * Returns null for a short or non-finite pair so the caller can skip the
 * feature rather than hand the native view a NaN, which renders off-screen
 * and reads as a missing asset.
 */
export function toLatLng(pair: number[] | null | undefined): LatLng | null {
  if (!Array.isArray(pair) || pair.length < 2) return null;
  const [longitude, latitude] = pair;
  if (!Number.isFinite(longitude) || !Number.isFinite(latitude)) return null;
  return { latitude, longitude };
}

/** A whole `LineString` converted, dropping any unusable vertex. */
export function toLatLngList(coordinates: number[][]): LatLng[] {
  return coordinates
    .map(toLatLng)
    .filter((p): p is LatLng => p !== null);
}

/**
 * The `{latitude, longitude}` shape, declared locally so `api.ts` keeps its
 * "imports nothing from react-native-maps" rule. That rule is worth keeping:
 * it is what lets this module be tested in plain Node, and it is why the
 * conversions above return a structural type rather than the library's own.
 */
export interface LatLng {
  latitude: number;
  longitude: number;
}

export interface InfraGroup<F = InfraFeature> {
  count: number;
  features: F[];
}

/**
 * A cut-off road.
 *
 * Typed separately from `InfraFeature` because the backend really does
 * constrain it: `backend/simulation/exposure.py::_load_line_layer` accepts
 * only `LineString` and `MultiLineString` for the roads layer, so no road can
 * arrive as a `Point`. The point assets get the wide mixed type because the
 * same loader deliberately admits `Point`/`LineString`/`Polygon`/
 * `MultiPolygon` for them — hospitals and substations are nodes in OSM far
 * more often than footprints.
 */
export interface RoadFeature {
  type: 'Feature';
  properties: { name: string; status: string; highway?: string };
  geometry:
    | { type: 'LineString'; coordinates: number[][] }
    | { type: 'MultiLineString'; coordinates: number[][][] };
}

/**
 * One road as the list of polylines needed to draw it.
 *
 * A `MultiLineString` is several disjoint paths, and a `<Polyline>` draws
 * exactly one, so a road that arrives multi-part needs several elements.
 * Returning a list (rather than flattening, which would draw a spurious
 * segment between the parts) keeps the shape honest. Long roads that share
 * endpoints are common, so some overlap is expected and harmless.
 */
export function roadPaths(feature: RoadFeature): LatLng[][] {
  const raw =
    feature.geometry.type === 'LineString'
      ? [feature.geometry.coordinates]
      : feature.geometry.coordinates;
  return raw.map(toLatLngList).filter((path) => path.length >= 2);
}

export interface ExposureResponse extends CategoryHeader {
  final_land_area_km2: number;
  hospitals: InfraGroup;
  substations: InfraGroup;
  roads_cut_off: InfraGroup<RoadFeature>;
  definitions: Record<string, string>;
  is_estimate: boolean;
}

export interface Shelter {
  name: string;
  lon: number;
  lat: number;
  capacity_people: number;
  is_demo_data: boolean;
}

export interface RoutesResponse extends CategoryHeader {
  origin: Locality;
  shelter: Shelter;
  shelter_assignment_basis: string;
  shelter_status: Record<string, unknown>;
  capacity_basis: Record<string, unknown>;
  origin_lonlat: number[];
  destination_lonlat: number[];
  /** Empty when `reachable` is false — check the flag, not the array length. */
  coordinates: number[][];
  length_m: number;
  length_km: number;
  reachable: boolean;
  reason: string;
  definitions: Record<string, string>;
}

export interface AllocationResponse extends CategoryHeader {
  final_land_area_km2: number;
  localities_evaluated: number;
  allocation: {
    node: string;
    population: number;
    assignments: { shelter: string; people: number; distance_km: number }[];
  }[];
  shelter_loads: { shelter: string; capacity_people: number; assigned: number }[];
  unmet_demand: number;
  total_person_km: number;
  message: string;
  shelter_status: Record<string, unknown>;
  capacity_basis: Record<string, unknown>;
  population_method: Record<string, unknown>;
  is_estimate: boolean;
}

export type PriorityLevel = 'CRITICAL' | 'HIGH' | 'MEDIUM' | 'LOW';

export interface EvacuationPriority {
  /** Field name is `locality_name`, NOT `block_name` — see CLAUDE.md note. */
  locality_name: string;
  priority_level: PriorityLevel;
  reasoning: string;
}

export interface DistrictAdvisory {
  executive_summary: string;
  evacuation_plan: EvacuationPriority[];
  sms_dispatch_draft: string;
  post_landfall_risks: string;
  historical_context: string;
}

export interface AdvisoryResponse {
  advisory: DistrictAdvisory;
  generated_for: {
    category: number;
    imd_category: string;
    wind_kmph: number;
    origin: Locality;
    origin_context: string;
    origin_reachable: boolean;
    origin_reason: string;
    origin_in_allocation: boolean;
    origin_entry_added_in_code: boolean;
  };
  model: string;
  /** Always true on a 200. The backend withholds rather than return a 502. */
  validated: true;
  validation: {
    checks: string[];
    plan_coverage: string;
    /** Correction passes: 1, or 2 when the first draft broke a check. */
    attempts: number;
    /** HTTP calls made, including retried 503s. Explains a slow response. */
    gemini_calls: number;
  };
}

/**
 * A captured `/advisory` response, as bundled by
 * `backend/tools/capture_advisory.py`.
 *
 * This is real model output from a real call, stored — not a hand-written
 * example. `cached` is in the envelope rather than inferred by the client, so
 * a reader of the file can tell a capture from a live response without
 * knowing anything about the capture tool.
 */
export interface CachedAdvisory {
  /** ISO 8601 UTC. The one thing the app shows about provenance. */
  captured_at: string;
  cached: true;
  response: AdvisoryResponse;
}

// ---------------------------------------------------------------------------
// Display overlays
// ---------------------------------------------------------------------------

/**
 * The four numbers `<Overlay>` needs to place an image on the map.
 *
 * These are the **DEM's** bounds, not the flood's. The overlay is deliberately
 * larger than its content so the map can place it without knowing how far
 * the water reaches — cropping to the flood would also make the image change
 * size between intensities.
 */
export interface OverlayBounds {
  west: number;
  south: number;
  east: number;
  north: number;
}

export interface OverlayDepthClass {
  /** Upper bound in metres; null for the open-ended deepest class. */
  upper: number | null;
  alpha: number;
}

/**
 * One pre-rendered flood raster. **Display only** — see the field docs on
 * `final_land_area_km2` and the `disclosure` string, which every entry
 * carries and which the UI is expected to surface.
 */
export interface OverlayEntry {
  /** "cat0".."cat6", or "remal_observed" for the case-study anchor. */
  id: string;
  label: string;
  wind_kmph: number;
  surge_m: number;
  imd_category: string;
  /** Filename within the overlay directory. */
  image: string;
  /** Server-relative path, e.g. "/overlays/flood_cat6.png". */
  image_url: string;
  bounds: OverlayBounds;
  width_px: number;
  height_px: number;
  png_bytes: number;
  flooded_pixels: number;
  flooded_fraction_of_raster: number;
  /**
   * The **model's** figure for land flooded, at this exact wind. This is the
   * number to show. It is not derivable from the image — measure the picture
   * and you get something else, because the raster is downsampled to ~150 m
   * and hard-quantised into four depth classes.
   */
  final_land_area_km2: number;
  /** What the polygon path would actually draw; lower than the above. */
  drawn_area_km2: number;
  depth_classes_m: OverlayDepthClass[];
  is_display_raster: true;
  disclosure: string;
}

export interface OverlaysResponse {
  generated_by: string | null;
  target_width_px: number;
  surge_method: string;
  anchor: { wind_kmph: number; surge_m: number };
  limitation: string;
  is_display_raster: true;
  disclosure: string;
  count: number;
  overlays: OverlayEntry[];
}

/**
 * The flood rasters. `/surge-zone` exists and is unchanged, but the map does
 * not use it: at category 6 that payload is 532 polygons, 40,698 rings and
 * **180,038 vertices — 7.0 MB raw, 936 KB gzipped**. `react-native-maps`
 * stutters drawing that, and re-fetching it per slider step is a connection
 * problem before it is a rendering one. The equivalent PNG is 126,552 bytes
 * and is placed by the map engine as a texture sample, not parsed at all.
 *
 * Consequence worth keeping straight: this is a display shortcut, not a
 * change to the model. `/exposure`, `/routes` and `/allocation` still use the
 * full-resolution mask and are unaffected by these images.
 */
export function getOverlays(): Promise<OverlaysResponse> {
  return request<OverlaysResponse>('/overlays', { method: 'GET' }, READ_TIMEOUT_MS);
}

/**
 * Absolute URL for an overlay PNG, or null when `EXPO_PUBLIC_API_URL` is
 * unset.
 *
 * `image_url` comes back server-relative, and `<Overlay>` needs something it
 * can fetch — a relative path would resolve against the Metro bundler's own
 * origin and 404. Returns null rather than a broken string so the caller can
 * show the config error instead of a silently blank map.
 */
export function overlayImageUrl(entry: OverlayEntry): string | null {
  if (!API_BASE_URL) return null;
  return `${API_BASE_URL}${entry.image_url}`;
}

/**
 * Is there a flood layer worth drawing for this entry?
 *
 * False when there is no entry, and false when the entry reports zero
 * flooded pixels. The second case is not an error and not a loading state —
 * **categories 0 through 3 genuinely flood nothing** at the anchor surge, and
 * their overlays are real, valid, entirely transparent 1000x930 images. The
 * old screen rendered `<Overlay>` anyway, with `uri: ''`, which asked the map
 * to place a bounds tuple over a texture that does not exist for a layer with
 * no water in it.
 *
 * Fail-closed on the count, and the type check is load-bearing rather than
 * decoration: `undefined > 0` and `null > 0` are both false, but `'16052' > 0`
 * is **true** — JavaScript coerces the string. A backend that started
 * serialising the count as a string would therefore be drawn, which is the
 * wrong way round. An index that loses the field, or sends it as anything but
 * a number, stops being drawn rather than being drawn as a solid rectangle
 * over the delta.
 */
export function shouldDrawOverlay(entry: OverlayEntry | null | undefined): boolean {
  if (!entry) return false;
  return typeof entry.flooded_pixels === 'number' && entry.flooded_pixels > 0;
}

// ---------------------------------------------------------------------------
// Historical track (GET /track)
// ---------------------------------------------------------------------------

/**
 * One best-track fix. Mirrored off a live `GET /track` response.
 *
 * `wind_kt` / `wind_kmph` are **null when IBTrACS reported no wind**, which is
 * not the same as zero: `backend/data_pipeline/fetch_ibtracs.py` coerces a
 * blank `USA_WIND` to `0.0` when it writes the GeoJSON, so 5 of the 19
 * committed fixes carry a 0.0 that means "not reported". The endpoint
 * separates them and `wind_reported` says which is which. Render the absence
 * as absence — a "0 kmph" label on one of these is a fabricated measurement on
 * a map of a real cyclone.
 *
 * Both units ship because the source column is knots and the rest of this app
 * reasons in km/h. `timestamp` is RFC 3339 UTC (`...Z`), the endpoint's
 * `timezone` field says so, and the labels below never re-interpret it.
 */
export interface TrackWaypoint {
  sequence: number;
  timestamp: string;
  latitude: number;
  longitude: number;
  wind_kt: number | null;
  wind_kmph: number | null;
  wind_reported: boolean;
}

/** `GET /track`. Not keyed by category: the track is a record, not a prediction. */
export interface TrackResponse {
  name: string | null;
  season: string | null;
  /** e.g. "IBTrACS v04r00" — the provenance every rendered number inherits. */
  source: string | null;
  wind_units: string | null;
  timezone: string;
  path: LatLng[];
  waypoints: TrackWaypoint[];
  waypoint_count: number;
  first_timestamp: string;
  last_timestamp: string;
  unreported_wind_count: number;
  disclosure: string;
}

const MONTHS = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec'];

/**
 * Waypoint label: `25 May 2024 · 12:00 UTC`.
 *
 * Sliced out of the RFC 3339 string rather than formatted from a `Date`,
 * because `new Date(...)` + `toLocaleString()` renders in the *device's*
 * timezone — the same fix would then be labelled 17:30 on a phone in Kolkata,
 * and the app would be reporting a different time for the same real fix
 * depending on who was looking. No `Intl` either: Hermes' locale support is
 * patchy (`group()` in `ReadoutPanel` avoids it for the same reason).
 */
export function waypointTitle(waypoint: TrackWaypoint): string {
  const t = waypoint.timestamp;
  const month = MONTHS[Number(t.slice(5, 7)) - 1] ?? t.slice(5, 7);
  return `${Number(t.slice(8, 10))} ${month} ${t.slice(0, 4)} · ${t.slice(11, 16)} UTC`;
}

/** `100 kmph (54 kt)`, or an explicit "not reported" — never `0 kmph`. */
export function waypointSubtitle(waypoint: TrackWaypoint): string {
  if (!waypoint.wind_reported || waypoint.wind_kmph === null || waypoint.wind_kt === null) {
    return 'Wind not reported for this fix';
  }
  return `${waypoint.wind_kmph.toFixed(0)} kmph (${waypoint.wind_kt.toFixed(0)} kt)`;
}

/**
 * The IMD band whose representative wind is closest to `wind_kmph`.
 *
 * The seven bands are a classification scheme, not seven events, and no
 * band's midpoint is the wind the case study actually made landfall at — so
 * the Remal preset has to borrow a band for every API-driven figure
 * (exposure, advisory). This returns the nearest one; the caller is
 * responsible for saying so, because the borrowed band's numbers are not the
 * preset's numbers.
 *
 * Returns the `category` field, not the array index — they are equal for the
 * `/categories` response today, but only one of them is the value
 * `/exposure?category=` actually takes.
 */
export function nearestCategory(
  categories: { category: number; wind_kmph: number }[],
  windKmph: number,
): number {
  let best = categories.length ? categories[0].category : 0;
  let bestDelta = Number.POSITIVE_INFINITY;
  for (const c of categories) {
    const delta = Math.abs(c.wind_kmph - windKmph);
    if (delta < bestDelta) {
      bestDelta = delta;
      best = c.category;
    }
  }
  return best;
}

// ---------------------------------------------------------------------------
// Transport
// ---------------------------------------------------------------------------

/**
 * Every request goes through here.
 *
 * `AbortController` is the only timeout mechanism that interrupts a fetch
 * mid-flight. `Promise.race` against a timer would let the request keep
 * running in the background and still spend the user's Gemini quota after
 * the UI has given up.
 */
async function request<T>(
  path: string,
  init: RequestInit,
  timeoutMs: number,
): Promise<T> {
  if (!API_BASE_URL) {
    throw new ApiError({
      kind: 'config',
      status: 0,
      message:
        'EXPO_PUBLIC_API_URL is not set. Set it to the backend origin ' +
        '(e.g. http://192.168.1.20:8000 for a device on the same Wi-Fi) ' +
        'and restart the bundler.',
    });
  }

  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), timeoutMs);

  let response: Response;
  try {
    response = await fetch(`${API_BASE_URL}${path}`, {
      ...init,
      signal: controller.signal,
    });
  } catch (err) {
    // `fetch` rejects with an AbortError when OUR controller fires, and with
    // a TypeError when the request never reached the server. The distinction
    // matters: one is "the backend is slow", the other is "there is no
    // backend".
    const aborted = err instanceof Error && err.name === 'AbortError';
    throw new ApiError({
      kind: aborted ? 'timeout' : 'network',
      status: 0,
      message: aborted
        ? `No response from the backend within ${Math.round(timeoutMs / 1000)}s. ` +
          'It may still be working — do not retry immediately if this is the ' +
          'advisory, because that spends more quota.'
        : `Could not reach ${API_BASE_URL}. Check the device is on the same ` +
          'network as the backend and that the URL is right.',
    });
  } finally {
    clearTimeout(timer);
  }

  if (!response.ok) {
    throw await toApiError(response);
  }
  return (await response.json()) as T;
}

/**
 * Turn a non-2xx response into a typed ApiError, preserving what matters.
 *
 * Exported so the status -> kind mapping can be tested directly. It is the
 * one function in this file whose whole job is a lookup table, and a lookup
 * table that has never been exercised is exactly the kind that quietly
 * regresses — a 429 that fell through to `kind: 'http'` would still have
 * typechecked, still have rendered, and still told the user to retry a limit
 * that does not move.
 */
export async function toApiError(response: Response): Promise<ApiError> {
  const status = response.status;

  // `Retry-After` may be seconds or an HTTP date; we only ever emit seconds.
  const retryHeader = response.headers.get('Retry-After');
  const retryAfterSeconds = retryHeader ? Number(retryHeader) : null;
  const retry =
    retryAfterSeconds !== null && Number.isFinite(retryAfterSeconds)
      ? retryAfterSeconds
      : null;

  let detail = '';
  let violations: string[] = [];
  try {
    const body = await response.json();
    const raw = (body as { detail?: unknown }).detail;
    if (typeof raw === 'string') {
      detail = raw;
    } else if (raw && typeof raw === 'object') {
      const d = raw as { message?: unknown; violations?: unknown };
      detail = typeof d.message === 'string' ? d.message : JSON.stringify(raw);
      if (Array.isArray(d.violations)) {
        violations = d.violations.map(String);
      }
    } else if (raw !== undefined) {
      detail = String(raw);
    }
  } catch {
    detail = response.statusText || '(no body)';
  }

  // Checked before 503, and kept as its own branch, because a spent daily
  // quota is the exact opposite of a busy model: it does not clear in a
  // minute, and every attempt spends a little more of what is already gone.
  // The backend sends no `Retry-After` with a 429 precisely so a client
  // cannot mistake this for a transient failure and start polling.
  if (status === 429) {
    return new ApiError({
      kind: 'quota',
      status,
      message:
        "Today's Gemini free-tier request limit has been used up. Advisories " +
        'are blocked until the quota resets at midnight Pacific — retrying now ' +
        'will not help. Every other part of the app is unaffected.',
      detail,
      retryAfterSeconds: null,
    });
  }

  if (status === 503) {
    return new ApiError({
      kind: 'capacity',
      status,
      message:
        'The advisory model is at capacity right now. This is a Gemini-side ' +
        'queue, not a fault in the app — the same request usually works in a minute.',
      detail,
      retryAfterSeconds: retry,
    });
  }

  if (violations.length > 0) {
    return new ApiError({
      kind: 'validation',
      status,
      message: 'The generated advisory failed its honesty checks and was withheld.',
      detail,
      violations,
    });
  }

  return new ApiError({
    kind: status === 501 || status === 502 ? 'upstream' : 'http',
    status,
    message: detail || `Request failed with HTTP ${status}.`,
    detail,
    retryAfterSeconds: retry,
  });
}

// ---------------------------------------------------------------------------
// Typed endpoint functions
// ---------------------------------------------------------------------------

/** IMD wind bands 0-6, for populating the intensity control. */
export function getCategories(): Promise<CategoriesResponse> {
  return request<CategoriesResponse>('/categories', { method: 'GET' }, READ_TIMEOUT_MS);
}

export function getLocalities(): Promise<LocalitiesResponse> {
  return request<LocalitiesResponse>('/localities', { method: 'GET' }, READ_TIMEOUT_MS);
}

/**
 * Cyclone Remal's observed best track, from the committed IBTrACS extract.
 *
 * Not a slider endpoint: the track is a historical fact, so it is fetched once
 * at boot and never refetched when the intensity changes.
 */
export function getTrack(): Promise<TrackResponse> {
  return request<TrackResponse>('/track', { method: 'GET' }, READ_TIMEOUT_MS);
}

export function getSurgeZone(category: number): Promise<SurgeZoneResponse> {
  return request<SurgeZoneResponse>(
    `/surge-zone?category=${category}`,
    { method: 'GET' },
    READ_TIMEOUT_MS,
  );
}

export function getExposure(category: number): Promise<ExposureResponse> {
  return request<ExposureResponse>(
    `/exposure?category=${category}`,
    { method: 'GET' },
    READ_TIMEOUT_MS,
  );
}

/**
 * `origin` is a locality ID from /localities (e.g. "sagar"), never a name
 * and never a coordinate.
 */
export function getRoutes(category: number, origin: string): Promise<RoutesResponse> {
  const q = `category=${category}&origin=${encodeURIComponent(origin)}`;
  return request<RoutesResponse>(`/routes?${q}`, { method: 'GET' }, READ_TIMEOUT_MS);
}

export function getAllocation(
  category: number,
  origin: string,
): Promise<AllocationResponse> {
  const q = `category=${category}&origin=${encodeURIComponent(origin)}`;
  return request<AllocationResponse>(`/allocation?${q}`, { method: 'GET' }, READ_TIMEOUT_MS);
}

/**
 * The one call that spends Gemini quota, so it is POST and it is only ever
 * reached from an explicit user action. 120 s for the reason derived at the
 * top of this file.
 */
export function postAdvisory(
  category: number,
  origin: string,
): Promise<AdvisoryResponse> {
  const q = `category=${category}&origin=${encodeURIComponent(origin)}`;
  return request<AdvisoryResponse>(
    `/advisory?${q}`,
    { method: 'POST' },
    ADVISORY_TIMEOUT_MS,
  );
}

// ---------------------------------------------------------------------------
// GeoJSON normalisation
// ---------------------------------------------------------------------------

/**
 * Flatten a flood geometry to a list of polygons, each a list of rings, each
 * ring a list of [lon, lat] pairs — the shape react-native-maps `<Polygon>`
 * wants.
 *
 * The backend emits three different geometry types depending on the category
 * (see the `FloodGeometry` union). Rather than make the map screen handle
 * all three, it asks for polygons and gets polygons.
 *
 * **The map screen does not use this.** Stage 2 draws the flood from the
 * pre-rendered PNG in `data/overlays/` via `<Overlay>`, because the polygon
 * payload is 7.0 MB / 180k vertices at category 6. These normalisers and the
 * `decimate*` helpers below are kept because they are correct and because
 * `/surge-zone` is still the right answer for any non-display consumer — but
 * nothing on the slider path should call them.
 *
 * An empty array is meaningful rather than an error: it means nothing floods
 * at this intensity. Categories 0-3 return no geometry at all because their
 * surge is under the DEM's 1 m vertical resolution (each carries a `note`
 * saying so), and the Remal anchor at 1.2 m sits just above that floor.
 */
export function floodPolygons(geometry: FloodGeometry | null | undefined): number[][][][] {
  if (!geometry) return [];
  switch (geometry.type) {
    case 'Polygon':
      return [geometry.coordinates];
    case 'MultiPolygon':
      return geometry.coordinates;
    case 'GeometryCollection':
      return geometry.geometries.flatMap(floodPolygons);
    default:
      return [];
  }
}

/**
 * The peak-surge frame — the only frame that carries a polygon.
 *
 * The other nine frames are deliberately geometry-less (see
 * `FloodResult.to_feature_collection`) and exist to carry per-step areas.
 * Taking `features[features.length - 1]` rather than filtering on a flag is
 * what "final frame first" means here.
 */
export function finalFrame(response: SurgeZoneResponse): FloodFeature | null {
  const { features } = response.geojson;
  return features.length ? features[features.length - 1] : null;
}

/**
 * Drop vertices that sit closer together than `toleranceMetres` of their
 * predecessor, keeping ring endpoints so polygons stay closed.
 *
 * This exists because of a measurement, not a hunch. At category 6 the flood
 * payload is **112,655 vertices / 4.6 MB** across 308 polygons — far past
 * what a phone can draw interactively, and too much to move on every slider
 * step. The backend already simplifies to ~170 m (`SIMPLIFY_TOL_DEG` in
 * `backend/simulation/flood.py`); this is a second, coarser pass on top.
 *
 * It is a straight distance filter, not Douglas-Peucker: no dependency, and
 * it is honest about being cruder. The real fix is a larger server-side
 * tolerance so the 4.6 MB never crosses the wire at all — that is a backend
 * change and is NOT made here. See MEMORY.md "Flagged for review".
 */
export function decimateRing(
  ring: number[][],
  toleranceMetres: number,
): number[][] {
  if (ring.length < 3) return ring;
  // Degrees per metre at this latitude, via the cos(lat) factor. 0.02 deg is
  // only ever used as a conservative guard, so the constant's exact value
  // matters less than the fact that it is not latitude-dependent enough to
  // distort the delta.
  const out: number[][] = [ring[0]];
  let last = ring[0];
  for (let i = 1; i < ring.length - 1; i += 1) {
    const dx = (ring[i][0] - last[0]) * 111_320 * 0.86; // lon, cos(22.5 deg)
    const dy = (ring[i][1] - last[1]) * 110_574; // lat
    if (dx * dx + dy * dy >= toleranceMetres * toleranceMetres) {
      out.push(ring[i]);
      last = ring[i];
    }
  }
  out.push(ring[ring.length - 1]); // close the ring
  return out;
}

/** Decimate a whole polygon, keeping only rings that survive with 3+ points. */
export function decimatePolygon(
  polygon: number[][][],
  toleranceMetres: number,
): number[][][] {
  return polygon
    .map((ring) => decimateRing(ring, toleranceMetres))
    .filter((ring) => ring.length >= 3);
}
