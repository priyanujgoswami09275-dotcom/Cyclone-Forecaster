/**
 * The strength chips — the four scenarios the app can be asked about — and the
 * rules for what they map to and when the advisory button is enabled.
 *
 * **Nothing here is hardcoded from the API's data.** The four chip *ids* are
 * the app's own vocabulary ("remal_observed" and three category indices), but
 * every number displayed on or under a chip — wind, surge, flooded area — is
 * read from `/categories` and `/overlays` at runtime, and the bands themselves
 * are never restated here. The rule is: this module decides *which* entry a
 * chip points at; the API decides what that entry says.
 *
 * **Why four chips and not the seven-band slider.** The slider was honest and
 * unusable in a demo: five of its seven positions expose no infrastructure at
 * all, so it spent four fifths of its travel on positions that show an empty
 * map. These four are the positions where something happens — the case study
 * itself, and the three bands above it. That is a demo decision, not a claim
 * that the lower bands do not exist; `/categories` still serves all seven and
 * the backend is untouched.
 */
import type { CategoriesResponse, OverlayEntry } from './api';

/** A chip's stable key. Matches an `OverlayEntry.id` without assuming it. */
export type ChipId = 'remal_observed' | 'cat4' | 'cat5' | 'cat6';

/** The default selection: the top of the scale, and the most defensible one. */
export const DEFAULT_CHIP: ChipId = 'cat6';

export interface StrengthChip {
  id: ChipId;
  /**
   * The short label on the chip. **Not** the API's `imd_category` — those are
   * full IMD names ("Extremely Severe Cyclonic Storm") and four of them side
   * by side in a chip row would be unreadable. The full name is still shown on
   * the figures line, so the abbreviation loses nothing.
   */
  label: string;
}

/**
 * The four chips, in order. Labels are the design's, and the mapping to bands
 * is the app's — the band's real name and numbers come from the API.
 */
export const STRENGTH_CHIPS: StrengthChip[] = [
  { id: 'remal_observed', label: 'Remal' },
  { id: 'cat4', label: 'Very severe' },
  { id: 'cat5', label: 'Extreme' },
  { id: 'cat6', label: 'Super cyclonic' },
];

export interface ChipResolution {
  chip: StrengthChip;
  /** The overlay for this chip, or null if the index has not loaded. */
  overlay: OverlayEntry | null;
  /** The category index to request `/exposure` for, or null for the preset. */
  categoryIndex: number | null;
  /**
   * The IMD category name to borrow figures from, or null for the preset.
   *
   * The preset (Remal at 115 kmph) is **not** an IMD band, so every
   * API-driven figure has to borrow the nearest one. 115 kmph is 12 kmph from
   * category 3's 103 and 20 from category 4's 135, so it borrows category 3 —
   * and the figures line says so rather than implying the numbers are the
   * preset's own.
   */
  borrowedCategory: string | null;
  /** True when the borrowed band's name is not what the chip is labelled. */
  borrowed: boolean;
}

/**
 * Resolve one chip against the live payloads.
 *
 * `categories` may be an empty array while `/categories` is still in flight;
 * every field that depends on it comes back null rather than throwing, so the
 * panel can render a loading state instead of crashing on a chip press.
 */
export function resolveChip(
  id: ChipId,
  categories: CategoriesResponse['categories'],
  overlays: OverlayEntry[],
): ChipResolution {
  const chip = STRENGTH_CHIPS.find((c) => c.id === id) ?? STRENGTH_CHIPS[0];

  // The preset is a named scenario, not a band, so it resolves to the overlay
  // directly and leaves the category to `nearestCategory` at the call site.
  if (chip.id === 'remal_observed') {
    return {
      chip,
      overlay: overlays.find((o) => o.id === 'remal_observed') ?? null,
      categoryIndex: null,
      borrowedCategory: null,
      borrowed: true,
    };
  }

  const index = Number(chip.id.replace('cat', ''));
  const category = categories[index] ?? null;
  return {
    chip,
    overlay: overlays.find((o) => o.id === chip.id) ?? null,
    categoryIndex: category ? index : null,
    borrowedCategory: category?.imd_category ?? null,
    borrowed: false,
  };
}

/**
 * The one-line figures under the chip row.
 *
 * **"≥" appears exactly when the wind is a band floor rather than a
 * midpoint.** IMD documents Super Cyclonic Storm as ≥222 kmph with no upper
 * bound, so the backend reports `wind_is_band_midpoint: false` and 222 is the
 * *lowest* wind in that band, not a representative one. Printing "222 km/h"
 * beside a chip labelled "Super cyclonic" would state as a measurement the
 * bottom of an open-ended range, which is the same class of error as the knots
 * bug in flag 31. When the flag is true the number is a midpoint and reads
 * without a prefix.
 *
 * The flooded area is the model's own `final_land_area_km2` and is **never**
 * measured off the picture — at category 6 the raster shows roughly 1,680 km²
 * and the model says 2,680, because the raster is downsampled to ~150 m and
 * quantised into four depth classes. "(model)" is in the string so the reader
 * knows which of the two numbers they are looking at.
 */
export function figuresLine(
  overlay: OverlayEntry | null,
  windIsBandMidpoint: boolean | undefined,
): string {
  if (!overlay) return '';
  const wind = `${windIsBandMidpoint ? '' : '≥'}${overlay.wind_kmph.toFixed(0)} km/h wind`;
  const surge = `${overlay.surge_m.toFixed(1)} m surge`;
  const area = `${Math.round(overlay.final_land_area_km2).toLocaleString('en-US')} km² flooded (model)`;
  return `${wind}, ${surge}, ${area}`;
}

/**
 * Whether "Generate advisory" should be enabled.
 *
 * **Empty exposure is the only thing that disables it.** There is nothing to
 * evacuate when no hospital, substation or road is in the water, and offering
 * to write an evacuation plan for a scenario with no exposure would produce
 * prose about an empty map.
 *
 * `loading` also disables it, and for a different reason: pressing while the
 * exposure request is in flight would generate an advisory for the *previous*
 * chip's numbers, which is the same class of error the stale-advisory notice
 * exists to catch — better to make it impossible than to warn about it
 * afterwards.
 */
export function advisoryEnabled(exposedCount: number, loading: boolean): boolean {
  if (loading) return false;
  return exposedCount > 0;
}
