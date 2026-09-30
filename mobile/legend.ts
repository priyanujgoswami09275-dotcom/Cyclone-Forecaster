/**
 * The map legend's data — five rows, kept out of the component so it can be
 * checked without a React renderer.
 *
 * **This module deliberately imports nothing.** `theme.ts` and
 * `mapStyles.ts` are imported extensionless (as every file in this app does,
 * and as Metro requires), and Node's ESM resolver will not follow that — so a
 * module that reached for either of them could not be loaded by
 * `node --test`. Keeping this file import-free is what makes the labels and
 * shapes below testable at all, and it is why the colours are *named* here
 * rather than read here.
 *
 * So each row names the constant its swatch must come from — `swatchFrom` is a
 * field name, not a value — and `MapLegend.tsx` resolves it against the real
 * `mapStyles.ts` / `theme` exports. That split is the whole point: the legend
 * still has exactly one place that reads a colour (the component, which reads
 * the same constants the `<Polyline>`s and `<Marker>`s are drawn from), and
 * `tests/legend.test.mjs` can still check that no colour was retyped.
 *
 * `allowImportingTsExtensions` would let this file import `./theme.ts`
 * directly, but that means editing `tsconfig.json` — which has uncommitted
 * changes from outside this task, and is the config the app itself builds with.
 * Not worth it for one file.
 */

/**
 * How a row's swatch is drawn. The shapes are not decoration: the dash carries
 * the meaning for cut-off roads, since `lineDashPattern` is not honoured by the
 * Android renderer and the layer draws solid there.
 */
export type LegendSwatchKind = 'flood' | 'pin' | 'dash' | 'path';

/**
 * Where a swatch's colour must be read from. These are field paths into
 * `mapStyles.ts` / `theme.ts`, written as strings so this module needs no
 * imports; `MapLegend.tsx` is the one place that resolves them.
 */
export type SwatchSource =
  | 'theme.flood'
  | 'theme.water'
  | 'mapStyles.assetPinColours.hospital'
  | 'mapStyles.assetPinColours.substation'
  | 'mapStyles.compromisedRoadStyle.strokeColor'
  | 'mapStyles.trackLineStyle.strokeColor';

export interface LegendRow {
  /** Stable key, also used as the React list key. */
  id: string;
  label: string;
  kind: LegendSwatchKind;
  /**
   * The constant this row's swatch is filled from. The flood row is the one
   * with no single colour — it is a fill *and* a stroke, so it names both and
   * `fillSource` is null.
   */
  fillSource: SwatchSource | null;
  /** Present only for the flood row. */
  strokeSource?: SwatchSource;
}

export const LEGEND_ROWS: LegendRow[] = [
  {
    id: 'flood',
    label: 'Flooded area',
    kind: 'flood',
    // `flood` is the *theme's* flood tint, and it is deliberately not the
    // raster's own blue. The committed PNGs are painted `#2563eb`
    // (`render_overlays.RGB`), and the dark design's `flood` is `#84a7d3` —
    // different values, and the swatch follows the theme because the swatch is
    // app chrome while the raster is a pre-rendered artefact the theme does not
    // control. The two are close enough in hue that the key reads correctly
    // against the layer, which is the only thing a legend has to do. Logged in
    // MEMORY.md: restyling the raster to `flood` is a Stage B question, and it
    // would change every committed PNG.
    fillSource: 'theme.flood',
    strokeSource: 'theme.water',
  },
  {
    id: 'hospital',
    label: 'Hospital',
    kind: 'pin',
    fillSource: 'mapStyles.assetPinColours.hospital',
  },
  {
    id: 'substation',
    label: 'Substation',
    kind: 'pin',
    fillSource: 'mapStyles.assetPinColours.substation',
  },
  {
    id: 'road',
    label: 'Cut-off road',
    kind: 'dash',
    fillSource: 'mapStyles.compromisedRoadStyle.strokeColor',
  },
  {
    id: 'stormpath',
    label: 'Storm path',
    kind: 'path',
    fillSource: 'mapStyles.trackLineStyle.strokeColor',
  },
];
