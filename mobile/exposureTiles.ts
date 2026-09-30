/**
 * The three exposure tiles' numbers and their empty state.
 *
 * **Imports nothing but types**, for the same reason `legend.ts` does: this
 * module is loaded by `node --test`, and a value import of `theme` or
 * `mapStyles` would be an extensionless specifier Node's ESM resolver refuses
 * to follow. Nothing here needs a colour — the tiles are labelled in the
 * component — so the split costs nothing.
 *
 * The empty-state sentence is specified product copy, and it is kept here
 * rather than in JSX so a change to it is a change a test can see. It is also
 * the string that has to be true in both of the two ways it fires, which is
 * why it does not claim there is no water.
 */

/** One tile's headline number, as shown. */
export type TileCount = number | null;

export type TileId = 'hospitals' | 'substations' | 'roads';

export interface Tile {
  id: TileId;
  label: string;
  /**
   * The unit line under the number — "submerged", "in the water" — or null
   * when there is nothing there to have a unit. Kept per-tile because the
   * three assets are not the same kind of thing: a road is *cut off* by water
   * it does not flood, which is why its caption differs.
   */
  unit: string;
  count: TileCount;
}

export interface TileInput {
  hospitals: TileCount;
  substations: TileCount;
  roads: TileCount;
}

export function buildTiles(input: TileInput): Tile[] {
  return [
    { id: 'hospitals', label: 'Hospitals', unit: 'submerged', count: input.hospitals },
    { id: 'substations', label: 'Substations', unit: 'submerged', count: input.substations },
    { id: 'roads', label: 'Roads cut', unit: 'cut off by water', count: input.roads },
  ];
}

/**
 * The exact empty-state line, verbatim from the brief.
 *
 * "Modelled" and "at this strength" are both load-bearing. *Modelled* because
 * the app did not observe anything — it ran a model over a DEM, and a real
 * cyclone at this strength might flood assets the model missed. *At this
 * strength* because the reader's next move is to pick another chip, and the
 * line has to invite that rather than read as a dead end.
 */
export const EMPTY_EXPOSURE_LINE = 'No modelled exposure at this strength';

/**
 * The sum of the three counts, treating "not loaded yet" as zero.
 *
 * Null is collapsed to 0 rather than propagated, because the caller wants a
 * number for the disabled rule (`advisoryEnabled`) and a count that has not
 * arrived must not enable the button. `advisoryEnabled` separately takes the
 * loading flag, so a null-collapsed 0 cannot enable it during a fetch.
 */
export function totalExposed(input: TileInput): number {
  return (input.hospitals ?? 0) + (input.substations ?? 0) + (input.roads ?? 0);
}
