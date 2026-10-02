/**
 * The Web bundle must never contain native map runtime code.
 *
 * `react-native-maps` is a **native module**. Its components resolve to
 * `codegenNativeComponent`, a function that exists only inside a React Native
 * runtime; in a browser bundle it is undefined and throws on first render.
 * The previous Web build died exactly that way — `codegenNativeComponent is not
 * a function` — and the page was blank.
 *
 * Two independent guards, because either alone is insufficient:
 *
 *   1. **Source-level**: the Web module graph names neither
 *      `react-native-maps` nor `mapStyles`. Checked as *text*, which catches
 *      an import that a bundler would happily resolve.
 *   2. **Build-level**: the exported Web bundle contains none of the native
 *      symbols. Checked against the real artefact, because a source check
 *      cannot see what a transitive dependency drags in.
 *
 * The build-level half runs against `dist/` when a build exists and is skipped
 * (loudly, with a reason) when it does not — so `node --test` stays green on a
 * fresh checkout without pretending to have verified anything.
 */

import assert from 'node:assert/strict';
import { existsSync, readFileSync, readdirSync } from 'node:fs';
import { dirname, join, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';
import { describe, it } from 'node:test';

const HERE = dirname(fileURLToPath(import.meta.url));
const MOBILE = resolve(HERE, '..');

/** Files that make up the Web module graph. */
const WEB_GRAPH = [
  'App.tsx',
  'api.ts',
  'apiCyclones.ts',
  'advisoryFlow.ts',
  'basemap.ts',
  'cycloneModel.ts',
  'exposureTiles.ts',
  'legend.ts',
  'mapProjection.ts',
  'sampleAdvisory.ts',
  'scenarioCompare.ts',
  'strengthChips.ts',
  'theme.ts',
  'trackFacts.ts',
  'webBasemap.ts',
  'webViewModel.ts',
  'components/MapScreen.web.tsx',
  'components/WebImpactMap.tsx',
  'components/AdvisoryPanel.tsx',
  'components/CyclonePicker.tsx',
  'components/LocalitySearch.tsx',
  'components/RiskAnalystPanel.tsx',
  'components/ScenarioComparePanel.tsx',
];

/**
 * Files that are **native-only** and must never enter the Web bundle.
 *
 * `LeafletMap` pulls in `react-native-webview`, whose native counterpart is
 * another `codegenNativeComponent` — the same class of failure that killed the
 * first Web build. `geo.ts` and `leafletHtml.ts` are Leaflet-specific and have
 * no business in a browser build that deliberately renders its own SVG.
 */
const NATIVE_ONLY = [
  'geo.ts',
  'leafletHtml.ts',
  'components/LeafletMap.tsx',
];

describe('The Web module graph names no native map module', () => {
  for (const relative of WEB_GRAPH) {
    it(`${relative} does not import react-native-maps or mapStyles`, () => {
      const source = readFileSync(join(MOBILE, relative), 'utf8');
      // Match the module *specifier*, not the word: `mapStyles` appears in
      // prose throughout (Design.md references, "matching mapStyles.ts") and
      // only an import of it is the hazard.
      assert.doesNotMatch(
        source,
        /(?:from|import|require)\s*\(?\s*['"][^'"]*react-native-maps['"]/,
        `${relative} imports react-native-maps — it must never enter the Web bundle`,
      );
      assert.doesNotMatch(
        source,
        /(?:from|import|require)\s*\(?\s*['"][^'"]*mapStyles['"]/,
        `${relative} imports mapStyles — it is native-adjacent and Web must stay clear`,
      );
    });
  }
});

describe('The native screen is still the native screen', () => {
  const source = readFileSync(join(MOBILE, 'components/MapScreen.tsx'), 'utf8');

  it('MapScreen.tsx no longer imports react-native-maps', () => {
    // **This assertion was inverted on 2026-10-01 and the reason matters.**
    //
    // The native map rendered solid black on Android in Expo Go:
    // `react-native-maps` is Google Maps there, which needs an API key, and
    // Expo Go cannot carry a project's own key. It was replaced with Leaflet in
    // a `react-native-webview`, which needs no key at all.
    //
    // The old test pinned `from 'react-native-maps'` in order to catch the Web
    // split "fixing" the crash by breaking the native app. Keeping that pin
    // would have made the black map unfixable; the real invariant is weaker and
    // more useful — the native screen must render *a* map, and it must not
    // reach for a key-requiring provider.
    assert.doesNotMatch(
      source,
      /(?:from|import|require)\s*\(?\s*['"][^'"]*react-native-maps['"]/,
      'MapScreen.tsx still imports react-native-maps',
    );
  });

  it('MapScreen.tsx renders LeafletMap', () => {
    assert.match(source, /from\s*'\.\/LeafletMap'/, 'the native screen must render LeafletMap');
    assert.match(source, /<LeafletMap/, 'and it must actually be in the tree');
  });

  it('MapScreen.tsx keeps every behaviour the old native map had', () => {
    // The replacement must not quietly drop a feature. Each of these was a
    // capability of the `<MapView>` version, so each is pinned.
    for (const [what, pattern] of [
      ['the flood overlay', /leafletOverlay/],
      ['the storm track', /leafletTrack/],
      ['the cut-off roads', /leafletRoads/],
      ['the exposed assets', /leafletAssets/],
      ['fit-to-track', /fitRequest/],
      ['recentre', /recentreRequest/],
    ]) {
      assert.match(source, pattern, `MapScreen.tsx lost ${what}`);
    }
  });

  it('no native-only module imports react-native-maps', () => {
    for (const relative of NATIVE_ONLY) {
      const text = readFileSync(join(MOBILE, relative), 'utf8');
      assert.doesNotMatch(
        text,
        /(?:from|import|require)\s*\(?\s*['"][^'"]*react-native-maps['"]/,
        `${relative} imports react-native-maps`,
      );
    }
  });

  it('react-native-webview is a declared dependency, not a phantom', () => {
    // A `WebView` import with nothing in package.json resolves on this machine
    // and fails on a clean checkout, which is the worst way for it to fail.
    const pkg = JSON.parse(readFileSync(join(MOBILE, 'package.json'), 'utf8'));
    assert.ok(
      pkg.dependencies['react-native-webview'],
      'react-native-webview is missing from dependencies',
    );
    const version = pkg.dependencies['react-native-webview'];
    const installed = JSON.parse(
      readFileSync(join(MOBILE, 'node_modules/react-native-webview/package.json'), 'utf8'),
    ).version;
    assert.equal(version, installed, 'the declared and installed versions disagree');
  });
});

describe('The exported Web bundle carries no native map runtime', () => {
  const JS_DIR = join(MOBILE, 'dist/_expo/static/js/web');

  /**
   * The one JS file Expo emitted, or null when there is no build.
   *
   * Expo names it `index-<content hash>.js`, so the filename cannot be
   * hardcoded — it is discovered, and the *content* is what is asserted on.
   */
  const bundlePath = existsSync(JS_DIR)
    ? (() => {
        const name = readdirSync(JS_DIR).find((entry) => entry.endsWith('.js'));
        return name === undefined ? null : join(JS_DIR, name);
      })()
    : null;
  const bundle = bundlePath === null ? null : readFileSync(bundlePath, 'utf8');

  if (bundle === null) {
    it('SKIPPED — no dist/ build present', () => {
      // Deliberately visible. A silent skip here would let a broken bundle
      // pass a test named "the bundle is clean".
      assert.ok(true);
    });
    return;
  }

  for (const symbol of [
    'codegenNativeComponent',
    'RNMapView',
    'RNMaps',
    'AIRMap',
  ]) {
    it(`the bundle does not reference ${symbol}`, () => {
      assert.ok(
        !bundle.includes(symbol),
        `the Web bundle references ${symbol} — a native map module reached the browser build`,
      );
    });
  }

  it('the bundle reads the backend origin from EXPO_PUBLIC_API_URL, not a literal', () => {
    // The invariant is about the *source*, not the bundle. The previous Web
    // build hardcoded the origin as an `||` fallback inside the component, so
    // the deployed app talked to one specific host even with the env var
    // unset — a misconfigured deploy silently worked, which is worse than
    // failing loudly. `api.ts` throws a `config` ApiError instead.
    //
    // The bundle *is* expected to contain the origin: Expo inlines
    // `EXPO_PUBLIC_*` at build time, so its presence proves the env var was
    // set at build rather than hardcoded in source. What must not appear is a
    // second, independent literal.
    const sources = WEB_GRAPH.map((relative) => readFileSync(join(MOBILE, relative), 'utf8'));
    const withLiteral = WEB_GRAPH.filter((_, index) =>
      /cyclone-forecaster-chi\.vercel\.app/.test(sources[index]),
    );
    assert.deepEqual(
      withLiteral,
      [],
      `these files hardcode the backend origin instead of reading EXPO_PUBLIC_API_URL: ${withLiteral.join(', ')}`,
    );

    // And the deployed value really is compiled in, which is what proves the
    // Vercel env var reached the build.
    assert.ok(
      bundle.includes('cyclone-forecaster-chi.vercel.app'),
      'the bundle does not contain the configured backend origin — the Vercel ' +
        'env var did not reach this build',
    );
  });

  it('the bundle contains no localhost or LAN address', () => {
    // A LAN IP or localhost compiled into a production bundle is the failure
    // this app documents at length: on a judge's laptop neither resolves to
    // anything, and the failure looks like a dead backend.
    assert.ok(!/localhost/.test(bundle), 'the bundle references localhost');
    assert.ok(
      !/192\.168\.\d+\.\d+/.test(bundle),
      'the bundle references a LAN address',
    );
    assert.ok(!/127\.0\.0\.1/.test(bundle), 'the bundle references 127.0.0.1');
  });
});
