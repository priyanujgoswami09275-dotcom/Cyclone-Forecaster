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
export const READ_TIMEOUT_MS = 45_000;

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

/** Provenance of the surge number — the model is an estimate and says so. */
export interface SurgeResult {
  wind_kmph: number;
  imd_category: string;
  surge_m: number;
  raw_prediction_m: number;
  clamped: boolean;
  /** Leave-one-out mean absolute error. The honest error bar on `surge_m`. */
  loo_mae_m: number;
  is_estimate: boolean;
  forward_speed_kmph: number;
  approach_angle_flag: number;
  forward_speed_assumed: boolean;
  approach_angle_assumed: boolean;
}

/**
 * The context block every simulation endpoint opens its response with. Kept
 * as a named interface (rather than inlined five times) so a change to
 * `backend/main.py::_category_header` has exactly one place to be mirrored.
 */
export interface CategoryHeader {
  category: number;
  imd_category: string;
  band_kmph: { lower: number; upper: number };
  wind_kmph: number;
  wind_is_band_midpoint: boolean;
  surge_m: number;
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

export interface CategoriesResponse {
  source: string;
  representative_wind: string;
  categories: {
    category: number;
    imd_category: string;
    band_kmph: { lower: number; upper: number };
    wind_kmph: number;
    wind_is_band_midpoint: boolean;
    surge_m: number;
    surge: SurgeResult;
    note?: string;
  }[];
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
 * One exposed asset. The backend emits `LineString` geometry for ALL THREE
 * asset classes — hospitals and substations included — because they come
 * from the same OSM extract. A marker therefore has to take
 * `coordinates[0]`; there is no `Point` geometry anywhere in this payload.
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
  geometry: { type: 'LineString'; coordinates: number[][] };
}

export interface InfraGroup {
  count: number;
  features: InfraFeature[];
}

export interface ExposureResponse extends CategoryHeader {
  final_land_area_km2: number;
  hospitals: InfraGroup;
  substations: InfraGroup;
  roads_cut_off: InfraGroup;
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

/** Turn a non-2xx response into a typed ApiError, preserving what matters. */
async function toApiError(response: Response): Promise<ApiError> {
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
 * Note the empty-collection cases are meaningful, not an error: categories
 * 0-5 currently return NO geometry at all, because the trained surge model
 * cannot resolve below ~115 kmph and those bands return 0 m of surge. An
 * empty array means "nothing floods at this intensity", and the map should
 * say so rather than treat it as a failure.
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
