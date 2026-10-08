/**
 * The Web screen's view model — every string, number and disclosure the judge
 * sees, derived from live API payloads.
 *
 * **Imports nothing but types**, for the same reason `mapProjection.ts` does:
 * `node --test` loads this file directly, and a value import of `theme` or
 * `api.ts` would be an extensionless specifier Node's ESM resolver refuses to
 * follow. Keeping the derivation here and the JSX in the component is what
 * makes it testable without a renderer — and the derivation is where the
 * honesty rules live, so it is the part that most needs testing.
 *
 * Nothing here invents a figure. Every number is read off a payload the
 * backend produced, and where a value is missing the result is `null` — which
 * the UI renders as `—`, never as `0`.
 */

import type {
  AdvisoryResponse,
  AllocationResponse,
  CategoriesResponse,
  ExposureResponse,
  LocalitiesResponse,
  OverlayEntry,
  RoutesResponse,
  TrackResponse,
} from './api';

/** One headline figure. `null` means "not arrived", which is not zero. */
export type Figure = number | null;

export interface ScenarioFigures {
  windKmph: Figure;
  surgeM: Figure;
  /** IMD band ceiling, or null for the open-ended top band. */
  windIsBandMidpoint: boolean;
  bandLabel: string;
  /** The model's own flooded land area. Never measured off the picture. */
  floodedKm2: Figure;
  drawnKm2: Figure;
  hospitals: Figure;
  substations: Figure;
  roads: Figure;
  /** IMD's note for bands whose surge is below the DEM's 1 m resolution. */
  bandNote: string | null;
}

/**
 * Headline figures for one scenario, from the overlay entry and the exposure
 * response.
 *
 * `windKmph` comes from the **exposure payload** rather than the overlay
 * index, because that is the response the counts came from and the two should
 * never disagree on screen. The overlay supplies `floodedKm2`, which is the
 * model's figure for this exact wind.
 *
 * `≥` is decided by `wind_is_band_midpoint`, never inferred: IMD documents
 * Super Cyclonic Storm as `>=222 kmph` with no ceiling, so 222 is that band's
 * *floor*, and printing it bare would state as a measurement the bottom of an
 * open-ended range — the same class of error as the knots bug this project
 * already fixed (MEMORY.md §31). `undefined` is treated as not-a-midpoint,
 * because a flag the payload did not send is not a claim it made.
 */
export function scenarioFigures(
  overlay: OverlayEntry | null,
  exposure: ExposureResponse | null,
  fallbackBand: string | null,
): ScenarioFigures {
  const bandLabel = exposure?.imd_category ?? fallbackBand ?? '';
  return {
    windKmph: exposure?.wind_kmph ?? overlay?.wind_kmph ?? null,
    surgeM: exposure?.surge_m ?? overlay?.surge_m ?? null,
    // Defaults to FALSE, not true. An absent flag means the payload did not
    // assert the wind is a midpoint, and the honest rendering of a claim
    // nobody made is the cautious one — which is what `figuresLine` in
    // `strengthChips.ts` already does with the same `undefined` (`isBandMidpoint
    // ? '' : '≥'`). Defaulting to true here would make the headline say "222
    // km/h" while the figures line directly below said "≥222 km/h wind", which
    // is the same screen contradicting itself.
    windIsBandMidpoint: exposure?.wind_is_band_midpoint ?? false,
    bandLabel,
    floodedKm2: exposure?.final_land_area_km2 ?? overlay?.final_land_area_km2 ?? null,
    drawnKm2: exposure?.roads_cut_off ? overlay?.drawn_area_km2 ?? null : null,
    // Read through optional chaining at *two* levels. `exposure.hospitals.count`
    // throws on a partially-shaped response, and a Web screen that white-screens
    // on a malformed payload is worse than one that shows `—`. Caught by
    // `tests/webViewModel.test.mjs` passing a header-only object.
    hospitals: exposure?.hospitals?.count ?? null,
    substations: exposure?.substations?.count ?? null,
    roads: exposure?.roads_cut_off?.count ?? null,
    bandNote: (exposure as unknown as { note?: string } | null)?.note ?? null,
  };
}

/** `222 km/h` or `≥222 km/h`. */
export function windLabel(windKmph: Figure, isBandMidpoint: boolean): string {
  if (windKmph === null) return '—';
  return `${isBandMidpoint ? '' : '≥'}${Math.round(windKmph)} km/h`;
}

/** `4.47 m` — two decimals, because a surge quoted to 3 looks like false precision. */
export function surgeLabel(surgeM: Figure): string {
  return surgeM === null ? '—' : `${surgeM.toFixed(2)} m`;
}

/** `2,680 km²`, grouped so a judge reads the magnitude at a glance. */
export function areaLabel(km2: Figure): string {
  if (km2 === null) return '—';
  if (km2 < 1) return `${km2.toFixed(2)} km²`;
  return `${Math.round(km2).toLocaleString('en-US')} km²`;
}

/**
 * The count for one exposure tile.
 *
 * `—` for null, and that distinction is load-bearing: an unresolved count is
 * not a zero exposure, and a tile reading `0` tells the reader "the model
 * found nothing there", which is a claim. `—` tells them "nothing has arrived
 * yet". Collapsing the two is how a dashboard ends up claiming a storm
 * exposed no hospitals while it is still loading.
 */
export function countLabel(count: Figure): string {
  return count === null ? '—' : count.toLocaleString('en-US');
}

/** The word under a count. Suppressed entirely when there is nothing there. */
export function countUnit(count: Figure, unit: string): string | null {
  return count === null || count === 0 ? null : unit;
}

/**
 * The road tile's wording, which must not say "impassable".
 *
 * The backend's own `definitions.road_cut_off` says: "road geometry
 * intersects the flood polygon; this is NOT a network connectivity analysis
 * and does not imply the road is impassable." That is the project's honest
 * position and this string carries it in the one place a judge reads it. The
 * native app says the same thing as "cut off by water"; "intersected" is
 * blunter and more accurate, since the computation is an intersection test.
 */
export const ROADS_HONEST_LABEL = 'intersected by the flood extent';

/** Why the road count is not a passability claim. Shown as a tooltip title. */
export const ROADS_DISCLOSURE =
  'A road is counted when its geometry intersects the flood extent. This is ' +
  'not a network-connectivity analysis and does not mean the road is ' +
  'impassable.';

/**
 * Plain-language versions of the two backend disclosures that are the wordiest.
 *
 * **`/track` and `/localities` return disclosure strings written for a
 * developer, and the Web build was rendering them verbatim in its main
 * panel.** They name a source file (`data/remal_track.geojson`), an internal
 * project rule (`Rules.md`), and wire-format fields (`wind_kt: null`,
 * `wind_reported: false`). All three are true and none of them belongs in the
 * first thing a judge reads.
 *
 * **The backend strings are NOT changed** — they stay exactly as they are on
 * the wire, and the full original text is still shown, verbatim, behind
 * "Show data provenance". What changes is that the main panel reads the plain
 * sentence and the provenance panel shows the whole thing. Nothing is removed
 * from the app; it is moved to where it belongs.
 *
 * The claims are preserved one-for-one. "record of what happened, not a
 * forecast"; positions are 3-hourly; a fix with no reported wind is *not* calm;
 * the study area is a scoping decision and not the district boundary.
 */

/** Plain-language track disclosure for the main panel. */
export const TRACK_DISCLOSURE_PLAIN =
  'The storm path is the observed track of Cyclone Remal, as recorded in ' +
  'IBTrACS. It is a record of what happened — not a forecast, and not this ' +
  "app's prediction for any other storm. Positions are 3-hourly. Where a " +
  'fix reported no wind, the app says so rather than showing a calm value.';

/** Plain-language study-area disclosure for the main panel. */
export const SCOPING_DISCLOSURE_PLAIN =
  'The study area covers South and North 24 Parganas and Sagar Island. It is ' +
  'a scoping decision made to keep the numbers legible — it is an ' +
  'approximation, not the district boundary, which is not in this dataset. ' +
  'Places outside the study area are listed by name in the data provenance ' +
  'below.';

/**
 * The unreported-wind sentence, assembled from the payload rather than stated.
 *
 * Counted from `waypoints` so it tracks the data, which is the same reason
 * `AboutSheet` takes it as a prop rather than writing it in a comment.
 */
export function unreportedWindSentence(track: {
  waypoint_count: number;
  waypoints: ReadonlyArray<{ wind_reported: boolean }>;
}): string {
  const missing = track.waypoints.filter((w) => !w.wind_reported).length;
  if (missing === 0) return '';
  return ` ${missing} of the ${track.waypoint_count} fixes report no wind at all, and those are shown as unreported rather than as calm.`;
}

/** The headline sentence under the scenario control. */
export function scenarioHeadline(figures: ScenarioFigures): string {
  if (figures.windKmph === null) return 'Reading the live model…';
  const band = figures.bandLabel || 'this band';
  return `${windLabel(figures.windKmph, figures.windIsBandMidpoint)} · ${band}`;
}

export interface ShelterDisclosure {
  isDemo: boolean;
  heading: string;
  body: string;
}

/**
 * The shelter disclosure, read from `/allocation`'s `shelter_status`.
 *
 * **Fails closed**: a null or malformed status discloses, because the failure
 * mode of dropping this notice is a reader sending people to buildings that
 * were never verified to exist. `is_demo_data === false` is the only value that
 * suppresses it.
 *
 * The count comes from the payload when present, so the notice can say how
 * many places rather than gesturing at "some".
 */
export function shelterDisclosure(
  status: Record<string, unknown> | null | undefined,
  shelterCount: number | null,
): ShelterDisclosure {
  if (status !== null && status !== undefined && status['is_demo_data'] === false) {
    return {
      isDemo: false,
      heading: 'Shelter data verified',
      body: 'Shelter locations and capacities in this plan come from a surveyed dataset.',
    };
  }
  const places =
    shelterCount === null
      ? 'The shelters named below'
      : `The ${shelterCount} shelters named below`;
  return {
    isDemo: true,
    heading: 'Shelter data is not verified',
    body:
      `${places} are placeholders used to demonstrate the allocation algorithm. ` +
      'No verified shelter dataset exists for this district, and capacities are ' +
      'derived as 1.1x the estimated exposed population rather than surveyed. ' +
      'Do not use this to direct a real evacuation.',
  };
}

export interface PopulationDisclosure {
  isEstimate: boolean;
  text: string;
}

/**
 * The population disclosure, read from `/allocation`'s `population_method`.
 *
 * The `disclosure` string is taken from the payload **verbatim** rather than
 * paraphrased, for the same reason `AboutSheet` does it: a paraphrase is
 * somewhere for the two to drift, and this sentence is the model's own
 * description of its own arithmetic.
 */
export function populationDisclosure(
  allocation: AllocationResponse | null,
): PopulationDisclosure {
  const method = allocation?.population_method as
    | { is_estimate?: unknown; disclosure?: unknown }
    | undefined;
  if (allocation !== null && method?.disclosure === undefined) {
    // No disclosure string at all. Say the short honest version rather than
    // silently implying the numbers are surveyed.
    return {
      isEstimate: true,
      text: 'Population figures are estimates derived from OSM building density, not census data.',
    };
  }
  const text = method?.disclosure;
  return {
    isEstimate: method?.is_estimate !== false,
    text: typeof text === 'string' ? text : 'Population figures are estimates, not census data.',
  };
}

/**
 * The case-study framing line: what this is anchored to, and what it is not.
 *
 * Built from `/categories`' own `anchor` and `limitation` rather than a
 * hardcoded sentence, so if the anchor is ever re-pointed the screen follows.
 */
export function caseStudyLine(categories: CategoriesResponse | null): {
  anchor: string;
  limitation: string;
} {
  const anchor = categories?.anchor;
  const wind = anchor?.wind_kmph ?? 115;
  const surge = anchor?.surge_m ?? 1.2;
  return {
    anchor:
      // **Khepupara is in Bangladesh, not West Bengal.** The wording follows
      // `backend/main.py`'s `REMAL_PRESET.source`, which has always said
      // "Sagar Island (West Bengal) and Khepupara (Bangladesh)". The masthead
      // subtitle used to render this as "Sagar Island and Khepupara, West
      // Bengal", which put the Bay of Bengal landfall inside India.
      `Cyclone Remal, May 2024: landfall between Sagar Island (West Bengal) ` +
      `and Khepupara (Bangladesh), ${Math.round(wind)} km/h with ` +
      `${surge.toFixed(1)} m of surge above astronomical tide.`,
    limitation:
      categories?.limitation ??
      'Screening estimate scaled from one observed event; omits tide, pressure, bathymetry and storm size.',
  };
}

export interface RouteSummary {
  reachable: boolean;
  headline: string;
  detail: string;
  lengthKm: Figure;
  shelterName: string | null;
  /** Person-km the allocation LP minimised, when `/allocation` is loaded. */
  totalPersonKm: Figure;
  unmetDemand: Figure;
  evaluatedLocalities: Figure;
}

/**
 * The routing and allocation summary for the chosen origin.
 *
 * **Unreachable is a result, not an error.** `/routes` returns 200 with
 * `reachable: false` and a reason, and the reason distinguishes two very
 * different failures: flood severance (the road is cut) and road-data
 * coverage (the committed OSM extract has no path there at all). Collapsing
 * them into "no route" would blame a flood that did not happen, which is the
 * dangerous direction to be wrong in — "cut off" reads as a warning. The
 * backend's own `reason` string is therefore shown rather than replaced.
 *
 * **`failed` is separate from `null`, and that separation is the point.** A
 * missing `routes` used to render as "Checking the road network…", so a
 * request that errored or was aborted was indistinguishable from one still in
 * flight — the panel claimed to be working forever while nothing was pending.
 * Caught in the browser: a `/routes` fetch that aborted left the screen saying
 * it was still checking, with no figure and no error. An error has to read as
 * an error.
 */
export function routeSummary(
  routes: RoutesResponse | null,
  allocation: AllocationResponse | null,
  failed = false,
): RouteSummary {
  const totalPersonKm =
    allocation !== null && Number.isFinite(allocation.total_person_km)
      ? allocation.total_person_km
      : null;
  const unmet =
    allocation !== null && Number.isFinite(allocation.unmet_demand)
      ? allocation.unmet_demand
      : null;
  const evaluated =
    allocation !== null && Number.isFinite(allocation.localities_evaluated)
      ? allocation.localities_evaluated
      : null;

  if (routes === null) {
    return {
      reachable: false,
      headline: failed
        ? 'Route lookup failed'
        : 'Checking the road network…',
      detail: failed
        ? 'The road-network request did not complete, so no route can be ' +
          'shown. The exposure figures above are unaffected — they come from a ' +
          'different endpoint.'
        : '',
      lengthKm: null,
      shelterName: null,
      totalPersonKm,
      unmetDemand: unmet,
      evaluatedLocalities: evaluated,
    };
  }

  if (routes.reachable) {
    const shelter = routes.shelter?.name ?? 'the assigned shelter';
    return {
      reachable: true,
      headline:
        `Flood-free route to ${shelter} · ` +
        `${routes.length_km.toFixed(1)} km`,
      detail: routes.shelter_assignment_basis ?? '',
      lengthKm: routes.length_km,
      shelterName: routes.shelter?.name ?? null,
      totalPersonKm,
      unmetDemand: unmet,
      evaluatedLocalities: evaluated,
    };
  }

  return {
    reachable: false,
    headline: 'No flood-free route from this origin',
    // The backend's reason, verbatim: it distinguishes road-data coverage from
    // flood severance and that distinction is the whole point of the endpoint.
    detail: routes.reason,
    lengthKm: null,
    shelterName: routes.shelter?.name ?? null,
    totalPersonKm,
    unmetDemand: unmet,
    evaluatedLocalities: evaluated,
  };
}

/** `17.4M person-km` — the LP's objective, grouped because it is large. */
export function personKmLabel(value: Figure): string {
  if (value === null) return '—';
  if (value >= 1e6) return `${(value / 1e6).toFixed(1)}M person-km`;
  if (value >= 1e3) return `${(value / 1e3).toFixed(1)}k person-km`;
  return `${Math.round(value)} person-km`;
}

/**
 * Whether "Generate advisory" should be enabled, and why not when it is not.
 *
 * Reuses `advisoryEnabled`'s rule — exposure, not a second opinion — so the
 * Web and native screens cannot disagree about when the button is live. The
 * `loading` guard matters here for the same reason it does natively: pressing
 * mid-fetch would generate an advisory for the *previous* chip's numbers.
 */
export function advisoryAvailability(
  exposedCount: number,
  loadingExposure: boolean,
  loadingAdvisory: boolean,
): { enabled: boolean; reason: string | null } {
  if (loadingAdvisory) {
    return { enabled: false, reason: 'Generating an advisory…' };
  }
  if (loadingExposure) {
    return { enabled: false, reason: 'Reading the exposure figures for this strength…' };
  }
  if (exposedCount === 0) {
    return {
      enabled: false,
      reason:
        'Nothing is exposed at this strength, so there is no evacuation to ' +
        'plan. Pick a stronger scenario to see the advisory path.',
    };
  }
  return { enabled: true, reason: null };
}

/**
 * Is the advisory on screen now describing a scenario the screen has moved off?
 *
 * Compared against the **server's** echo in `generated_for`, the same
 * comparison the native app makes — the server's copy is what the prose was
 * actually written for.
 */
export function advisoryStaleNote(
  response: AdvisoryResponse,
  category: number,
  originId: string,
  cycloneId?: string,
  scenarioId?: string,
): string | null {
  const generated = response.generated_for;
  // Missing-field rule: an axis the stored advisory does not record (a response
  // from before cyclone_id / scenario_id existed) cannot be a mismatch. Only a
  // field present on *both* sides and *differing* counts as stale.
  const cycloneMatches =
    cycloneId === undefined ||
    generated.cyclone_id === undefined ||
    generated.cyclone_id === cycloneId;
  const scenarioMatches =
    scenarioId === undefined ||
    generated.scenario_id === undefined ||
    generated.scenario_id === scenarioId;
  const unchanged =
    generated.category === category &&
    generated.origin.id === originId &&
    cycloneMatches &&
    scenarioMatches;
  if (unchanged) return null;
  return (
    `Generated for ${generated.imd_category} at ${generated.origin.name}. ` +
    'The scenario or origin has changed since — close this and generate again.'
  );
}

/**
 * Localities for the picker, sorted the way a reader is looking for one.
 *
 * **All 45 are offered.** The previous Web build took the first ten of
 * whatever order the API returned, which is not a subset anyone chose and hid
 * Sagar Island — the case study's actual landfall — in some orderings. The
 * sort puts the study area's larger places first by `radius_km` (the backend's
 * own search radius, so it is ordering by the field that decides whether the
 * place is findable), then alphabetical, so the list is stable.
 */
export function searchableLocalities(
  response: LocalitiesResponse | null,
  query: string,
  limit = 60,
): LocalitiesResponse['localities'] {
  const all = response?.localities ?? [];
  const needle = query.trim().toLowerCase();

  const matched = needle
    ? all.filter(
        (locality) =>
          locality.name.toLowerCase().includes(needle) ||
          locality.id.toLowerCase().includes(needle) ||
          locality.place.toLowerCase().includes(needle),
      )
    : all;

  return [...matched]
    .sort((a, b) => {
      if (b.radius_km !== a.radius_km) return b.radius_km - a.radius_km;
      return a.name.localeCompare(b.name);
    })
    .slice(0, limit);
}

/** How many localities match, for the "showing N of 45" line. */
export function localityMatchCount(
  response: LocalitiesResponse | null,
  query: string,
): { matched: number; total: number } {
  const all = response?.localities ?? [];
  const needle = query.trim().toLowerCase();
  if (!needle) return { matched: all.length, total: all.length };
  return {
    matched: all.filter(
      (locality) =>
        locality.name.toLowerCase().includes(needle) ||
        locality.id.toLowerCase().includes(needle) ||
        locality.place.toLowerCase().includes(needle),
    ).length,
    total: all.length,
  };
}

/**
 * The track caption: what this line is, and what it is not.
 *
 * "A record of what happened — not a forecast" is the project's own framing
 * and the single most important thing to say about a cyclone track on a
 * screen full of forward-looking numbers.
 */
export function trackCaption(track: TrackResponse | null): string {
  if (track === null) return '';
  const count = track.waypoint_count ?? track.path.length;
  return (
    `${track.name ?? 'Remal'} ${track.season ?? ''} · ${count} best-track fixes · ` +
    `${track.source ?? 'IBTrACS'} · a record of what happened, not a forecast`
  ).trim();
}

/**
 * One waypoint's label. `null` wind renders as an explicit absence.
 *
 * Never `0 kmph`. The IBTrACS source writes a blank `USA_WIND` as `0.0`, and
 * 5 of the 19 committed fixes carry that. A "0 kmph" label would be a
 * fabricated measurement on a map of a real cyclone.
 */
export function waypointLabel(waypoint: {
  timestamp: string;
  wind_kmph: number | null;
  wind_reported: boolean;
}): { title: string; wind: string } {
  const t = waypoint.timestamp;
  const months = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec'];
  const month = months[Number(t.slice(5, 7)) - 1] ?? t.slice(5, 7);
  const title = `${Number(t.slice(8, 10))} ${month} ${t.slice(0, 4)} · ${t.slice(11, 16)} UTC`;
  const wind =
    !waypoint.wind_reported || waypoint.wind_kmph === null
      ? 'Wind not reported for this fix'
      : `${waypoint.wind_kmph.toFixed(0)} km/h reported`;
  return { title, wind };
}

// --- responsive layout -----------------------------------------------------

/**
 * The viewport width at which the Web screen switches between its two layouts.
 *
 * **Below** it the screen stacks: masthead, then the map, then the four steps,
 * then "What this is and is not" — one column, so a phone never has to fit a
 * 340 px-minimum control column beside a map. **At and above** it the map and
 * the control column sit side by side, as they always have on desktop.
 *
 * 860 rather than the control column's 340 plus the map's minimum: the
 * breakpoint is the width at which the *stacked* layout stops being the better
 * one, not the width at which the two-column one becomes impossible. The
 * two-column layout technically survives down to ~600 px, but between there
 * and 860 the map column is too narrow for a 1.16-aspect drawing to stay
 * legible while the control column keeps its 340 px — so the stack wins there.
 *
 * The value is exported so tests can pin it rather than re-deriving it, and so
 * `MapScreen.web.tsx` never hardcodes the number twice.
 */
export const COMPACT_BREAKPOINT_PX = 860;

/** The stacked layout's floor for the map panel, in px. Brief: `min 320`. */
export const COMPACT_MAP_MIN_HEIGHT_PX = 320;

/** The stacked map is at most this fraction of the viewport. Brief: `~55vh`. */
export const COMPACT_MAP_VIEWPORT_FRACTION = 0.55;

/**
 * Which layout the viewport gets: single column below the breakpoint, two
 * columns at and above it.
 *
 * Pure, because the breakpoint is a decision the tests must be able to check
 * without a renderer — the layout itself is applied in `MapScreen.web.tsx`.
 */
export function isCompactViewport(viewportWidthPx: number): boolean {
  return viewportWidthPx < COMPACT_BREAKPOINT_PX;
}

/**
 * The map panel's height, matched to the drawing rather than letterboxing it.
 *
 * **Why this function exists: the black strip.** The SVG draws a viewBox whose
 * aspect is the bbox's ground aspect (~1.164 for the study region), and the
 * `<svg>` element fills its panel with `preserveAspectRatio="xMidYMid meet"`.
 * `meet` fits the whole drawing inside the panel, so whenever the panel's own
 * aspect is wider or taller than the frame's, the leftover panel shows the
 * panel background — near-black `#090909`, reading as an empty strip beside
 * the pale basemap. That is the defect in the deployed screenshots: not a
 * basemap that fails to load, but a panel sized independently of the drawing
 * it contains.
 *
 * Sizing the panel to `width / frameAspect` makes `meet` a no-op: the drawing
 * fills the panel exactly, the way `viewBoxFor` already makes the frame match
 * the bbox one level down. The clamps keep the panel useful when the aspect
 * and the viewport disagree:
 *
 * - **floor** 320 px in both layouts — a very wide frame in a narrow panel
 *   must not collapse the map to a ribbon. The wide layout once kept a
 *   separate 460 px floor inherited from the fixed-height panel; it only
 *   ever bound between 860 and ~990 px, where the map column is too narrow
 *   for a 460 px panel to be filled by a ~1.16-aspect drawing — measured at
 *   860x800: a 446 px panel, a 383 px drawing, 77 px of dead panel — so it
 *   is gone and the wide layout is aspect-fit all the way down to the same
 *   320 px floor;
 * - **ceiling** `COMPACT_MAP_VIEWPORT_FRACTION` of the viewport height in the
 *   stacked layout only — a 720 px-wide phone map is most of the viewport, so
 *   the steps below it start off-screen. The wide layout has no ceiling: the
 *   page scrolls, and a taller map is the fix there, not a defect.
 *
 * Degenerate inputs fall back to the floor rather than NaN — a non-finite
 * height is silently dropped by the style system and would leave the panel at
 * the flex default, which is exactly the unlabelled state this replaces.
 */
export function mapPanelHeightPx(input: {
  /** The panel's measured content width, from `onLayout`. */
  panelWidthPx: number;
  /** The SVG viewBox's width / height, from `viewBoxFor`. */
  frameAspect: number;
  /** The viewport height, from `useWindowDimensions`. */
  viewportHeightPx: number;
  /** Which layout is active — decides the ceiling. */
  compact: boolean;
}): number {
  const { panelWidthPx, frameAspect, viewportHeightPx, compact } = input;
  const floor = COMPACT_MAP_MIN_HEIGHT_PX;
  const ceiling = compact
    ? Math.max(floor, Math.round(viewportHeightPx * COMPACT_MAP_VIEWPORT_FRACTION))
    : Number.POSITIVE_INFINITY;

  const measurable =
    Number.isFinite(panelWidthPx) && Number.isFinite(frameAspect) && frameAspect > 0;
  const ideal = measurable ? panelWidthPx / frameAspect : floor;

  if (!Number.isFinite(ideal)) return floor;
  return Math.round(Math.min(Math.max(ideal, floor), ceiling));
}

// --- the map's floating chrome vs the disclosure strip ----------------------

/**
 * The disclosure strip's height as the layout assumes it **before the first
 * `onLayout` measurement arrives** — the value the old code hard-coded for
 * every width.
 */
export const DISCLOSURE_STRIP_FALLBACK_PX = 44;

/** The gap kept between the strip's top edge and the floating map chrome. */
export const MAP_CHROME_GAP_PX = 8;

/**
 * How far from the panel's bottom the legend and the S/T/L buttons must sit.
 *
 * **Why this function exists: the strip's height is not a constant.** The
 * basemap disclosure is body text at 10 px over the full panel width, so it
 * wraps — measured in the browser: **84 px at a 360 px viewport**, 70 px at
 * 390, 56 px at 480/600/860, 42 px from ~700 px up. The layout positioned the
 * legend and the controls above a hard-coded 44 px, so at every width where
 * the strip wrapped taller than that, the legend's last rows ("Storm path",
 * "Your origin") and the map buttons rendered **behind** the strip's
 * 90%-opaque background.
 *
 * The rule is one sentence: clear the *measured* strip by the chrome gap, and
 * fall back to the old constant only until the first measurement arrives. A
 * degenerate measurement (NaN, negative, infinite) is treated as unmeasured
 * rather than as zero — a zero-height strip is a layout bug, not a
 * strip-less panel.
 */
export function overlayClearancePx(stripHeightPx: number | null): number {
  const measured =
    stripHeightPx !== null && Number.isFinite(stripHeightPx) && stripHeightPx >= 0
      ? stripHeightPx
      : DISCLOSURE_STRIP_FALLBACK_PX;
  return measured + MAP_CHROME_GAP_PX;
}
