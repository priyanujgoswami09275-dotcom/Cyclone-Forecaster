/**
 * The Web map's basemap metadata — extent and provenance, as data.
 *
 * **Imports nothing but types**, for the same reason `mapProjection.ts` does:
 * `node --test` loads this directly, and a value import would be an
 * extensionless specifier Node's ESM resolver will not follow. The image
 * itself is required separately by `webBasemap.ts`, which is a Metro asset
 * import and would defeat that constraint.
 *
 * ## Why the Web app has a basemap and the native app does not
 *
 * The native screen draws Google's vector tiles through `react-native-maps`
 * and recolours them with `customMapStyle`. The Web build cannot: that module
 * is native, and importing it into a browser bundle throws
 * `codegenNativeComponent is not a function` on first render. There is also no
 * Google Maps key for the browser and no tile server in this project.
 *
 * So the Web map draws its own base layer from the DEM this project already
 * committed. `backend/tools/render_basemap.py` writes
 * `data/basemap/basemap.png` from the 0 m contour of `data/dem.tif` — real
 * SRTM, deterministic, offline. The same pattern as `data/overlays/`, and the
 * same guarantee: **display-only, and nothing reads a number off it.**
 */

export interface BasemapMeta {
  width: number;
  height: number;
  pngBytes: number;
  bounds: { west: number; south: number; east: number; north: number };
  crs: string;
  /** Share of the bbox at or above the 0 m contour. Orientation only. */
  landFraction: number;
  thresholdM: number;
}

/**
 * `data/basemap/basemap.json`, as written by `backend/tools/render_basemap.py`.
 *
 * Rounded to four decimals (~11 m, finer than the ~150 m the image resolves)
 * so the values can be compared by eye against `/overlays`' bounds, which
 * agree to within 5e-5 degrees. `tests/basemap.test.mjs` pins them against the
 * committed index file, so a regenerated basemap whose extent moved fails a
 * test instead of silently misplacing the water.
 */
export const BASEMAP_META: BasemapMeta = {
  width: 1000,
  height: 930,
  pngBytes: 48074,
  bounds: {
    west: 87.8,
    south: 21.3,
    east: 89.2,
    north: 22.6016,
  },
  crs: 'EPSG:4326',
  landFraction: 0.590666,
  thresholdM: 0,
};

/**
 * The basemap's disclosure, shown on screen.
 *
 * Says the three things a reader could otherwise be misled about: the
 * coastline is derived rather than surveyed, the image is coarse, and no
 * figure in the app is measured off it.
 */
export const BASEMAP_DISCLOSURE =
  'Land and water are read from the project’s committed SRTM elevation model ' +
  'at the 0 m contour — a derived coastline, not a surveyed chart — and the ' +
  'image is subsampled to roughly 150 m per pixel. No number in this app is ' +
  'measured off it.';
