/**
 * The Web map's base layer — where the DEM-derived basemap lives and how the
 * Web build ships it.
 *
 * **Imports nothing but types**, for the same reason `mapProjection.ts` does:
 * `node --test` loads this directly, and a value import would be an
 * extensionless specifier Node's ESM resolver will not follow.
 *
 * ## Why there is a basemap here at all
 *
 * The native screen draws Google's vector tiles through `react-native-maps`
 * and recolours them with `customMapStyle`. **The Web build cannot do that.**
 * `react-native-maps` is a native module: importing it into a browser bundle
 * puts `codegenNativeComponent` — a function that only exists inside a React
 * Native runtime — into the module graph, and it throws on first render. That
 * is the crash this file's existence is a response to, and it is why the Web
 * screen is a separate `MapScreen.web.tsx` rather than a shared component
 * with a platform branch inside it.
 *
 * There is also no Google Maps key for the browser and no tile server in this
 * project, so there is no basemap to recolour. The honest alternative is the
 * terrain this project already committed: `backend/tools/render_basemap.py`
 * writes `data/basemap/basemap.png` from the 0 m contour of `data/dem.tif` —
 * real `USGS/SRTMGL1_003`, already fetched, deterministic, offline. Same
 * pattern and same guarantees as `data/overlays/`.
 *
 * ## It ships as a build asset, not a backend fetch
 *
 * 48 KB, served from the same origin as the app. That removes a cross-origin
 * request, a second round trip before the map can draw, and a class of
 * "basemap 404s so the map is blank" failure from the critical path. The
 * flood rasters stay on the backend, at `/overlays`, because those are the
 * model's output rather than a fixed artefact and are already served by a
 * route that reports its own bounds.
 */

/**
 * The committed basemap, bundled as a build asset.
 *
 * Metro turns a static `require` of a `.png` into a hashed asset URL, which
 * is what makes this correct under an Expo Web export: the file is copied into
 * the build and referenced by its emitted name, so there is no runtime path to
 * get wrong and no server to depend on.
 *
 * **The path is `./assets/basemap.png`, not `../assets/`.** The earlier
 * `./../` form looked equivalent and Metro resolved it relative to `mobile/`
 * anyway — but it is one directory hop further than the file actually lives,
 * and Metro's resolver reported "None of these files exist" while the bundler
 * still exited 0. Written plainly, it fails the build loudly instead.
 *
 * Typed as a string because Metro's asset registry replaces the call with a
 * URL at build time. This is the one place in the app that requires an image.
 */
export const BASEMAP_ASSET = require('./assets/basemap.png') as string;
