/**
 * The Dynamic Cyclone System endpoints.
 *
 * **Why a separate module from `api.ts`.** `api.ts` serves the case-study
 * simulation — the surge, the exposure, the track, the advisory. These six
 * serve the cyclone *model*: the catalogue, the scenarios, the live feed, the
 * comparison and the risk analyst. Different concern, different evolution
 * rate, and the split is what lets the new endpoints be added without
 * touching a line of the simulation client.
 *
 * **What they share with `api.ts` is everything that can go subtly wrong.** The
 * `request` function, the `ApiError` taxonomy and the timeouts are imported
 * rather than reimplemented. A second copy of the timeout or the
 * `AbortError`/`TypeError` distinction is a second place for the two to
 * disagree, and only one of them would be tested.
 *
 * **Timeouts.** `READ_TIMEOUT_MS` (150 s) for all six, including the POST.
 *
 * That is more headroom than `/advisory`'s 120 s, and deliberately so: the risk
 * analyst calls `exposure()` internally, which measured **113.9 s on a cold
 * cache** (see the note on `READ_TIMEOUT_MS` in `api.ts`), and then spends up to
 * 90 s on the Gemini ladder. A 120 s bound could abort a request the backend
 * was about to answer — the exact failure that timeout was raised to prevent.
 */
import {
  READ_TIMEOUT_MS,
  request,
  type TrackResponse,
} from './api';
import type {
  ComparisonResponse,
  CyclonesResponse,
  LiveCycloneState,
  RiskAnalystRequest,
  RiskAnalystResponse,
  ScenariosResponse,
} from './cycloneModel';

/**
 * Every historical cyclone the app can address.
 *
 * Sorted case study first, then newest first, so the default is always the
 * first thing a client sees.
 */
export function getCyclones(): Promise<CyclonesResponse> {
  return request<CyclonesResponse>('/cyclones', { method: 'GET' }, READ_TIMEOUT_MS);
}

/**
 * One cyclone's track, in the same shape `/track` returns.
 *
 * Remal is served from the committed GeoJSON, which is the same payload `/track`
 * returns, so the two endpoints are literally the same code path for the case
 * study and cannot disagree.
 */
export function getCycloneTrack(cycloneId: string): Promise<TrackResponse> {
  return request<TrackResponse>(
    `/cyclones/${encodeURIComponent(cycloneId)}/track`,
    { method: 'GET' },
    READ_TIMEOUT_MS,
  );
}

/**
 * The strengths a client may ask for, scoped to one cyclone.
 *
 * Scoped because one of them is that cyclone's *own* observed intensity, which
 * is a different number for every storm.
 */
export function getScenarios(cycloneId: string): Promise<ScenariosResponse> {
  return request<ScenariosResponse>(
    `/scenarios?cyclone_id=${encodeURIComponent(cycloneId)}`,
    { method: 'GET' },
    READ_TIMEOUT_MS,
  );
}

/**
 * The live state, reported as a state rather than an error.
 *
 * **Never a 5xx for an unreachable source.** A feed being down is a fact about
 * the feed, not a fault in this service. And **never a historical stand-in**:
 * when the status is not `available` the `cyclone` field is `null`, always.
 */
export function getLiveCyclone(): Promise<LiveCycloneState> {
  return request<LiveCycloneState>('/live-cyclone', { method: 'GET' }, READ_TIMEOUT_MS);
}

/**
 * The same scenario applied to several cyclones, side by side.
 *
 * The point is the delta, and a delta is only meaningful between figures that
 * were computed the same way — so every entry is built by one helper from one
 * `(cyclone_id, scenario_id)` pair.
 */
export function getComparison(
  cycloneIds: string[],
  category: number,
): Promise<ComparisonResponse> {
  const q = `category=${category}&cyclone_ids=${cycloneIds.map(encodeURIComponent).join(',')}`;
  return request<ComparisonResponse>(`/comparison?${q}`, { method: 'GET' }, READ_TIMEOUT_MS);
}

/**
 * An evidence-grounded risk analysis, behind a deliberate press.
 *
 * The one call in this module that spends Gemini quota, so it is POST and it
 * is only ever reached from an explicit user action — the same gate
 * `postAdvisory` sits behind.
 */
export function postRiskAnalysis(body: RiskAnalystRequest): Promise<RiskAnalystResponse> {
  return request<RiskAnalystResponse>(
    '/risk-analyst',
    {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body),
    },
    READ_TIMEOUT_MS,
  );
}
