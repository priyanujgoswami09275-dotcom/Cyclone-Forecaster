/**
 * Boot must never wait on the live feed.
 *
 * The feed is a network probe against an external provider; when it times out
 * or rejects it would otherwise kill the whole boot — the map stayed on the
 * loading spinner (or the boot error screen) for reasons that have nothing to
 * do with whether we can draw data. So boot resolves from the four *local*
 * catalogue requests and the live cyclone is resolved on its own thread: on a
 * rejection or timeout it becomes the truthful `live_unavailable` state, never
 * the boot error screen.
 */

import { ApiError } from './api.ts';
import type {
  CategoriesResponse,
  LocalitiesResponse,
  OverlayEntry,
} from './api.ts';
import type { Cyclone, CyclonesResponse, LiveCycloneState } from './cycloneModel.ts';
import type { OverlaysResponse } from './api.ts';

export interface BootDeps {
  getCategories: () => Promise<CategoriesResponse>;
  getOverlays: () => Promise<OverlaysResponse>;
  getLocalities: () => Promise<LocalitiesResponse>;
  getCyclones: () => Promise<CyclonesResponse>;
  getLiveCyclone: () => Promise<LiveCycloneState>;
}

export type BootOutcome =
  | {
      kind: 'ready';
      categories: CategoriesResponse;
      overlayIndex: OverlayEntry[];
      localities: LocalitiesResponse;
      cyclones: Cyclone[];
      defaultCycloneId: string;
      /** Resolved independently of the four boot getters, so boot can never
       * block on it. Always settles — never rejects. */
      livePromise: Promise<LiveCycloneState>;
    }
  | { kind: 'error'; error: ApiError };

/** The live probe UI can honestly represent when the request itself failed. */
export function liveUnavailableFromError(error: unknown): LiveCycloneState {
  const detail =
    error instanceof ApiError
      ? error.message
      : error instanceof Error
        ? error.message
        : String(error);
  const checkedAt = new Date().toISOString().replace(/\.\d+Z$/, 'Z');
  return {
    status: 'live_unavailable',
    source: '/live-cyclone',
    http_status: null,
    reason:
      'No live cyclone feed could be reached, so nothing is being shown in its ' +
      'place and no historical cyclone is substituted for the live feed. ' +
      `The request that found out failed: ${detail}`,
    checked_at: checkedAt,
    endpoints: [],
    cyclone: null,
    generated_at: checkedAt,
    limitation: 'The live feed was not reached; nothing is shown in its place.',
  };
}

/** The four local catalogue requests, then the map is ready — live is branched. */
export async function bootMap(deps: BootDeps): Promise<BootOutcome> {
  try {
    const [categories, overlays, localities, cycloneList] = await Promise.all([
      deps.getCategories(),
      deps.getOverlays(),
      deps.getLocalities(),
      deps.getCyclones(),
    ]);

    // Never make the map wait on the live feed. It resolves off its own thread
    // into its truthful state; a rejection or timeout never touches boot.
    const livePromise = deps
      .getLiveCyclone()
      .then(
        (state) => state,
        (error) => liveUnavailableFromError(error),
      );

    return {
      kind: 'ready',
      categories,
      overlayIndex: overlays.overlays,
      localities,
      cyclones: cycloneList.cyclones,
      defaultCycloneId: cycloneList.default_cyclone_id,
      livePromise,
    };
  } catch (error) {
    return { kind: 'error', error: error as ApiError };
  }
}
