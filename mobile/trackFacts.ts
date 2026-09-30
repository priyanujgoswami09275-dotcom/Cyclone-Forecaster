/**
 * Facts about the committed track that the app states in prose.
 *
 * **Imports nothing but types**, so `node --test` can load it — the same
 * constraint `legend.ts` and `strengthChips.ts` work under, and for the same
 * reason: a value import of `theme`/`mapStyles`/`api` would be an extensionless
 * specifier Node's ESM resolver will not follow.
 *
 * The one non-obvious thing here is `peakReportedWindKmph`, and it exists as a
 * function rather than a constant because of a specific documented hazard in
 * the source data. See its own comment.
 */

/** The subset of a waypoint this module reads. */
export interface WindFix {
  wind_kmph: number | null;
  wind_reported: boolean;
}

export const KNOTS_TO_KMPH = 1.852;

/**
 * The strongest wind the track actually reports, in km/h — or null if none is.
 *
 * **The hazard this guards is the one the backend's own disclosure names**: the
 * IBTrACS source file writes a blank `USA_WIND` as `0.0`, so "not reported" and
 * "calm" arrive looking identical unless something separates them. The
 * endpoint already does that work and hands over `wind_reported: false` with a
 * null wind; this function's job is to not undo it.
 *
 * So it filters on `wind_reported`, and *not* on the wind being non-zero. That
 * is deliberate and it is the one place a zero is meaningful: a best-track
 * agency genuinely does report 0 kt for a dissipated system, and dropping
 * those would understate a track that weakens to nothing. The distinction that
 * matters is whether a number was *reported*, not whether it is large.
 *
 * Returns null rather than 0 for a track with nothing reported, because the
 * About sheet's sentence branches on exactly this: "No wind is reported in this
 * track" is true, and "its strongest fix is 0 km/h" is a claim about a
 * measurement that does not exist.
 */
export function peakReportedWindKmph(fixes: WindFix[]): number | null {
  let peak: number | null = null;
  for (const fix of fixes) {
    if (!fix.wind_reported) continue;
    if (fix.wind_kmph === null || !Number.isFinite(fix.wind_kmph)) continue;
    if (peak === null || fix.wind_kmph > peak) peak = fix.wind_kmph;
  }
  return peak;
}

/**
 * How many fixes report no wind.
 *
 * Shown in the About sheet because a track with unreported fixes is a track
 * with a gap in it, and a reader told "19 fixes" would otherwise assume 19
 * measurements.
 */
export function countUnreported(fixes: WindFix[]): number {
  return fixes.filter((f) => !f.wind_reported).length;
}

/**
 * Whether the track's peak is below IMD's published landfall wind for Remal.
 *
 * **This is a comparability check, not a contradiction check**, and it exists
 * so the app can say *why* the two numbers differ rather than leaving a reader
 * to conclude the model is more extreme than anything observed. The track's
 * wind is JTWC's 1-minute mean; IMD's 110–120 km/h is a 3-minute mean. A
 * 1-minute mean is normally the *higher* of the two, so a lower track peak is
 * not evidence that the observation understated the storm — it is evidence
 * that two agencies averaged over different windows.
 *
 * Returned as a boolean rather than rendered here so the wording lives with the
 * rest of the copy in `AboutSheet.tsx`.
 */
export function windNeedsUnitCaveat(peakKmph: number | null, imdKmph: number): boolean {
  if (peakKmph === null) return false;
  return peakKmph < imdKmph;
}
