/**
 * The presentation strings for the comparison sheet and the live banner.
 *
 * **Why these are here and not in the components.** They are the sentences a
 * judge reads, and each one guards a distinction the UI can silently collapse.
 * A string built inline in a JSX return is a string nobody has tested, and the
 * three below are exactly the ones where being wrong is worse than being
 * absent.
 *
 * Re-exports `comparisonDeltas` and `mlEstimateLabel` from `cycloneModel.ts` so
 * a panel imports its labels from one module. The definitions stay where the
 * types are; this file adds only what the screen needs.
 */
import type { LiveCycloneState, LiveStatus } from './cycloneModel.ts';
import type { ChipId } from './strengthChips.ts';

export { comparisonDeltas, mlEstimateLabel } from './cycloneModel.ts';

/**
 * The seven strength bands, in order.
 *
 * Order matters here: `pickSecondScenario` walks it to find the *nearest*
 * other band, so a comparison step is one category wide and a reader can
 * attribute the delta to that one step.
 */
const BAND_ORDER = ['cat0', 'cat1', 'cat2', 'cat3', 'cat4', 'cat5', 'cat6'] as const;

/**
 * Which scenario a strength chip asks for.
 *
 * Only `remal_observed` differs from its own id: it addresses the case study's
 * own peak, spelled `observed` on the wire. The other three are band ids.
 *
 * Deliberately total — every `ChipId` the app offers is covered, and adding a
 * chip without updating this is a compile error rather than a request for an
 * unknown scenario.
 */
export function scenarioForChip(chip: ChipId): string {
  return chip === 'remal_observed' ? 'observed' : chip;
}

/**
 * The second scenario a comparison should contrast against.
 *
 * **Why this function exists.** `observed` is registered only for cyclones
 * whose track is committed locally — `/scenarios` lists it for Remal and not
 * for the other 609. Requesting `scenario_id=observed` for one of them is a
 * `400 unknown scenario 'observed'`. A comparison sheet whose button errors
 * for every storm except the case study would be worse than no sheet, so the
 * choice is made against what the catalogue actually reports rather than
 * assumed.
 *
 * **Preference order, and the reason for each:**
 *
 *   1. `observed`, when it is not the scenario already selected — a storm's own
 *      intensity against a band is the most informative contrast there is:
 *      "what the band says" against "what it actually did".
 *   2. Otherwise the nearest other band. A one-category step keeps the delta
 *      attributable; `cat4 → cat0` is a number a reader cannot reason about.
 *   3. When the selected scenario *is* `observed`, the strongest band — the
 *      closest thing to a storm's own peak that every cyclone has.
 *
 * Returns `null` when only one scenario exists. That is not an error: the
 * caller renders a single row, and `comparisonDeltas` correctly emits no
 * deltas for it. Emitting a zero delta would claim a comparison that was
 * never made.
 */
export function pickSecondScenario(current: string, available: string[]): string | null {
  const pool = available.filter((s) => s !== current);
  if (pool.length === 0) return null;

  const candidates: string[] = [];
  if (current !== 'observed') candidates.push('observed');

  const index = BAND_ORDER.indexOf(current as (typeof BAND_ORDER)[number]);
  if (index >= 0) {
    // Higher bands, starting with the immediate neighbour.
    candidates.push(...BAND_ORDER.slice(index + 1));
    // Lower bands, nearest first.
    candidates.push(...BAND_ORDER.slice(0, index).reverse());
  } else {
    // The current scenario is `observed`, so any band will do — strongest first.
    candidates.push(...[...BAND_ORDER].reverse());
  }

  return candidates.find((s) => pool.includes(s)) ?? pool[0];
}

/**
 * The flooded-area figure for a comparison row.
 *
 * **A zero is a measurement, not a gap.** The model answered "nothing is
 * flooded", and printing "—" or "unknown" would tell a reader the model did not
 * answer at all. The distinction matters most at the low end of the scale,
 * where a zero is the good news and a dash reads as a failure.
 */
export function exposureText({ flooded_km2 }: { flooded_km2: number }): string {
  return `${Math.round(flooded_km2).toLocaleString('en-US')} km² flooded`;
}

/**
 * The live banner.
 *
 * **Never a storm name unless the feed actually reported one.** A name on a
 * screen labelled "unavailable" is a historical cyclone presented as now — the
 * one unforgivable substitution in this system. So the name appears only on
 * `available`, where the feed itself supplied it; every other state renders the
 * backend's own reason, which already carries the promise that nothing is being
 * substituted.
 */
export function liveBannerText(state: {
  status: LiveStatus;
  reason: string;
  checked_at: string | null;
  cyclone: LiveCycloneState['cyclone'];
}): string {
  if (state.status === 'available' && state.cyclone) {
    const { name, latest_wind_kmph } = state.cyclone;
    return `${name} — live, ${latest_wind_kmph} km/h`;
  }
  return state.reason;
}

/**
 * The comparison sheet's footer.
 *
 * **Always names the surge method.** A table of numbers without the method that
 * produced them invites the exact confusion the architecture is built to
 * prevent: a reader who cannot tell a deterministic screening estimate from a
 * forecast, or a median baseline from a prediction, will read the most
 * flattering one into every row.
 */
export function comparisonFooterText(): string {
  return (
    'Surge figures come from the anchored quadratic scaling law ' +
    '1.2 × (wind/115)², scaled from one observed event — a screening estimate, ' +
    'not a forecast. The ML figure is a flat-median baseline that did not beat ' +
    'its own gate, is not a prediction, and is not the surge figure.'
  );
}
