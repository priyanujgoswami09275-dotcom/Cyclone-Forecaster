/**
 * The cyclone model: the types the Dynamic Cyclone System speaks, and the pure
 * functions that turn them into strings a judge reads.
 *
 * **Why this module exists separately from `api.ts`.** `api.ts` owns the wire:
 * timeouts, the error taxonomy, and the GeoJSON normalisers. This module owns
 * the *domain* — what a cyclone is, what a scenario is, what the live feed
 * said — and the labels derived from them. The split is what lets the labels
 * be tested without a network and without a screen, which is where a
 * mislabelled live state has to be caught: it renders fine either way.
 *
 * **Every type here is mirrored from a running response, not from a doc.** The
 * field lists were read off `backend/main.py` and `backend/cyclones/base.py`.
 * A type that drifts from the wire is worse than no type, because it compiles
 * and then renders `undefined`.
 *
 * **The rule this module exists to enforce** (stated once, on `liveStateLabel`
 * where it belongs): an unavailable live feed must never read as an active
 * storm. A judge who reads "Live feed unavailable" should conclude that
 * nothing is being claimed — not that something is.
 */

// ---------------------------------------------------------------------------
// Historical cyclones — GET /cyclones
// ---------------------------------------------------------------------------

/** One entry in the catalogue. `is_case_study` marks Remal. */
export interface Cyclone {
  cyclone_id: string;
  name: string;
  season: number;
  basin: string;
  subbasin: string | null;
  peak_wind_kmph: number;
  waypoint_count: number;
  is_case_study: boolean;
}

export interface CyclonesResponse {
  source: string;
  generated_at: string;
  count: number;
  default_cyclone_id: string;
  limitation: string;
  cyclones: Cyclone[];
}

// ---------------------------------------------------------------------------
// Scenarios — GET /scenarios
// ---------------------------------------------------------------------------

/**
 * One strength a client may ask for.
 *
 * `kind` distinguishes the two kinds of scenario, which are not interchangeable:
 * a band scenario is a fixed IMD midpoint shared by every storm, while
 * `observed` is that cyclone's own peak and is a different number for each one.
 * A client comparing two cyclones needs to know which it is holding.
 */
export interface ScenarioSummary {
  scenario_id: string;
  label: string;
  wind_kmph: number;
  imd_category: string;
  kind: string;
  wind_is_band_midpoint: boolean;
  limitation: string;
}

export interface ScenariosResponse {
  cyclone_id: string;
  cyclone_name: string;
  generated_at: string;
  scenarios: ScenarioSummary[];
  limitation: string;
}

// ---------------------------------------------------------------------------
// Live state — GET /live-cyclone
// ---------------------------------------------------------------------------

/**
 * The storm the live feed reported, or `null`.
 *
 * `null` on every non-`available` state, and that is the whole point: a feed
 * that answered with nothing, or did not answer at all, must not have a
 * historical cyclone placed in this field by a caller who found one
 * convenient. Substituting Remal for a live storm would put a real historical
 * cyclone on a screen labelled as now.
 */
export interface LiveCycloneSummary {
  cyclone_id: string;
  name: string;
  season: string;
  basin: string;
  waypoint_count: number;
  first_timestamp: string;
  last_timestamp: string;
  latest_latitude: number;
  latest_longitude: number;
  latest_wind_kmph: number;
  latest_wind_reported: boolean;
  data_through: string;
  limitation: string;
}

/**
 * The three states, closed.
 *
 * They mean different things and must not be collapsed:
 *   - `available` — a live fix was parsed.
 *   - `no_active_storm` — the source answered and there is nothing active. The
 *     feed works; there is simply nothing to show. This is the normal state of
 *     the app for most of the year.
 *   - `live_unavailable` — no trustworthy answer arrived. Nothing is shown in
 *     its place.
 */
export type LiveStatus = 'available' | 'live_unavailable' | 'no_active_storm';

export interface LiveCycloneState {
  status: LiveStatus;
  source: string;
  /** `null` when the probe never received a status code — a timeout has none. */
  http_status: number | null;
  /**
   * Always carries the promise that nothing is being substituted, then the
   * diagnostic. A diagnostic alone ("tried 4 sources: 403, 403, 404, 200")
   * tells a reader nothing about what is *not* being shown.
   */
  reason: string;
  /** When the probe ran, UTC. Present even on failure: a stale failure
   *  reported as current is its own kind of lie. */
  checked_at: string;
  endpoints: string[];
  cyclone: LiveCycloneSummary | null;
  generated_at: string;
  limitation: string;
}

// ---------------------------------------------------------------------------
// Comparison — GET /comparison
// ---------------------------------------------------------------------------

/**
 * One cyclone at one scenario, reduced to the figures a comparison needs.
 *
 * Every number here was computed for this exact `(cyclone_id, scenario_id)`
 * pair — none is carried across from another cyclone or scenario, which is the
 * whole reason the comparison exists.
 */
export interface ComparisonEntry {
  cyclone_id: string;
  name: string | null;
  season: number | null;
  scenario_id: string;
  scenario_kind: string;
  wind_kmph: number;
  imd_category: string;
  /** From the deterministic law, never from the scenario. */
  surge_m: number;
  flood_area_km2: number;
  hospitals_exposed: number;
  substations_exposed: number;
  roads_cut_off: number;
  unreported_wind_fixes: number | null;
}

/**
 * The deltas between two entries.
 *
 * **Sign convention is the opposite of the server's, deliberately.** The
 * backend reports `left - right`; this reports `right - left`, because the
 * label is an arrow (`cat4 → cat5`) and a reader expects the number after the
 * arrow to be the change *along* it. Going from cat4 to cat5 the surge rises,
 * so the delta is positive. A client that mixed the two conventions would
 * print a falling surge for a strengthening storm.
 */
export interface ComparisonDeltas {
  between: string[];
  note: string;
  surge_m_delta?: number;
  flood_area_km2_delta?: number;
  hospitals_exposed_delta?: number;
  substations_exposed_delta?: number;
  roads_cut_off_delta?: number;
}

export interface ComparisonResponse {
  generated_at: string;
  scenario_id: string;
  cyclones: ComparisonEntry[];
  deltas: ComparisonDeltas;
  limitation: string;
}

// ---------------------------------------------------------------------------
// Storm peak intensity — the ML figure, and the gate it failed
// ---------------------------------------------------------------------------

/**
 * The ML layer's output, as `/risk-analyst` returns it.
 *
 * `estimate_kt` is what the app shows; `estimate_source` says what it is. When
 * the gate failed — which is the real outcome — `estimate_kt` *is* the flat
 * median and `model_kt_unused` is the losing model's output, preserved as
 * evidence and never presented as the answer.
 */
export interface PeakEstimate {
  estimate_kt: number;
  estimate_source: string;
  is_a_prediction: boolean;
  model_kt_unused: number | null;
  baseline_kt: number;
  interval_kt: [number | null, number | null];
  n_training: number;
  beats_baseline: boolean;
  unit: string;
  limitation: string;
}

// ---------------------------------------------------------------------------
// The AI Risk Analyst — POST /risk-analyst
// ---------------------------------------------------------------------------

/**
 * The request body. `category` is required; the rest scope the analysis.
 *
 * `origin` is a locality ID from `/localities`, never a name and never a
 * coordinate — the same rule `postAdvisory` follows.
 */
export interface RiskAnalystRequest {
  category: number;
  cyclone_id?: string;
  scenario_id?: string;
  origin?: string;
}

/**
 * What kind of evidence a finding rests on.
 *
 * `general_knowledge` is in the list on purpose. A model that has to admit
 * "this part is general knowledge" cannot pass a plausible-sounding hand-wave
 * off as a figure this service computed. Making the admission part of the
 * schema is cheaper and more reliable than asking for it in prose.
 */
export type EvidenceKind = 'computed' | 'historical' | 'model_estimate' | 'general_knowledge';

export type RiskSeverity = 'CRITICAL' | 'HIGH' | 'MEDIUM' | 'LOW';

export interface RiskFinding {
  finding: string;
  evidence: string;
  severity: RiskSeverity;
  evidence_kind: EvidenceKind;
}

export interface RiskAnalysis {
  summary: string;
  findings: RiskFinding[];
  comparison_note: string;
  /** No default: an optional disclaimer is one left out on the run it mattered. */
  disclaimer: string;
}

/** The storm a response is about, or `null` when it is not about one. */
export interface CycloneBlock {
  cyclone_id: string;
  name: string;
  season: number;
  basin: string;
  subbasin: string | null;
  peak_wind_kmph: number | null;
  waypoint_count: number;
}

export interface RiskAnalystResponse {
  cyclone_id: string;
  scenario_id: string;
  wind_kmph: number;
  imd_category: string;
  wind_is_band_midpoint: boolean;
  scenario_kind: string;
  generated_at: string;
  scenario_limitation: string;
  limitation: string;
  category: number;
  band_kmph: { lower: number; upper: number };
  cyclone: CycloneBlock | null;
  analysis: RiskAnalysis;
  /** HTTP calls actually made, including retried 503s. */
  gemini_calls: number;
  model: string;
  comparison_between: string[];
  peak_estimate: PeakEstimate;
  is_estimate: boolean;
}

// ---------------------------------------------------------------------------
// Labels
// ---------------------------------------------------------------------------

/**
 * A cyclone's name, rendered for a human.
 *
 * IBTrACS stores names uppercase and may carry a paired name separated by `:`
 * or `-` for storms named differently by two agencies (`BESS:BONNIE`,
 * `KHAI-MUK`). Both separators are treated as word boundaries, so each half is
 * title-cased on its own rather than the second half being left shouting.
 *
 * `null` or `''` renders as `Unnamed`, never `undefined` or an empty heading —
 * **482 of the 610 catalogued storms are unnamed** (measured from
 * `data/cyclones/catalogue.json`), so the absence is the common case and it
 * has to be a word.
 */
export function cycloneDisplayName(name: string | null | undefined): string {
  if (!name || !name.trim()) return 'Unnamed';
  return name
    .split(/([:\-\s])/)
    .map((part) =>
      /^[:\-\s]$/.test(part) ? part : part.charAt(0).toUpperCase() + part.slice(1).toLowerCase(),
    )
    .join('');
}

/**
 * The one-line live-state label.
 *
 * **The rule, in prose, because it is the reason this function exists:** an
 * unavailable feed must never read as an active storm. A judge who reads
 * "Live feed unavailable" should conclude that nothing is being claimed — not
 * that something is. The three states are kept apart because they tell a
 * reader opposite things: `available` means a live fix was parsed,
 * `no_active_storm` means the feed works and there is nothing in it, and
 * `live_unavailable` means no trustworthy answer arrived.
 *
 * Returns exactly one of three strings, so a caller can branch on the label
 * itself rather than re-deriving the state. The storm's name is deliberately
 * not appended here — a name is a claim, and the caller decides whether the
 * current state licenses one.
 */
export function liveStateLabel(state: {
  status: LiveStatus;
  cyclone?: LiveCycloneSummary | null;
}): string {
  if (state.status === 'available') return 'Live';
  if (state.status === 'no_active_storm') return 'No active cyclone';
  return 'Live feed unavailable';
}

/**
 * When the live state was last checked, for a screen that is about to show it.
 *
 * A failure reported without its time is a failure reported as current, so the
 * timestamp is part of the statement rather than an optional extra. When there
 * is none the note says so — "never checked" is a fact, and a blank is not.
 */
export function freshnessNote(state: {
  status: LiveStatus;
  checked_at: string | null;
}): string {
  const label = liveStateLabel(state);
  if (!state.checked_at) return `${label} — never checked`;
  return `${label} — checked ${state.checked_at}`;
}

/**
 * The label for the ML figure.
 *
 * **Never "predicted" when the gate failed.** The model lost to a flat median,
 * so the number on screen is that median and the label must say so. Calling it
 * a prediction would be the one mislabel this whole branch exists to prevent:
 * a plausible-sounding number whose only defence is a word.
 */
export function mlEstimateLabel(estimate: {
  beats_baseline: boolean;
  estimate_source?: string;
}): string {
  if (!estimate.beats_baseline) return 'median baseline (not a prediction)';
  // The gate passed, so the model's output is the figure and may be called one.
  return 'ML estimate';
}

/**
 * The deltas between consecutive comparison rows.
 *
 * **Right minus left, matching the arrow in the label.** See
 * `ComparisonDeltas` for why this is the opposite of the server's convention.
 *
 * **The label names whichever axis the two rows differ on.** A comparison can
 * run along either: two scenarios of one storm (`cat4 → cat5`) or two storms
 * at one scenario (`Remal 2024 → Dana 2023`). Labelling by scenario when the
 * scenarios are equal would print `cat5 → cat5`, which is not a comparison.
 *
 * Rounded to 4 dp, the same precision the backend rounds to: `3.41 - 1.83` is
 * `1.5800000000000003` in binary floating point, and a strict-equality test
 * against `1.58` is exactly the kind of failure that teaches a team to stop
 * trusting arithmetic.
 *
 * Fewer than two rows yields no deltas — a single row has nothing to be
 * compared against, and emitting a zero delta would present a comparison that
 * was never made.
 */
export function comparisonDeltas(
  rows: {
    cyclone_id: string;
    scenario_id: string;
    surge_m: number;
    exposed: number;
    name?: string | null;
  }[],
): { label: string; surge_delta_m: number; exposed_delta: number }[] {
  const out: { label: string; surge_delta_m: number; exposed_delta: number }[] = [];
  for (let i = 1; i < rows.length; i += 1) {
    const left = rows[i - 1];
    const right = rows[i];
    const label =
      left.scenario_id !== right.scenario_id
        ? `${left.scenario_id} → ${right.scenario_id}`
        : `${left.name ?? left.cyclone_id} → ${right.name ?? right.cyclone_id}`;
    out.push({
      label,
      surge_delta_m: round4(right.surge_m - left.surge_m),
      exposed_delta: right.exposed - left.exposed,
    });
  }
  return out;
}

/** Round to 4 dp, the backend's own precision for a delta. */
function round4(value: number): number {
  return Math.round(value * 10_000) / 10_000;
}
